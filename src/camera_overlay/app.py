import argparse
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import QApplication

from camera_overlay.config import OverlayConfig, load_config
from camera_overlay.constants import (
    APP_DESKTOP_ID,
    APP_DISPLAY_NAME,
    APP_WM_CLASS,
)
from camera_overlay.overlay_window import CameraOverlay


def configure_qt_identity(app: QApplication) -> None:
    """Set Stable Application Identity For Window Managers."""
    app.setApplicationName(APP_WM_CLASS)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_WM_CLASS)
    app.setDesktopFileName(APP_DESKTOP_ID)


def run_overlay(config: OverlayConfig) -> int:
    """Run Only The Circular Camera Overlay Window."""
    argv = list(sys.argv)
    argv[0] = APP_WM_CLASS
    app = QApplication(argv)
    configure_qt_identity(app)
    app.setQuitOnLastWindowClosed(True)
    overlay = CameraOverlay(config)
    overlay.setObjectName(APP_WM_CLASS)
    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    return app.exec_()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI Arguments For Settings Or Overlay Mode."""
    parser = argparse.ArgumentParser(description=APP_DISPLAY_NAME)
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="Run Overlay Window Only",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON Config Path For Overlay Mode",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Dispatch To Native Settings Or Overlay Window."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.overlay:
        if args.config is None:
            print("Overlay mode needs --config", file=sys.stderr)
            return 2
        return run_overlay(load_config(args.config))

    from camera_overlay.settings_window import main as settings_main

    return settings_main()
