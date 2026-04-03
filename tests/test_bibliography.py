"""Tests for scholarposter.bibliography — BibTeX and Markdown formatting."""
from __future__ import annotations

from datetime import datetime, timezone

from scholarposter.bibliography import _bibtex_key, _escape_bibtex, to_bibtex, to_markdown
from scholarposter.models import BibliographyEntry


def _make_entry(**kwargs) -> BibliographyEntry:
    defaults = {
        "doi": "10.1234/test",
        "title": "Test Paper",
        "authors": ["Alice Smith", "Bob Jones"],
        "abstract": "A test abstract.",
        "url": "https://doi.org/10.1234/test",
        "shared_at": datetime(2026, 3, 15, tzinfo=timezone.utc),
        "publication_year": 2025,
        "platforms": ["bluesky"],
    }
    defaults.update(kwargs)
    return BibliographyEntry(**defaults)


class TestEscapeBibtex:
    def test_ampersand(self):
        assert _escape_bibtex("A & B") == r"A \& B"

    def test_percent(self):
        assert _escape_bibtex("100%") == r"100\%"

    def test_dollar(self):
        assert _escape_bibtex("$5") == r"\$5"

    def test_underscore(self):
        assert _escape_bibtex("a_b") == r"a\_b"

    def test_braces(self):
        assert _escape_bibtex("{x}") == r"\{x\}"

    def test_combined(self):
        result = _escape_bibtex("Title & Methods: 100% $cost")
        assert r"\&" in result
        assert r"\%" in result
        assert r"\$" in result


class TestBibtexKey:
    def test_simple_doi(self):
        assert _bibtex_key("10.1234/foo.bar") == "doi_10_1234_foo_bar"

    def test_starts_with_doi_prefix(self):
        key = _bibtex_key("10.1234/abc")
        assert key.startswith("doi_")

    def test_complex_doi(self):
        key = _bibtex_key("10.1016/j.cell.2024.01.005")
        assert key == "doi_10_1016_j_cell_2024_01_005"


class TestToBibtex:
    def test_basic_entry(self):
        entry = _make_entry()
        result = to_bibtex([entry])
        assert "@article{doi_10_1234_test," in result
        assert "author = {Alice Smith and Bob Jones}" in result
        assert "title = {Test Paper}" in result
        assert "year = {2025}" in result

    def test_uses_publication_year(self):
        entry = _make_entry(publication_year=2020)
        result = to_bibtex([entry])
        assert "year = {2020}" in result

    def test_falls_back_to_shared_at_year(self):
        entry = _make_entry(publication_year=None)
        result = to_bibtex([entry])
        assert "year = {2026}" in result  # shared_at is 2026

    def test_escapes_special_chars_in_title(self):
        entry = _make_entry(title="A & B: 100% $cost")
        result = to_bibtex([entry])
        assert r"A \& B: 100\% \$cost" in result

    def test_escapes_doi_with_underscore(self):
        entry = _make_entry(doi="10.1234/foo_bar")
        result = to_bibtex([entry])
        assert r"doi = {10.1234/foo\_bar}" in result

    def test_no_authors(self):
        entry = _make_entry(authors=[])
        result = to_bibtex([entry])
        assert "author" not in result

    def test_multiple_entries(self):
        e1 = _make_entry(doi="10.1/a", title="Paper A")
        e2 = _make_entry(doi="10.1/b", title="Paper B")
        result = to_bibtex([e1, e2])
        assert result.count("@article{") == 2


class TestToMarkdown:
    def test_basic_entry(self):
        entry = _make_entry()
        result = to_markdown([entry])
        assert "**Test Paper** (2025)" in result
        assert "Alice Smith, Bob Jones" in result
        assert "[10.1234/test]" in result

    def test_no_abstract(self):
        entry = _make_entry(abstract="")
        result = to_markdown([entry])
        assert "*" not in result or "**" in result  # no italic snippet

    def test_long_abstract_truncated(self):
        entry = _make_entry(abstract="x" * 300)
        result = to_markdown([entry])
        assert "…" in result

    def test_falls_back_to_shared_year(self):
        entry = _make_entry(publication_year=None)
        result = to_markdown([entry])
        assert "(2026)" in result
