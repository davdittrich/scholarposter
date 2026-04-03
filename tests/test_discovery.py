"""Tests for scholarposter.discovery — OpenAlex paper discovery."""
from __future__ import annotations

import httpx
import respx
from scholarposter.discovery import (
    extract_interests,
    discover_papers,
    _parse_openalex_work,
    _reconstruct_abstract,
)


class TestExtractInterests:
    def test_ranks_authors_by_frequency(self):
        bib = [
            {"doi": "10.1/a", "authors": ["Alice", "Bob"]},
            {"doi": "10.1/b", "authors": ["Alice", "Charlie"]},
            {"doi": "10.1/c", "authors": ["Alice"]},
        ]
        result = extract_interests(bib)
        assert result["top_authors"][0] == "Alice"
        assert "Bob" in result["top_authors"]
        assert len(result["shared_dois"]) == 3

    def test_empty_bibliography(self):
        result = extract_interests([])
        assert result["top_authors"] == []
        assert result["shared_dois"] == set()

    def test_skips_empty_author_names(self):
        bib = [{"doi": "10.1/a", "authors": ["", "Alice", ""]}]
        result = extract_interests(bib)
        assert result["top_authors"] == ["Alice"]

    def test_entries_without_doi_ignored_for_shared_dois(self):
        bib = [{"authors": ["Alice"]}, {"doi": "10.1/a", "authors": []}]
        result = extract_interests(bib)
        assert result["shared_dois"] == {"10.1/a"}


class TestParseOpenalexWork:
    def test_extracts_fields(self):
        work = {
            "doi": "https://doi.org/10.1234/test",
            "title": "Test Paper",
            "authorships": [{"author": {"display_name": "Alice Smith"}}],
            "publication_date": "2026-03-15",
            "cited_by_count": 5,
            "open_access": {"oa_url": "https://arxiv.org/abs/123"},
            "abstract_inverted_index": {"Hello": [0], "world": [1]},
        }
        result = _parse_openalex_work(work)
        assert result["doi"] == "10.1234/test"
        assert result["title"] == "Test Paper"
        assert result["authors"] == ["Alice Smith"]
        assert result["cited_by_count"] == 5
        assert result["open_access_url"] == "https://arxiv.org/abs/123"
        assert result["abstract"] == "Hello world"

    def test_open_access_null(self):
        work = {
            "doi": "https://doi.org/10.1/x",
            "title": "Paper",
            "authorships": [],
            "open_access": None,
        }
        result = _parse_openalex_work(work)
        assert result["open_access_url"] is None

    def test_missing_doi_returns_none(self):
        work = {"title": "No DOI Paper", "authorships": []}
        assert _parse_openalex_work(work) is None

    def test_missing_title_returns_none(self):
        work = {"doi": "https://doi.org/10.1/x", "authorships": []}
        assert _parse_openalex_work(work) is None

    def test_empty_doi_returns_none(self):
        work = {"doi": "", "title": "Paper", "authorships": []}
        assert _parse_openalex_work(work) is None


class TestReconstructAbstract:
    def test_happy_path(self):
        idx = {"The": [0], "quick": [1], "brown": [2], "fox": [3]}
        assert _reconstruct_abstract(idx) == "The quick brown fox"

    def test_none_input(self):
        assert _reconstruct_abstract(None) == ""

    def test_empty_dict(self):
        assert _reconstruct_abstract({}) == ""

    def test_out_of_bounds_position_ignored(self):
        idx = {"hello": [0], "world": [1], "spam": [60000]}
        result = _reconstruct_abstract(idx)
        assert "spam" not in result
        assert result == "hello world"

    def test_non_integer_position_ignored(self):
        idx = {"hello": [0], "bad": ["not_int"], "world": [1]}
        result = _reconstruct_abstract(idx)
        assert result == "hello world"

    def test_duplicate_positions(self):
        idx = {"a": [0], "b": [0]}
        result = _reconstruct_abstract(idx)
        assert len(result.split()) == 2


class TestDiscoverPapers:
    @respx.mock
    def test_returns_parsed_papers(self):
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json={
                "results": [{
                    "doi": "https://doi.org/10.1/new",
                    "title": "New Paper",
                    "authorships": [{"author": {"display_name": "Alice"}}],
                    "publication_date": "2026-03-20",
                    "cited_by_count": 0,
                    "open_access": None,
                    "abstract_inverted_index": None,
                }]
            })
        )
        interests = {"top_authors": ["Alice"], "shared_dois": set()}
        papers = discover_papers(interests, days=30)
        assert len(papers) == 1
        assert papers[0]["doi"] == "10.1/new"

    @respx.mock
    def test_excludes_shared_dois(self):
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json={
                "results": [{
                    "doi": "https://doi.org/10.1/already-shared",
                    "title": "Old Paper",
                    "authorships": [],
                    "publication_date": "2026-03-20",
                    "cited_by_count": 0,
                    "open_access": None,
                    "abstract_inverted_index": None,
                }]
            })
        )
        interests = {"top_authors": ["Alice"], "shared_dois": {"10.1/already-shared"}}
        papers = discover_papers(interests, days=30)
        assert len(papers) == 0

    @respx.mock
    def test_deduplicates_by_doi(self):
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"doi": "https://doi.org/10.1/dup", "title": "Paper A",
                     "authorships": [], "publication_date": "", "cited_by_count": 0,
                     "open_access": None, "abstract_inverted_index": None},
                    {"doi": "https://doi.org/10.1/dup", "title": "Paper A copy",
                     "authorships": [], "publication_date": "", "cited_by_count": 0,
                     "open_access": None, "abstract_inverted_index": None},
                ]
            })
        )
        interests = {"top_authors": ["Alice"], "shared_dois": set()}
        papers = discover_papers(interests, days=30)
        assert len(papers) == 1

    @respx.mock
    def test_429_logs_warning_continues(self):
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(429)
        )
        interests = {"top_authors": ["Alice"], "shared_dois": set()}
        messages = []
        from loguru import logger
        sink_id = logger.add(lambda m: messages.append(m.record["message"]))
        papers = discover_papers(interests, days=30)
        logger.remove(sink_id)
        assert len(papers) == 0
        assert any("rate limited" in m for m in messages)

    def test_empty_top_authors_returns_empty(self):
        interests = {"top_authors": [], "shared_dois": set()}
        assert discover_papers(interests) == []
