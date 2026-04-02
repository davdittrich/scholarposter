"""Tests for enrichment/doi.py - DOI detection and Crossref lookup."""
from __future__ import annotations

import pytest
import respx
import httpx

from scholarposter.enrichment.doi import detect_dois, lookup_doi

_CROSSREF_BASE = "https://api.crossref.org/works"


def _crossref_url(doi: str) -> str:
    return f"{_CROSSREF_BASE}/{doi}"


def _ok_payload(title: str = "Test Paper Title",
                abstract: str = "<jats:p>This is the abstract text.</jats:p>",
                authors: list | None = None) -> dict:
    if authors is None:
        authors = [{"given": "Jane", "family": "Doe"}, {"given": "John", "family": "Smith"}]
    return {
        "status": "ok",
        "message": {
            "title": [title],
            "abstract": abstract,
            "author": authors,
        },
    }


class TestDetectDois:
    def test_detects_doi_in_url(self) -> None:
        urls = ["https://doi.org/10.1000/xyz123"]
        result = detect_dois(urls, "")
        assert "10.1000/xyz123" in result

    def test_detects_doi_in_text(self) -> None:
        text = "See doi:10.1038/nature12345 for details."
        result = detect_dois([], text)
        assert "10.1038/nature12345" in result

    def test_detects_doi_in_both_url_and_text(self) -> None:
        urls = ["https://doi.org/10.1000/abc"]
        text = "Also referenced in 10.5555/xyz.2024"
        result = detect_dois(urls, text)
        assert "10.1000/abc" in result
        assert "10.5555/xyz.2024" in result

    def test_deduplicates_dois(self) -> None:
        urls = ["https://doi.org/10.1000/abc"]
        text = "doi:10.1000/abc is the same DOI"
        result = detect_dois(urls, text)
        assert result.count("10.1000/abc") == 1

    def test_returns_empty_list_for_no_dois(self) -> None:
        result = detect_dois(["https://example.com/paper"], "No DOI here")
        assert result == []

    def test_returns_list(self) -> None:
        result = detect_dois([], "")
        assert isinstance(result, list)

    def test_doi_with_complex_suffix(self) -> None:
        text = "10.1016/j.cell.2023.01.005 is a complex DOI"
        result = detect_dois([], text)
        assert any("10.1016" in doi for doi in result)

    def test_strips_trailing_period_from_text(self) -> None:
        """Trailing sentence period must not be captured as part of the DOI."""
        text = "See 10.1234/foo.bar."
        result = detect_dois([], text)
        assert "10.1234/foo.bar" in result
        assert "10.1234/foo.bar." not in result

    def test_strips_trailing_semicolon_from_text(self) -> None:
        """Trailing semicolon must not be captured as part of the DOI."""
        text = "DOI: 10.1234/foo.bar;"
        result = detect_dois([], text)
        assert "10.1234/foo.bar" in result
        assert "10.1234/foo.bar;" not in result

    def test_preserves_trailing_paren_in_suffix(self) -> None:
        """Closing parenthesis that is part of the DOI suffix must be preserved."""
        text = "10.1000/xyz(2024)"
        result = detect_dois([], text)
        assert "10.1000/xyz(2024)" in result

    def test_strips_trailing_comma_from_text(self) -> None:
        """Trailing comma (list separator) must not be captured."""
        text = "10.1234/foo.bar, and"
        result = detect_dois([], text)
        assert "10.1234/foo.bar" in result
        assert "10.1234/foo.bar," not in result

    def test_strips_trailing_colon_from_text(self) -> None:
        """Trailing colon must not be captured as part of the DOI."""
        text = "10.1234/foo.bar:"
        result = detect_dois([], text)
        assert "10.1234/foo.bar" in result
        assert "10.1234/foo.bar:" not in result

    def test_strips_trailing_period_from_url(self) -> None:
        """Trailing period in a URL must be stripped (exercises URL detection path)."""
        result = detect_dois(["https://doi.org/10.1234/foo.bar."], "")
        assert "10.1234/foo.bar" in result
        assert "10.1234/foo.bar." not in result

    def test_strips_trailing_semicolon_from_url(self) -> None:
        """Trailing semicolon in a URL must be stripped (exercises URL detection path)."""
        result = detect_dois(["https://doi.org/10.1234/foo.bar;"], "")
        assert "10.1234/foo.bar" in result

    def test_strips_trailing_comma_from_url(self) -> None:
        """Trailing comma in a URL must be stripped (exercises URL detection path)."""
        result = detect_dois(["https://doi.org/10.1234/foo.bar,"], "")
        assert "10.1234/foo.bar" in result

    def test_strips_trailing_colon_from_url(self) -> None:
        """Trailing colon in a URL must be stripped (exercises URL detection path)."""
        result = detect_dois(["https://doi.org/10.1234/foo.bar:"], "")
        assert "10.1234/foo.bar" in result

    def test_doi_inside_parentheses_sentence(self) -> None:
        """DOI inside parentheses: trailing ) is sentence punctuation here, not DOI suffix.
        The regex captures the ) as part of the DOI because ) is in the character class.
        After _clean_doi stripping (which only strips .;,:), the ) remains.
        This test documents the current behaviour: 10.1234/foo) is returned as-is."""
        text = "(see 10.1234/foo)"
        result = detect_dois([], text)
        # The regex captures "10.1234/foo)" — _clean_doi does not strip )
        assert "10.1234/foo)" in result


    def test_detect_dois_preserves_insertion_order(self) -> None:
        """First DOI encountered should be result[0], not arbitrary set order."""
        text = "First 10.1111/aaa then 10.2222/bbb"
        result = detect_dois([], text)
        assert len(result) == 2
        assert result[0] == "10.1111/aaa"
        assert result[1] == "10.2222/bbb"


