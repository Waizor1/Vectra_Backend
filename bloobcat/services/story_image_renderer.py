"""Pillow-based JPG renderer for Telegram Stories share image (Bot API 7.8+).

Output: 1080x1920 JPG, brand-color background with the user's deterministic
story code printed in a large, readable strip. This is a PROCEDURAL renderer
— no template asset on disk, so the service has zero artefact dependencies.
A future iteration can swap to compositing over a design PNG, but the JPG
shape and code-overlay placement are already correct.

Color palette pulled from the Vectra mascot kit
(ai_docs/design/vectra-mascot-kit/) so the image is on-brand even before a
designer-built template ships:
  - Background gradient: #0D1117 (top) -> #161B22 (bottom)
  - Accent strip:        cyan #15AABF
  - Code text:           magenta #E64980

Caller path: `bloobcat/routes/referrals.py` -> GET /referrals/story-image.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

STORY_IMAGE_WIDTH = 1080
STORY_IMAGE_HEIGHT = 1920


def _gradient_background() -> Image.Image:
    """Vertical gradient from #0D1117 (top) to #161B22 (bottom)."""
    top = (0x0D, 0x11, 0x17)
    bottom = (0x16, 0x1B, 0x22)
    bg = Image.new("RGB", (STORY_IMAGE_WIDTH, STORY_IMAGE_HEIGHT), top)
    draw = ImageDraw.Draw(bg)
    span = STORY_IMAGE_HEIGHT
    for y in range(span):
        ratio = y / max(span - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * ratio)
        g = int(top[1] + (bottom[1] - top[1]) * ratio)
        b = int(top[2] + (bottom[2] - top[2]) * ratio)
        draw.line([(0, y), (STORY_IMAGE_WIDTH, y)], fill=(r, g, b))
    return bg


@lru_cache(maxsize=4)
def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best-effort font lookup. Falls back to Pillow's default bitmap font
    when no system TrueType is reachable — the rest of the render still
    produces a valid JPG so the endpoint never 500s.
    """
    candidates = [
        # macOS dev paths
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        # Linux production paths (Debian/Ubuntu base images)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:  # pragma: no cover - font load
                continue
    # Last-resort fallback — Pillow's built-in bitmap font.
    return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    """Center `text` horizontally at the given vertical anchor."""
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    x = (STORY_IMAGE_WIDTH - width) // 2
    draw.text((x, y), text, font=font, fill=fill)


def render_story_image(code: str) -> bytes:
    """Render the 1080x1920 story share image for `code` and return the JPG bytes.

    Deterministic: same `code` always produces byte-identical output (modulo
    Pillow version) — useful for downstream caching on the CDN edge.
    """
    bg = _gradient_background()
    draw = ImageDraw.Draw(bg)

    # Top accent strip.
    accent = (0x15, 0xAA, 0xBF)
    draw.rectangle([(0, 120), (STORY_IMAGE_WIDTH, 132)], fill=accent)

    # Headline.
    headline_font = _load_font(72, bold=True)
    _draw_centered_text(
        draw,
        "VECTRA",
        220,
        headline_font,
        (0xF0, 0xF6, 0xFC),
    )

    # Sub-headline.
    sub_font = _load_font(56, bold=True)
    _draw_centered_text(
        draw,
        "20 дней бесплатно",
        360,
        sub_font,
        (0x66, 0xD9, 0xE8),
    )

    # Body description.
    body_font = _load_font(40)
    _draw_centered_text(
        draw,
        "Приватный VPN от друга",
        520,
        body_font,
        (0xC9, 0xD1, 0xD9),
    )
    _draw_centered_text(
        draw,
        "1 устройство + 1 GB LTE",
        580,
        body_font,
        (0xC9, 0xD1, 0xD9),
    )

    # Code panel — magenta accent so it pops on the dark background.
    panel_top = 1280
    panel_bottom = 1480
    panel_left = 80
    panel_right = STORY_IMAGE_WIDTH - 80
    draw.rectangle(
        [(panel_left, panel_top), (panel_right, panel_bottom)],
        outline=(0xE6, 0x49, 0x80),
        width=6,
    )

    code_label_font = _load_font(36)
    _draw_centered_text(
        draw,
        "ТВОЙ КОД АКТИВАЦИИ",
        panel_top + 32,
        code_label_font,
        (0xE6, 0x49, 0x80),
    )
    code_font = _load_font(80, bold=True)
    _draw_centered_text(
        draw,
        code,
        panel_top + 90,
        code_font,
        (0xF0, 0xF6, 0xFC),
    )

    # Footer CTA.
    cta_font = _load_font(40, bold=True)
    _draw_centered_text(
        draw,
        "Жми ссылку ниже →",
        1600,
        cta_font,
        (0x66, 0xD9, 0xE8),
    )

    buf = io.BytesIO()
    bg.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()
