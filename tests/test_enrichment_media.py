"""Tests for enrichment/media.py - image resize, convert, download."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import httpx
import pytest
from PIL import Image

from scholarposter.enrichment.media import (
    resize_image,
    convert_to_jpeg,
    download_media,
)


def make_jpeg_bytes(width: int = 100, height: int = 100, quality: int = 85) -> bytes:
    """Create a minimal JPEG image in memory."""
    img = Image.new("RGB", (width, height), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def make_png_bytes(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal PNG image in memory."""
    img = Image.new("RGB", (width, height), color=(128, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestResizeImage:
    def test_returns_bytes(self) -> None:
        img_bytes = make_jpeg_bytes()
        result = resize_image(img_bytes, max_size_kb=100, max_dims=(512, 512))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_output_within_max_size_kb(self) -> None:
        img_bytes = make_jpeg_bytes(800, 800, quality=95)
        result = resize_image(img_bytes, max_size_kb=10, max_dims=(512, 512))
        assert len(result) <= 10 * 1024

    def test_small_image_unchanged_size(self) -> None:
        img_bytes = make_jpeg_bytes(50, 50, quality=50)
        result = resize_image(img_bytes, max_size_kb=100, max_dims=(512, 512))
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"

    def test_resizes_dimensions_if_too_large(self) -> None:
        img_bytes = make_jpeg_bytes(1000, 1000)
        result = resize_image(img_bytes, max_size_kb=500, max_dims=(200, 200))
        img = Image.open(io.BytesIO(result))
        assert img.width <= 200
        assert img.height <= 200

    def test_invalid_bytes_raises(self) -> None:
        with pytest.raises(Exception):
            resize_image(b"not an image", max_size_kb=100, max_dims=(512, 512))


class TestConvertToJpeg:
    def test_converts_png_to_jpeg(self) -> None:
        png_bytes = make_png_bytes()
        result = convert_to_jpeg(png_bytes)
        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"

    def test_jpeg_input_returns_jpeg(self) -> None:
        jpeg_bytes = make_jpeg_bytes()
        result = convert_to_jpeg(jpeg_bytes)
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"

    def test_invalid_bytes_raises(self) -> None:
        with pytest.raises(Exception):
            convert_to_jpeg(b"not an image")

    def test_output_is_rgb(self) -> None:
        png_bytes = make_png_bytes()
        result = convert_to_jpeg(png_bytes)
        img = Image.open(io.BytesIO(result))
        assert img.mode == "RGB"


class TestDownloadMedia:
    def test_returns_bytes_on_success(self) -> None:
        mock_client = MagicMock()
        mock_head_resp = MagicMock()
        mock_head_resp.headers = {"content-length": "10"}
        mock_get_resp = MagicMock()
        mock_get_resp.content = b"media data"
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = mock_head_resp
        mock_client.get.return_value = mock_get_resp
        with patch("scholarposter.enrichment.media.httpx.Client", return_value=mock_client):
            result = download_media("https://example.com/image.jpg", timeout=10)
        assert result == b"media data"

    def test_returns_none_on_timeout(self) -> None:
        with patch("scholarposter.enrichment.media.httpx.Client", side_effect=httpx.TimeoutException("timeout")):
            result = download_media("https://example.com/image.jpg", timeout=10)
        assert result is None

    def test_returns_none_on_http_error(self) -> None:
        with patch("scholarposter.enrichment.media.httpx.Client", side_effect=httpx.HTTPError("error")):
            result = download_media("https://example.com/image.jpg", timeout=10)
        assert result is None

    def test_returns_none_when_content_length_exceeds_max(self) -> None:
        mock_client = MagicMock()
        mock_head_resp = MagicMock()
        mock_head_resp.headers = {"content-length": "999999999"}
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = mock_head_resp
        with patch("scholarposter.enrichment.media.httpx.Client", return_value=mock_client):
            result = download_media("https://example.com/huge.pdf", max_bytes=1000)
        assert result is None
        mock_client.get.assert_not_called()

    def test_returns_none_when_body_exceeds_max(self) -> None:
        mock_client = MagicMock()
        mock_head_resp = MagicMock()
        mock_head_resp.headers = {}  # No Content-Length
        mock_get_resp = MagicMock()
        mock_get_resp.content = b"x" * 2000
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = mock_head_resp
        mock_client.get.return_value = mock_get_resp
        with patch("scholarposter.enrichment.media.httpx.Client", return_value=mock_client):
            result = download_media("https://example.com/data", max_bytes=1000)
        assert result is None

    def test_proceeds_when_no_content_length(self) -> None:
        mock_client = MagicMock()
        mock_head_resp = MagicMock()
        mock_head_resp.headers = {}  # No Content-Length
        mock_get_resp = MagicMock()
        mock_get_resp.content = b"small data"
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = mock_head_resp
        mock_client.get.return_value = mock_get_resp
        with patch("scholarposter.enrichment.media.httpx.Client", return_value=mock_client):
            result = download_media("https://example.com/data", max_bytes=50_000_000)
        assert result == b"small data"
