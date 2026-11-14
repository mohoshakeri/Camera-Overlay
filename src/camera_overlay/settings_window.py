import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import gi
from PIL import Image, ImageOps

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from camera_overlay.capture import build_ffmpeg_command, ensure_ffmpeg, read_latest_frame
from camera_overlay.config import OverlayConfig
from camera_overlay.constants import (
    APP_DISPLAY_NAME,
    APP_WM_CLASS,
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    PREVIEW_SIZE,
    RESOLUTIONS,
    SETTINGS_APP_ID,
    WARMTH_MAX,
    WARMTH_MIN,
    ZOOM_MAX,
    ZOOM_MIN,
)
from camera_overlay.effects import apply_frame_effects
from camera_overlay.paths import project_root, src_dir


class PreviewCapture(threading.Thread):
    """Capture Preview Frames With The Current Settings."""

    def __init__(
        self,
        get_config: Callable[[], OverlayConfig],
        on_frame: Callable[[Image.Image], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Initialize Preview Capture Thread."""
        super().__init__(daemon=True)
        self._get_config = get_config
        self._on_frame = on_frame
        self._on_error = on_error
        self._running = False
        self._process: Optional[subprocess.Popen] = None
        self._restart = threading.Event()

    def stop(self) -> None:
        """Request Capture Shutdown."""
        self._running = False
        self._restart.set()
        self._shutdown_process()

    def request_restart(self) -> None:
        """Restart Capture After Resolution Or Device Changes."""
        self._restart.set()
        self._shutdown_process()

    def run(self) -> None:
        """Read Camera Frames Until Stopped."""
        self._running = True
        while self._running:
            self._restart.clear()
            config = self._get_config()
            width = config.capture_width
            height = config.capture_height
            frame_bytes = width * height * 3
            command = build_ffmpeg_command(config.camera_device, width, height)
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=frame_bytes,
                )
            except FileNotFoundError:
                self._emit_error("FFmpeg is not installed.")
                return
            except OSError as error:
                self._emit_error("Could not start camera preview: {}".format(error))
                return

            stdout = self._process.stdout
            if stdout is None:
                self._emit_error("Could not open camera preview stream.")
                self._shutdown_process()
                return

            while self._running and not self._restart.is_set():
                raw = read_latest_frame(stdout, frame_bytes)
                if raw is None:
                    if self._running and not self._restart.is_set():
                        self._emit_error(
                            "Could not read camera frames from {}.".format(
                                config.camera_device
                            )
                        )
                    break

                try:
                    latest = self._get_config()
                    image = Image.frombytes("RGB", (width, height), raw)
                    image = ImageOps.mirror(image)
                    image = self._apply_zoom(image, latest.initial_zoom)
                    image = image.resize(
                        (PREVIEW_SIZE, PREVIEW_SIZE),
                        Image.Resampling.BILINEAR,
                    )
                    image = apply_frame_effects(image, latest)
                    self._emit_frame(image)
                except Exception:
                    # Keep Preview Alive If One Frame Fails Mid-Drag
                    continue

            self._shutdown_process()
            if self._running and self._restart.is_set():
                time.sleep(0.15)

    def _apply_zoom(self, image: Image.Image, zoom: float) -> Image.Image:
        """Crop Frame Center Using The Selected Zoom."""
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        width, height = image.size
        side = min(width, height) / zoom
        left = (width - side) / 2
        top = (height - side) / 2
        return image.crop((left, top, left + side, top + side))

    def _emit_frame(self, image: Image.Image) -> None:
        """Marshal A Frame Onto The GTK Main Loop."""
        GLib.idle_add(self._on_frame, image.copy(), priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _emit_error(self, message: str) -> None:
        """Marshal An Error Onto The GTK Main Loop."""
        GLib.idle_add(self._on_error, message, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _shutdown_process(self) -> None:
        """Terminate The FFmpeg Process If Still Running."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
        self._process = None


class SettingsWindow(Adw.ApplicationWindow):
    """Native GNOME Settings Window For Overlay Options."""

    def __init__(self, application: Adw.Application) -> None:
        """Build Adwaita Preferences Content."""
        super().__init__(application=application, title=APP_DISPLAY_NAME)
        self.set_default_size(560, 760)
        self.set_resizable(True)
        self.set_name(APP_WM_CLASS)
        self._overlay_process: Optional[subprocess.Popen] = None
        self._config_path: Optional[Path] = None
        self._preview: Optional[PreviewCapture] = None
        self._preview_pixbuf = None
        self._updating_labels = False
        self._config_lock = threading.Lock()
        self._cached_config = OverlayConfig()
        self._active_resolution_index = 0
        self._resolution_restart_id = 0

        Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=APP_DISPLAY_NAME))
        toolbar.add_top_bar(header)

        self._run_button = Gtk.Button(label="Run")
        self._run_button.add_css_class("suggested-action")
        self._run_button.add_css_class("pill")
        self._run_button.connect("clicked", self._on_run_clicked)
        header.pack_start(self._run_button)

        close_button = Gtk.Button(label="Close")
        close_button.add_css_class("flat")
        close_button.connect("clicked", lambda *_: self.close())
        header.pack_end(close_button)

        page = Adw.PreferencesPage()
        page.set_title("Settings")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(page)
        toolbar.set_content(scrolled)

        preview_group = Adw.PreferencesGroup()
        preview_group.set_title("Live Preview")
        preview_group.set_description(
            "Watch the camera with your current settings before launching the overlay."
        )
        page.add(preview_group)

        preview_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        preview_box.set_halign(Gtk.Align.CENTER)

        self._preview_picture = Gtk.Picture()
        self._preview_picture.set_size_request(PREVIEW_SIZE, PREVIEW_SIZE)
        self._preview_picture.set_content_fit(Gtk.ContentFit.COVER)
        self._preview_picture.add_css_class("camera-preview")
        preview_box.append(self._preview_picture)

        self._preview_status = Gtk.Label(label="Starting camera preview...")
        self._preview_status.add_css_class("dim-label")
        self._preview_status.set_wrap(True)
        self._preview_status.set_justify(Gtk.Justification.CENTER)
        preview_box.append(self._preview_status)
        preview_group.add(preview_box)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .camera-preview {
              border-radius: 9999px;
              background-color: #1c1c20;
              outline: 2px solid alpha(@window_fg_color, 0.18);
            }
            scale {
              min-width: 180px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        image_group = Adw.PreferencesGroup()
        image_group.set_title("Image")
        page.add(image_group)

        self._resolution_label = Gtk.Label(label=RESOLUTIONS[0][0])
        self._resolution_label.add_css_class("dim-label")
        self._resolution_scale = self._add_slider_row(
            image_group,
            title="Resolution",
            subtitle="Drag to choose capture quality",
            adjustment=Gtk.Adjustment(
                value=0,
                lower=0,
                upper=len(RESOLUTIONS) - 1,
                step_increment=1,
                page_increment=1,
            ),
            digits=0,
            value_widget=self._resolution_label,
            on_changed=self._on_resolution_changed,
        )
        self._resolution_scale.set_round_digits(0)

        self._zoom_label = Gtk.Label(label="Normal")
        self._zoom_label.add_css_class("dim-label")
        self._zoom_scale = self._add_slider_row(
            image_group,
            title="Initial Zoom",
            subtitle="Drag to zoom the preview",
            adjustment=Gtk.Adjustment(
                value=1.0,
                lower=ZOOM_MIN,
                upper=ZOOM_MAX,
                step_increment=0.05,
                page_increment=0.25,
            ),
            digits=2,
            value_widget=self._zoom_label,
            on_changed=self._on_zoom_changed,
        )

        light_group = Adw.PreferencesGroup()
        light_group.set_title("Lighting")
        page.add(light_group)

        self._brightness_label = Gtk.Label(label="Normal")
        self._brightness_label.add_css_class("dim-label")
        self._brightness_scale = self._add_slider_row(
            light_group,
            title="Brightness",
            subtitle="Drag darker or brighter",
            adjustment=Gtk.Adjustment(
                value=1.0,
                lower=BRIGHTNESS_MIN,
                upper=BRIGHTNESS_MAX,
                step_increment=0.05,
                page_increment=0.2,
            ),
            digits=2,
            value_widget=self._brightness_label,
            on_changed=self._on_brightness_changed,
        )

        self._warmth_label = Gtk.Label(label="Neutral")
        self._warmth_label.add_css_class("dim-label")
        self._warmth_scale = self._add_slider_row(
            light_group,
            title="Light / Warmth",
            subtitle="Drag toward cool or warm light",
            adjustment=Gtk.Adjustment(
                value=0.0,
                lower=WARMTH_MIN,
                upper=WARMTH_MAX,
                step_increment=0.05,
                page_increment=0.2,
            ),
            digits=2,
            value_widget=self._warmth_label,
            on_changed=self._on_warmth_changed,
        )

        background_group = Adw.PreferencesGroup()
        background_group.set_title("Background")
        page.add(background_group)

        self._blur_background = Adw.SwitchRow()
        self._blur_background.set_title("Blur Background")
        self._blur_background.set_subtitle(
            "Soften the outer area to keep focus on the center"
        )
        self._blur_background.set_active(False)
        background_group.add(self._blur_background)

        help_group = Adw.PreferencesGroup()
        help_group.set_title("Tips")
        help_group.set_description(
            "Drag inside the circle to move it. Drag near the rim to resize. "
            "Scroll to zoom. Use the floating close button to quit the overlay."
        )
        page.add(help_group)

        self._blur_background.connect("notify::active", self._on_blur_changed)
        self._refresh_slider_labels()
        self._sync_cached_config()
        self.connect("map", self._on_map)
        self.connect("close-request", self._on_close_request)

    def _add_slider_row(
        self,
        group: Adw.PreferencesGroup,
        title: str,
        subtitle: str,
        adjustment: Gtk.Adjustment,
        digits: int,
        value_widget: Gtk.Label,
        on_changed: Callable,
    ) -> Gtk.Scale:
        """Create An Action Row With A Scrollable Slider And No Numeric Entry."""
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)

        scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=adjustment,
        )
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_size_request(190, -1)
        if digits == 0:
            scale.set_round_digits(0)
        scale.connect("value-changed", on_changed)

        value_widget.set_valign(Gtk.Align.CENTER)
        value_widget.set_width_chars(8)
        value_widget.set_xalign(1.0)

        row.add_suffix(scale)
        row.add_suffix(value_widget)
        row.set_activatable(False)
        group.add(row)
        return scale

    def _refresh_slider_labels(self) -> None:
        """Update Qualitative Labels For All Sliders."""
        self._updating_labels = True
        self._on_resolution_changed(self._resolution_scale)
        self._on_zoom_changed(self._zoom_scale)
        self._on_brightness_changed(self._brightness_scale)
        self._on_warmth_changed(self._warmth_scale)
        self._updating_labels = False

    def _build_config(self) -> OverlayConfig:
        """Collect Slider Values Into Overlay Config On The UI Thread."""
        resolution_index = int(round(self._resolution_scale.get_value()))
        resolution_index = max(0, min(len(RESOLUTIONS) - 1, resolution_index))
        _, width, height = RESOLUTIONS[resolution_index]
        return OverlayConfig(
            capture_width=int(width),
            capture_height=int(height),
            brightness=float(self._brightness_scale.get_value()),
            warmth=float(self._warmth_scale.get_value()),
            initial_zoom=float(self._zoom_scale.get_value()),
            blur_background=bool(self._blur_background.get_active()),
        )

    def _sync_cached_config(self) -> None:
        """Publish A Thread-Safe Snapshot Of The Current Settings."""
        config = self._build_config()
        with self._config_lock:
            self._cached_config = config

    def _get_cached_config(self) -> OverlayConfig:
        """Read The Latest Settings Snapshot From Any Thread."""
        with self._config_lock:
            return self._cached_config

    def _on_map(self, *_args) -> None:
        """Start Preview When The Window Becomes Visible."""
        if self._overlay_process is not None and self._overlay_process.poll() is None:
            return
        self._sync_cached_config()
        self._start_preview()

    def _on_resolution_changed(self, scale: Gtk.Scale) -> None:
        """Update Resolution Label And Debounce Camera Restart."""
        index = int(round(scale.get_value()))
        index = max(0, min(len(RESOLUTIONS) - 1, index))
        self._resolution_label.set_label(RESOLUTIONS[index][0])
        self._sync_cached_config()
        if self._updating_labels:
            self._active_resolution_index = index
            return
        if index == self._active_resolution_index:
            return
        if self._resolution_restart_id:
            GLib.source_remove(self._resolution_restart_id)
        self._preview_status.set_label("Switching resolution...")
        self._resolution_restart_id = GLib.timeout_add(
            280,
            self._apply_resolution_restart,
        )

    def _apply_resolution_restart(self) -> bool:
        """Restart Preview Once After The Resolution Slider Settles."""
        self._resolution_restart_id = 0
        index = int(round(self._resolution_scale.get_value()))
        index = max(0, min(len(RESOLUTIONS) - 1, index))
        if abs(self._resolution_scale.get_value() - index) > 0.001:
            self._resolution_scale.set_value(index)
        self._resolution_label.set_label(RESOLUTIONS[index][0])
        self._sync_cached_config()
        if index == self._active_resolution_index:
            self._preview_status.set_label("Live preview")
            return False
        self._active_resolution_index = index
        if self._preview is not None and self._preview.is_alive():
            self._preview.request_restart()
        return False

    def _on_zoom_changed(self, scale: Gtk.Scale) -> None:
        """Update Zoom Label From Slider Position."""
        value = scale.get_value()
        if value < 1.15:
            label = "Normal"
        elif value < 2.0:
            label = "Closer"
        elif value < 3.0:
            label = "Close"
        else:
            label = "Very Close"
        self._zoom_label.set_label(label)
        if not self._updating_labels:
            self._sync_cached_config()

    def _on_brightness_changed(self, scale: Gtk.Scale) -> None:
        """Update Brightness Label From Slider Position."""
        value = scale.get_value()
        if value < 0.75:
            label = "Dark"
        elif value < 0.95:
            label = "Dim"
        elif value < 1.1:
            label = "Normal"
        elif value < 1.35:
            label = "Bright"
        else:
            label = "Very Bright"
        self._brightness_label.set_label(label)
        if not self._updating_labels:
            self._sync_cached_config()

    def _on_warmth_changed(self, scale: Gtk.Scale) -> None:
        """Update Warmth Label From Slider Position."""
        value = scale.get_value()
        if value < -0.35:
            label = "Cool"
        elif value < -0.1:
            label = "Slightly Cool"
        elif value < 0.1:
            label = "Neutral"
        elif value < 0.35:
            label = "Slightly Warm"
        else:
            label = "Warm"
        self._warmth_label.set_label(label)
        if not self._updating_labels:
            self._sync_cached_config()

    def _on_blur_changed(self, *_args) -> None:
        """Publish Blur Toggle Changes To The Preview Thread."""
        if not self._updating_labels:
            self._sync_cached_config()

    def _start_preview(self) -> None:
        """Start Or Restart The Live Preview Capture."""
        if not ensure_ffmpeg():
            self._preview_status.set_label(
                "FFmpeg not found. Run ./install.sh or install ffmpeg."
            )
            return
        if self._preview is not None and self._preview.is_alive():
            return
        self._sync_cached_config()
        self._active_resolution_index = int(
            round(self._resolution_scale.get_value())
        )
        self._preview_status.set_label("Starting camera preview...")
        self._preview = PreviewCapture(
            get_config=self._get_cached_config,
            on_frame=self._on_preview_frame,
            on_error=self._on_preview_error,
        )
        self._preview.start()

    def _stop_preview(self) -> None:
        """Stop Preview Capture And Release The Camera."""
        if self._resolution_restart_id:
            GLib.source_remove(self._resolution_restart_id)
            self._resolution_restart_id = 0
        preview = self._preview
        self._preview = None
        if preview is not None:
            preview.stop()
            preview.join(timeout=2.0)

    def _on_preview_frame(self, image: Image.Image) -> bool:
        """Update The Circular Preview Image."""
        if self._preview is None:
            return False
        data = image.tobytes("raw", "RGB")
        pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            data,
            GdkPixbuf.Colorspace.RGB,
            False,
            8,
            image.width,
            image.height,
            image.width * 3,
        )
        self._preview_pixbuf = pixbuf.copy()
        texture = Gdk.Texture.new_for_pixbuf(self._preview_pixbuf)
        self._preview_picture.set_paintable(texture)
        self._preview_status.set_label("Live preview")
        return False

    def _on_preview_error(self, message: str) -> bool:
        """Show Preview Failure Text."""
        self._preview_status.set_label(message)
        return False

    def _on_run_clicked(self, *_args) -> None:
        """Launch Overlay Process With Current Settings."""
        if self._overlay_process is not None and self._overlay_process.poll() is None:
            return

        if not ensure_ffmpeg():
            dialog = Adw.MessageDialog.new(
                self,
                "FFmpeg Not Found",
                "Run ./install.sh once, or install ffmpeg manually.",
            )
            dialog.add_response("ok", "OK")
            dialog.set_default_response("ok")
            dialog.set_close_response("ok")
            dialog.present()
            return

        self._stop_preview()
        self._sync_cached_config()
        config = self._get_cached_config()
        handle, temp_name = tempfile.mkstemp(prefix="camera-overlay-", suffix=".json")
        os.close(handle)
        config_path = Path(temp_name)
        config_path.write_text(
            json.dumps(
                {
                    "capture_width": config.capture_width,
                    "capture_height": config.capture_height,
                    "brightness": config.brightness,
                    "warmth": config.warmth,
                    "initial_zoom": config.initial_zoom,
                    "blur_background": config.blur_background,
                    "camera_device": config.camera_device,
                }
            ),
            encoding="utf-8",
        )
        self._config_path = config_path

        env = os.environ.copy()
        pythonpath = str(src_dir())
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            pythonpath if not existing else "{}:{}".format(pythonpath, existing)
        )
        command = [
            sys.executable,
            "-m",
            "camera_overlay",
            "--overlay",
            "--config",
            str(config_path),
        ]
        self._overlay_process = subprocess.Popen(command, env=env, cwd=str(project_root()))
        self._run_button.set_sensitive(False)
        self._preview_status.set_label("Overlay running...")
        self.set_visible(False)
        GLib.timeout_add(400, self._poll_overlay_process)

    def _poll_overlay_process(self) -> bool:
        """Restore Settings When Overlay Process Exits."""
        process = self._overlay_process
        if process is None:
            return False
        if process.poll() is None:
            return True

        self._overlay_process = None
        self._run_button.set_sensitive(True)
        if self._config_path is not None and self._config_path.exists():
            try:
                self._config_path.unlink()
            except OSError:
                pass
            self._config_path = None
        if not self.is_visible():
            self.set_visible(True)
            self.present()
        self._start_preview()
        return False

    def _on_close_request(self, *_args) -> bool:
        """Stop Preview And Overlay Child When Settings Window Closes."""
        self._stop_preview()
        process = self._overlay_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        self._overlay_process = None
        return False


class SettingsApplication(Adw.Application):
    """Libadwaita Application Hosting The Settings Window."""

    def __init__(self) -> None:
        """Initialize Application Identity."""
        super().__init__(
            application_id=SETTINGS_APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._window: Optional[SettingsWindow] = None

    def do_activate(self) -> None:  # noqa: N802
        """Show Or Focus The Settings Window."""
        if self._window is None:
            self._window = SettingsWindow(application=self)
        self._window.set_title(APP_DISPLAY_NAME)
        self._window.present()


def main() -> int:
    """Run Native GNOME Settings Application."""
    GLib.set_prgname(APP_WM_CLASS)
    GLib.set_application_name(APP_DISPLAY_NAME)
    Adw.init()
    app = SettingsApplication()
    return int(app.run([APP_WM_CLASS]))


if __name__ == "__main__":
    sys.exit(main())
