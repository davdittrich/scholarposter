"""Tests for enrichment/html.py - OG tag extraction and body text extraction."""
from __future__ import annotations

import pytest

from scholarposter.enrichment.html import extract_og_tags, extract_body_text


FIXTURE_DIR = "tests/fixtures"


@pytest.fixture
def sample_html_page() -> str:
    """HTML page with full OG tags."""
    with open(f"{FIXTURE_DIR}/sample_html_page.html", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def sample_og_page() -> str:
    """HTML page with NO OG tags, only <title>, <h1>, and <p>."""
    with open(f"{FIXTURE_DIR}/sample_og_page.html", encoding="utf-8") as f:
        return f.read()


class TestExtractOgTags:
    def test_extracts_og_title(self, sample_html_page: str) -> None:
        result = extract_og_tags(sample_html_page)
        assert result["title"] == "Game Theory in Modern Economics: A Survey"

    def test_extracts_og_description(self, sample_html_page: str) -> None:
        result = extract_og_tags(sample_html_page)
        assert "game theory" in result["description"].lower()

    def test_extracts_og_image(self, sample_html_page: str) -> None:
        result = extract_og_tags(sample_html_page)
        assert result["image"] == "https://example.com/images/game-theory-survey.jpg"

    def test_fallback_title_from_title_tag(self, sample_og_page: str) -> None:
        """When no og:title, fall back to <title> tag."""
        result = extract_og_tags(sample_og_page)
        assert result["title"] == "Fallback Title Only Page"

    def test_fallback_description_from_p_tag(self, sample_og_page: str) -> None:
        """When no og:description, fall back to first <p> text."""
        result = extract_og_tags(sample_og_page)
        assert result["description"] is not None
        assert len(result["description"]) > 0

    def test_no_og_image_returns_none(self, sample_og_page: str) -> None:
        """When no og:image, image key should be None."""
        result = extract_og_tags(sample_og_page)
        assert result.get("image") is None

    def test_empty_html_returns_empty_dict_values(self) -> None:
        result = extract_og_tags("<html><body></body></html>")
        assert result.get("title") is None
        assert result.get("description") is None
        assert result.get("image") is None

    def test_returns_dict(self, sample_html_page: str) -> None:
        result = extract_og_tags(sample_html_page)
        assert isinstance(result, dict)


class TestExtractBodyText:
    def test_returns_string_for_valid_html(self, sample_html_page: str) -> None:
        result = extract_body_text(sample_html_page)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_none_for_empty_html(self) -> None:
        result = extract_body_text("<html><body></body></html>")
        assert result is None

    def test_extracts_meaningful_text(self, sample_html_page: str) -> None:
        result = extract_body_text(sample_html_page)
        assert result is not None
        # Should contain some of the article text
        assert "game" in result.lower() or "theory" in result.lower()

    def test_returns_none_for_blank_page(self) -> None:
        result = extract_body_text("<!DOCTYPE html><html><head><title>X</title></head><body>   </body></html>")
        assert result is None
