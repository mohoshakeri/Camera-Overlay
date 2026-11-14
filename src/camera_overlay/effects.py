from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from camera_overlay.config import OverlayConfig
from camera_overlay.constants import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    WARMTH_MAX,
    WARMTH_MIN,
)


def apply_brightness(image: Image.Image, brightness: float) -> Image.Image:
    """Apply Brightness Multiplier To An RGB Image."""
    value = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, brightness))
    if abs(value - 1.0) < 0.01:
        return image
    return ImageEnhance.Brightness(image).enhance(value)


def apply_warmth(image: Image.Image, warmth: float) -> Image.Image:
    """Shift Color Temperature From Cool To Warm."""
    value = max(WARMTH_MIN, min(WARMTH_MAX, warmth))
    if abs(value) < 0.02:
        return image
    r, g, b = image.split()
    if value > 0:
        r = r.point(lambda channel: min(255, int(channel * (1.0 + 0.18 * value))))
        b = b.point(lambda channel: max(0, int(channel * (1.0 - 0.18 * value))))
    else:
        cool = -value
        r = r.point(lambda channel: max(0, int(channel * (1.0 - 0.16 * cool))))
        b = b.point(lambda channel: min(255, int(channel * (1.0 + 0.16 * cool))))
    return Image.merge("RGB", (r, g, b))


def apply_background_blur(image: Image.Image) -> Image.Image:
    """Keep Center Sharper And Soft-Blur Outer Background Area."""
    width, height = image.size
    small = image.resize(
        (max(64, width // 2), max(64, height // 2)),
        Image.Resampling.BILINEAR,
    )
    blurred_small = small.filter(ImageFilter.GaussianBlur(radius=4))
    blurred = blurred_small.resize((width, height), Image.Resampling.BILINEAR)
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    inset_x = int(width * 0.18)
    inset_y = int(height * 0.18)
    draw.ellipse(
        (inset_x, inset_y, width - inset_x, height - inset_y),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=12))
    return Image.composite(image, blurred, mask)


def apply_frame_effects(image: Image.Image, config: OverlayConfig) -> Image.Image:
    """Apply Brightness, Warmth, And Optional Background Blur."""
    image = apply_brightness(image, config.brightness)
    image = apply_warmth(image, config.warmth)
    if config.blur_background:
        image = apply_background_blur(image)
    return image
