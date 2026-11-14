import json
from dataclasses import asdict, dataclass
from pathlib import Path

from camera_overlay.constants import CAMERA_DEVICE


@dataclass
class OverlayConfig:
    """User Selected Overlay Launch Settings."""

    capture_width: int = 640
    capture_height: int = 480
    brightness: float = 1.0
    warmth: float = 0.0
    initial_zoom: float = 1.0
    blur_background: bool = False
    camera_device: str = CAMERA_DEVICE

    def to_dict(self) -> dict:
        """Serialize Config For Temporary Launch Files."""
        return asdict(self)


def load_config(path: Path) -> OverlayConfig:
    """Load Overlay Config From A JSON File."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return OverlayConfig(
        capture_width=int(payload.get("capture_width", 640)),
        capture_height=int(payload.get("capture_height", 480)),
        brightness=float(payload.get("brightness", 1.0)),
        warmth=float(payload.get("warmth", 0.0)),
        initial_zoom=float(payload.get("initial_zoom", 1.0)),
        blur_background=bool(payload.get("blur_background", False)),
        camera_device=str(payload.get("camera_device", CAMERA_DEVICE)),
    )
