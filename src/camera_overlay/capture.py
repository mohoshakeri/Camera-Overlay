import select
import subprocess
from typing import Optional

from PIL import Image, ImageOps
from PyQt5.QtCore import QThread, pyqtSignal

from camera_overlay.config import OverlayConfig
from camera_overlay.effects import apply_frame_effects


def build_ffmpeg_command(device: str, width: int, height: int) -> list[str]:
    """Build A Low-Latency V4L2 Capture Command."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-f",
        "v4l2",
        "-framerate",
        "30",
        "-input_format",
        "mjpeg",
        "-video_size",
        "{}x{}".format(width, height),
        "-i",
        device,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-an",
        "-",
    ]


def read_latest_frame(stdout, frame_bytes: int) -> Optional[bytes]:
    """Read One Frame And Drop Any Backlog For Lower Latency."""
    raw = stdout.read(frame_bytes)
    if not raw or len(raw) < frame_bytes:
        return None
    dropped = 0
    while dropped < 8:
        ready, _, _ = select.select([stdout], [], [], 0)
        if not ready:
            break
        skipped = stdout.read(frame_bytes)
        if not skipped or len(skipped) < frame_bytes:
            break
        raw = skipped
        dropped += 1
    return raw


def ensure_ffmpeg() -> bool:
    """Return Whether FFmpeg Is Available On PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return False


class CameraWorker(QThread):
    """Capture Camera Frames Through FFmpeg."""

    frame_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config: OverlayConfig) -> None:
        """Initialize Capture Worker."""
        super().__init__()
        self._config = config
        self._running = False
        self._process: Optional[subprocess.Popen] = None

    def run(self) -> None:
        """Start FFmpeg And Emit RGB Frames Until Stopped."""
        width = self._config.capture_width
        height = self._config.capture_height
        frame_bytes = width * height * 3
        command = build_ffmpeg_command(self._config.camera_device, width, height)

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=frame_bytes,
            )
        except FileNotFoundError:
            self.failed.emit("FFmpeg is not installed.")
            return
        except OSError as error:
            self.failed.emit("Could not start camera: {}".format(error))
            return

        self._running = True
        stdout = self._process.stdout
        if stdout is None:
            self.failed.emit("Could not open camera stream.")
            self._shutdown_process()
            return

        while self._running:
            raw = read_latest_frame(stdout, frame_bytes)
            if raw is None:
                if self._running:
                    self.failed.emit(
                        "Could not read camera frames from {}.".format(
                            self._config.camera_device
                        )
                    )
                break

            image = Image.frombytes("RGB", (width, height), raw)
            image = ImageOps.mirror(image)
            max_side = 640
            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
            image = apply_frame_effects(image, self._config)
            self.frame_ready.emit(image)

        self._shutdown_process()

    def stop(self) -> None:
        """Request Worker Shutdown."""
        self._running = False
        self._shutdown_process()

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
