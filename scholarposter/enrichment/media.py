"""Media enrichment: image resizing/conversion, and media download."""
from __future__ import annotations

import io
from typing import Optional

import httpx
from PIL import Image


def resize_image(
    img_bytes: bytes,
    max_size_kb: int,
    max_dims: tuple[int, int],
) -> bytes:
    """Resize an image to fit within max_dims and max_size_kb.

    Opens the image, thumbnails it to max_dims (preserving aspect ratio),
    then reduces JPEG quality in a loop until the output is within max_size_kb.

    Raises on invalid image bytes (re-raises PIL exception).
    """
    img = Image.open(io.BytesIO(img_bytes))

    # Convert to RGB for JPEG compatibility
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Resize to fit within max_dims while preserving aspect ratio
    img.thumbnail(max_dims, Image.Resampling.LANCZOS)

    max_bytes = max_size_kb * 1024
    quality = 85

    while quality > 10:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
        quality -= 5

    # Final attempt at lowest quality
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=10)
    return buf.getvalue()


def convert_to_jpeg(img_bytes: bytes) -> bytes:
    """Convert image bytes to JPEG format.

    Opens the image, converts to RGB, and saves as JPEG.
    Raises on invalid image bytes.
    """
    img = Image.open(io.BytesIO(img_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def download_media(url: str, timeout: int = 30, max_bytes: int = 50_000_000) -> Optional[bytes]:
    """Download media from a URL and return the raw bytes.

    Checks Content-Length via HEAD before downloading. Discards responses
    exceeding max_bytes (default 50MB). Returns None on timeout or error.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # HEAD check for Content-Length before downloading
            try:
                head = client.head(url)
                cl = int(head.headers.get("content-length", 0))
                if cl > max_bytes:
                    return None
            except (httpx.HTTPError, ValueError):
                pass  # HEAD failed or no Content-Length; proceed with GET
            response = client.get(url)
            if len(response.content) > max_bytes:
                return None
            return response.content
    except (httpx.TimeoutException, httpx.HTTPError, Exception):
        return None


def generate_card_thumbnail(title: str, subtitle: str, cfg: "ThumbnailFallbackConfig") -> bytes:
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (cfg.width, cfg.height), color=cfg.background_color)
    draw = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.load_default(size=48)
        sub_font = ImageFont.load_default(size=28)
        brand_font = ImageFont.load_default(size=20)
    except TypeError:
        title_font = sub_font = brand_font = ImageFont.load_default()

    words = title.split()
    lines, line = [], []
    for word in words:
        line.append(word)
        if draw.textlength(" ".join(line), font=title_font) > cfg.width - 120:
            if len(line) > 1:
                lines.append(" ".join(line[:-1]))
                line = [word]
            else:
                lines.append(" ".join(line))
                line = []
        if len(lines) >= 2:
            break
    if line:
        lines.append(" ".join(line))
    lines = lines[:2]

    y = 80
    for ln in lines:
        draw.text((60, y), ln, font=title_font, fill=cfg.text_color)
        y += 64
    if subtitle:
        draw.text((60, y + 20), subtitle, font=sub_font, fill=cfg.text_color)
    draw.text((cfg.width - 220, cfg.height - 40), "⚗️ scholarposter", font=brand_font, fill=cfg.text_color)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
