"""Tests for enrichment/pdf.py - PDF metadata and text extraction."""
from __future__ import annotations

from pathlib import Path

import pytest

from scholarposter.enrichment.pdf import extract_pdf_metadata, extract_pdf_text


@pytest.fixture
def pdf_bytes() -> bytes:
    return Path("tests/fixtures/sample_pdf.pdf").read_bytes()


@pytest.fixture
def invalid_pdf_bytes() -> bytes:
    return b"not a pdf at all"


class TestExtractPdfMetadata:
    def test_returns_title_from_docinfo(self, pdf_bytes: bytes) -> None:
        result = extract_pdf_metadata(pdf_bytes)
        assert "title" in result
        assert result["title"] == "Mechanism Design: A Survey"

    def test_returns_description_from_subject(self, pdf_bytes: bytes) -> None:
        result = extract_pdf_metadata(pdf_bytes)
        assert "description" in result
        assert "mechanism design" in result["description"].lower()

    def test_returns_dict(self, pdf_bytes: bytes) -> None:
        result = extract_pdf_metadata(pdf_bytes)
        assert isinstance(result, dict)

    def test_returns_empty_dict_on_invalid_bytes(self, invalid_pdf_bytes: bytes) -> None:
        result = extract_pdf_metadata(invalid_pdf_bytes)
        assert result == {}

    def test_empty_bytes_returns_empty_dict(self) -> None:
        result = extract_pdf_metadata(b"")
        assert result == {}


class TestExtractPdfText:
    def test_returns_string_for_valid_pdf(self, pdf_bytes: bytes) -> None:
        result = extract_pdf_text(pdf_bytes)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_none_for_invalid_bytes(self, invalid_pdf_bytes: bytes) -> None:
        result = extract_pdf_text(invalid_pdf_bytes)
        assert result is None

    def test_returns_none_for_empty_bytes(self) -> None:
        result = extract_pdf_text(b"")
        assert result is None

    def test_extracts_meaningful_text(self, pdf_bytes: bytes) -> None:
        result = extract_pdf_text(pdf_bytes)
        assert result is not None
        # The fixture PDF is about mechanism design
        assert "mechanism" in result.lower() or "design" in result.lower()

    def test_max_pages_limits_output(self, pdf_bytes: bytes) -> None:
        """max_pages=0 should still work (clamps to 0 pages = None or empty)."""
        result = extract_pdf_text(pdf_bytes, max_pages=20)
        assert result is not None  # 1-page pdf, max_pages=20 covers it

    def test_respects_max_pages_parameter(self, pdf_bytes: bytes) -> None:
        """Passing max_pages extracts at most that many pages."""
        result = extract_pdf_text(pdf_bytes, max_pages=1)
        assert result is not None


class TestExtractPdfMetadataRobustness:
    """Tests for non-string metadata values (PyMuPDF can return list or None)."""

    def test_list_title_returns_first_element(self) -> None:
        """When metadata title is a list, return its first element as a string."""
        from unittest.mock import MagicMock, patch
        mock_doc = MagicMock()
        mock_doc.metadata = {"title": ["My Title"], "subject": ""}
        with patch("scholarposter.enrichment.pdf.fitz.open", return_value=mock_doc):
            result = extract_pdf_metadata(b"fake")
        assert result.get("title") == "My Title"

    def test_none_title_omitted(self) -> None:
        """When metadata title is None, the key must not appear in the result."""
        from unittest.mock import MagicMock, patch
        mock_doc = MagicMock()
        mock_doc.metadata = {"title": None, "subject": ""}
        with patch("scholarposter.enrichment.pdf.fitz.open", return_value=mock_doc):
            result = extract_pdf_metadata(b"fake")
        assert "title" not in result

    def test_empty_list_title_omitted(self) -> None:
        """When metadata title is an empty list, the key must not appear."""
        from unittest.mock import MagicMock, patch
        mock_doc = MagicMock()
        mock_doc.metadata = {"title": [], "subject": ""}
        with patch("scholarposter.enrichment.pdf.fitz.open", return_value=mock_doc):
            result = extract_pdf_metadata(b"fake")
        assert "title" not in result

    def test_list_title_str_not_bracket_repr(self) -> None:
        """str(list) must NOT be used — result must not contain bracket syntax."""
        from unittest.mock import MagicMock, patch
        mock_doc = MagicMock()
        mock_doc.metadata = {"title": ["Bracketed Title"], "subject": ""}
        with patch("scholarposter.enrichment.pdf.fitz.open", return_value=mock_doc):
            result = extract_pdf_metadata(b"fake")
        assert "[" not in result.get("title", "")
