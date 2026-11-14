#!/usr/bin/env python3
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def _lerp(a: float, b: float, t: float) -> float:
    """Linearly Interpolate Between Two Values."""
    return a + (b - a) * t


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """Mix Two RGB Colors."""
    return (
        int(_lerp(c1[0], c2[0], t)),
        int(_lerp(c1[1], c2[1], t)),
        int(_lerp(c1[2], c2[2], t)),
    )


def _draw_radial_circle(
    image: Image.Image,
    center: tuple[float, float],
    radius: float,
    inner: tuple[int, int, int],
    outer: tuple[int, int, int],
    alpha: int = 255,
) -> None:
    """Paint A Soft Radial Gradient Disc."""
    width, height = image.size
    cx, cy = center
    pixels = image.load()
    radius_sq = radius * radius
    for y in range(height):
        for x in range(width):
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            dist_sq = dx * dx + dy * dy
            if dist_sq > radius_sq:
                continue
            t = math.sqrt(dist_sq) / radius
            # Ease Out For A Glassier Falloff
            t = t * t
            r, g, b = _mix(inner, outer, t)
            existing = pixels[x, y]
            out_a = alpha / 255.0
            inv = 1.0 - out_a
            pixels[x, y] = (
                int(r * out_a + existing[0] * inv),
                int(g * out_a + existing[1] * inv),
                int(b * out_a + existing[2] * inv),
                max(existing[3], alpha),
            )


def generate_icon(output_path: Path, size: int = 512) -> None:
    """Create A Polished Circular Camera Overlay Icon."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Soft Drop Shadow
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    margin = int(size * 0.08)
    shadow_draw.ellipse(
        (margin, margin + int(size * 0.03), size - margin, size - margin + int(size * 0.03)),
        fill=(15, 23, 42, 90),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=size * 0.045))
    canvas = Image.alpha_composite(canvas, shadow)

    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    center = (size / 2, size / 2)
    radius = size * 0.42
    _draw_radial_circle(
        disc,
        center,
        radius,
        inner=(96, 165, 250),
        outer=(37, 99, 235),
    )
    # Cool Teal Accent Toward Bottom
    accent = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _draw_radial_circle(
        accent,
        (size * 0.58, size * 0.62),
        radius * 0.85,
        inner=(34, 211, 238),
        outer=(37, 99, 235),
        alpha=110,
    )
    disc = Image.alpha_composite(disc, accent)

    draw = ImageDraw.Draw(disc)
    ring_width = max(3, size // 42)
    inset = size * 0.08 + ring_width
    draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(255, 255, 255, 210),
        width=ring_width,
    )

    # Inner Lens Well
    well = size * 0.22
    draw.ellipse(
        (well, well, size - well, size - well),
        fill=(15, 23, 42, 235),
    )
    draw.ellipse(
        (well, well, size - well, size - well),
        outline=(148, 163, 184, 120),
        width=max(2, size // 90),
    )

    # Lens Glass
    lens = size * 0.32
    lens_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _draw_radial_circle(
        lens_layer,
        center,
        size * 0.18,
        inner=(125, 211, 252),
        outer=(30, 64, 175),
    )
    disc = Image.alpha_composite(disc, lens_layer)
    draw = ImageDraw.Draw(disc)
    draw.ellipse(
        (lens, lens, size - lens, size - lens),
        outline=(226, 232, 240, 200),
        width=max(2, size // 80),
    )

    # Iris Ring
    iris = size * 0.38
    draw.ellipse(
        (iris, iris, size - iris, size - iris),
        outline=(56, 189, 248, 160),
        width=max(2, size // 100),
    )

    # Specular Highlight
    hx = size * 0.38
    hy = size * 0.34
    hw = size * 0.11
    hh = size * 0.07
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(highlight)
    hdraw.ellipse((hx, hy, hx + hw, hy + hh), fill=(255, 255, 255, 200))
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius=size * 0.012))
    disc = Image.alpha_composite(disc, highlight)

    canvas = Image.alpha_composite(canvas, disc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, optimize=True)


def main() -> int:
    """Write Icon File From CLI Argument Or Default Path."""
    root = Path(__file__).resolve().parents[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "assets" / "icon.png"
    generate_icon(target)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
