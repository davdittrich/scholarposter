"""Tests for enrichment/media.py - image resize, convert, video probe, download."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import httpx
import pytest
from PIL import Image

from scholarposter.enrichment.media import (
    resize_image,
    convert_to_jpeg,
    probe_video,
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
        # Create a larger image and demand it fit under 10 KB
        img_bytes = make_jpeg_bytes(800, 800, quality=95)
        result = resize_image(img_bytes, max_size_kb=10, max_dims=(512, 512))
        assert len(result) <= 10 * 1024

    def test_small_image_unchanged_size(self) -> None:
        """A small image already within limits should still be valid JPEG."""
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


class TestProbeVideo:
    def test_returns_dict_with_expected_keys(self) -> None:
        mock_stream = MagicMock()
        mock_stream.codec_context.name = "h264"
        mock_stream.codec_context.width = 1920
        mock_stream.codec_context.height = 1080

        mock_container = MagicMock()
        mock_container.duration = 10_000_000  # in av time base units
        mock_container.streams.video = [mock_stream]

        with patch("av.open", return_value=mock_container):
            result = probe_video(b"fake video bytes")

        assert result is not None
        assert "duration" in result
        assert "codec" in result
        assert "width" in result
        assert "height" in result

    def test_returns_correct_values(self) -> None:
        mock_stream = MagicMock()
        mock_stream.codec_context.name = "h264"
        mock_stream.codec_context.width = 1920
        mock_stream.codec_context.height = 1080

        mock_container = MagicMock()
        mock_container.duration = 5_000_000
        mock_container.streams.video = [mock_stream]

        with patch("av.open", return_value=mock_container):
            result = probe_video(b"fake video bytes")

        assert result is not None
        assert result["codec"] == "h264"
        assert result["width"] == 1920
        assert result["height"] == 1080

    def test_returns_none_on_exception(self) -> None:
        with patch("av.open", side_effect=Exception("Invalid video")):
            result = probe_video(b"invalid bytes")
        assert result is None

    def test_returns_none_for_no_video_streams(self) -> None:
        mock_container = MagicMock()
        mock_container.streams.video = []

        with patch("av.open", return_value=mock_container):
            result = probe_video(b"fake bytes")
        assert result is None


class TestDownloadMedia:
    def test_returns_bytes_on_success(self) -> None:
        mock_response = MagicMock()
        mock_response.content = b"media data"
        with patch("httpx.get", return_value=mock_response):
            result = download_media("https://example.com/image.jpg", timeout=10)
        assert result == b"media data"

    def test_returns_none_on_timeout(self) -> None:
        with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
            result = download_media("https://example.com/image.jpg", timeout=10)
        assert result is None

    def test_returns_none_on_http_error(self) -> None:
        with patch("httpx.get", side_effect=httpx.HTTPError("error")):
            result = download_media("https://example.com/image.jpg", timeout=10)
        assert result is None

    def test_calls_with_follow_redirects(self) -> None:
        mock_response = MagicMock()
        mock_response.content = b"data"
        with patch("httpx.get", return_value=mock_response) as mock_get:
            download_media("https://example.com/image.jpg", timeout=10)
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs.get("follow_redirects") is True
