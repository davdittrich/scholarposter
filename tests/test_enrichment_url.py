"""Tests for scholarposter.enrichment.url"""
import pytest
import respx
import httpx
from scholarposter.enrichment.url import unshorten_url, detect_content_type


class TestUnshortenUrl:
    @respx.mock
    def test_follows_redirect_via_head(self):
        """HEAD follows redirects and returns the final URL."""
        respx.head("https://t.co/shortlink").mock(
            return_value=httpx.Response(
                301,
                headers={"location": "https://example.com/full-article"},
            )
        )
        respx.head("https://example.com/full-article").mock(
            return_value=httpx.Response(200)
        )
        result = unshorten_url("https://t.co/shortlink")
        assert result == "https://example.com/full-article"

    @respx.mock
    def test_returns_original_on_timeout(self):
        respx.head("https://example.com/timeout").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        result = unshorten_url("https://example.com/timeout")
        assert result == "https://example.com/timeout"

    @respx.mock
    def test_returns_original_on_error(self):
        respx.head("https://example.com/error").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = unshorten_url("https://example.com/error")
        assert result == "https://example.com/error"

    @respx.mock
    def test_no_redirect_returns_url(self):
        respx.head("https://example.com/direct").mock(
            return_value=httpx.Response(200)
        )
        result = unshorten_url("https://example.com/direct")
        assert result == "https://example.com/direct"

    @respx.mock
    def test_head_405_falls_back_to_get(self):
        """When HEAD returns 405, fall back to GET."""
        respx.head("https://example.com/no-head").mock(
            return_value=httpx.Response(405)
        )
        respx.get("https://example.com/no-head").mock(
            return_value=httpx.Response(
                301,
                headers={"location": "https://example.com/resolved"},
            )
        )
        respx.get("https://example.com/resolved").mock(
            return_value=httpx.Response(200, text="Page")
        )
        result = unshorten_url("https://example.com/no-head")
        assert result == "https://example.com/resolved"

    @respx.mock
    def test_head_and_get_both_fail_returns_original(self):
        """When HEAD returns 405 and GET also fails, return original URL."""
        respx.head("https://example.com/both-fail").mock(
            return_value=httpx.Response(405)
        )
        respx.get("https://example.com/both-fail").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = unshorten_url("https://example.com/both-fail")
        assert result == "https://example.com/both-fail"


class TestDetectContentType:
    @respx.mock
    def test_detects_html(self):
        respx.head("https://example.com/page").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
        result = detect_content_type("https://example.com/page")
        assert result == "text/html"

    @respx.mock
    def test_detects_pdf(self):
        respx.head("https://example.com/paper.pdf").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
            )
        )
        result = detect_content_type("https://example.com/paper.pdf")
        assert result == "application/pdf"

    def test_falls_back_to_extension_pdf(self):
        # No mock — test pure extension fallback for unreachable URL
        with respx.mock:
            respx.head("https://example.com/doc.pdf").mock(
                side_effect=httpx.ConnectError("refused")
            )
            result = detect_content_type("https://example.com/doc.pdf")
            assert result == "application/pdf"

    @respx.mock
    def test_returns_none_on_unknown(self):
        respx.head("https://example.com/unknown.xyz123").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = detect_content_type("https://example.com/unknown.xyz123")
        assert result is None