class TestLookupDoi:
    @respx.mock
    def test_returns_dict_with_title(self) -> None:
        doi = "10.1000/test"
        respx.get(_crossref_url(doi)).mock(
            return_value=httpx.Response(200, json=_ok_payload())
        )
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=5)
        assert result is not None
        assert result["title"] == "Test Paper Title"

    @respx.mock
    def test_strips_html_from_abstract(self) -> None:
        doi = "10.1000/test"
        respx.get(_crossref_url(doi)).mock(
            return_value=httpx.Response(
                200,
                json=_ok_payload(abstract="<jats:p>Clean abstract text.</jats:p>", authors=[]),
            )
        )
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=5)
        assert result is not None
        assert "<jats:p>" not in result["abstract"]
        assert "Clean abstract text." in result["abstract"]

    @respx.mock
    def test_returns_authors_list(self) -> None:
        doi = "10.1000/test"
        respx.get(_crossref_url(doi)).mock(
            return_value=httpx.Response(200, json=_ok_payload())
        )
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=5)
        assert result is not None
        assert "authors" in result
        assert isinstance(result["authors"], list)
        assert len(result["authors"]) == 2

    @respx.mock
    def test_returns_none_on_not_found(self) -> None:
        doi = "10.9999/nonexistent"
        respx.get(_crossref_url(doi)).mock(
            return_value=httpx.Response(404)
        )
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=5)
        assert result is None

    @respx.mock
    def test_returns_none_on_exception(self) -> None:
        doi = "10.1000/test"
        respx.get(_crossref_url(doi)).mock(side_effect=httpx.ConnectError("Network error"))
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=5)
        assert result is None

    @respx.mock
    def test_handles_missing_abstract(self) -> None:
        doi = "10.1000/test"
        payload = {
            "status": "ok",
            "message": {
                "title": ["Title Without Abstract"],
                "author": [{"given": "Jane", "family": "Doe"}],
            },
        }
        respx.get(_crossref_url(doi)).mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=5)
        assert result is not None
        assert result["title"] == "Title Without Abstract"
        assert result.get("abstract") is None or result.get("abstract") == ""

    @respx.mock
    def test_timeout_enforced(self) -> None:
        doi = "10.1000/timeout-test"
        respx.get(_crossref_url(doi)).mock(side_effect=httpx.TimeoutException("timed out"))
        result = lookup_doi(doi, etiquette_email="test@example.com", timeout=1)
        assert result is None
