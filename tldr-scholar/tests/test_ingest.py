"""Tests for tldr_scholar.ingest."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from tldr_scholar.ingest import (
    ingest,
    UnsupportedInputError,
    PasswordProtectedError,
    EmptyTextError,
)


class TestIngestFile:
    def test_plain_text_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Hello world. This is a test document.")
        text, input_type = ingest(str(f))
        assert input_type == "text"
        assert "Hello world" in text

    def test_markdown_file_strips_formatting(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\n**Bold** text with [link](http://example.com)")
        text, input_type = ingest(str(f))
        assert input_type == "markdown"
        assert "# Title" not in text  # heading stripped
        assert "**Bold**" not in text  # emphasis stripped
        assert "Bold" in text  # text preserved
        assert "link" in text  # link text preserved

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "test.png"
        f.write_bytes(b"\x89PNG")
        with pytest.raises(UnsupportedInputError, match="Unsupported file type"):
            ingest(str(f))

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            ingest("/nonexistent/file.txt")

    def test_empty_text_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("   ")
        with pytest.raises(EmptyTextError):
            ingest(str(f))


class TestIngestUrl:
    @respx.mock
    def test_html_url(self):
        respx.head("https://example.com/article").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/html"})
        )
        respx.route(method="GET", url="https://example.com/article").mock(
            return_value=httpx.Response(200, text="<html><body><p>Research findings show X.</p></body></html>")
        )
        with patch("tldr_scholar.ingest.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "Research findings show X."
            text, input_type = ingest("https://example.com/article")
        assert input_type == "html"
        assert "Research findings" in text

    def test_unsupported_scheme(self):
        with pytest.raises(UnsupportedInputError, match="Unsupported URL scheme"):
            ingest("ftp://example.com/file")

    @respx.mock
    def test_empty_html_extraction(self):
        respx.head("https://example.com/empty").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/html"})
        )
        respx.route(method="GET", url="https://example.com/empty").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        with patch("tldr_scholar.ingest.trafilatura") as mock_traf:
            mock_traf.extract.return_value = None
            with pytest.raises(EmptyTextError):
                ingest("https://example.com/empty")


class TestFileSizeLimit:
    def test_large_file_truncated(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 6_000_000)
        text, _ = ingest(str(f))
        assert len(text) <= 5_000_000
