"""Tests for scholarposter.discovery — citation graph traversal (US-014).

Replaces the old author-frequency discovery tests (extract_interests,
discover_papers). All tests use respx for deterministic httpx mocking.
"""
from __future__ import annotations

import json
import os
from typing import Optional
from unittest.mock import patch

import httpx
import pytest
import respx
from typer.testing import CliRunner

from scholarposter.cli import app
from scholarposter.config import DiscoveryConfig
from scholarposter.discovery import CandidatePaper
from scholarposter.discovery.graph import (
    cited_by,
    cites,
    co_cited,
    resolve_doi_to_openalex_id,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_EMAIL = "test@example.com"
_BASE = "https://api.openalex.org"

_DEFAULT_CFG = DiscoveryConfig(limit=10)


def _make_work(
    doi: str = "10.1/x",
    title: str = "Test Paper",
    year: int = 2024,
    cited_by_count: int = 5,
    is_oa: bool = True,
    openalex_id: str = "W123",
    referenced_works: Optional[list[str]] = None,
) -> dict:
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "publication_date": f"{year}-06-01",
        "cited_by_count": cited_by_count,
        "open_access": {"is_oa": is_oa},
        "referenced_works": referenced_works or [],
        "authorships": [],
        "abstract_inverted_index": None,
    }


def _work_list_response(works: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"results": works, "meta": {"count": len(works)}})


# ---------------------------------------------------------------------------
# CandidatePaper
# ---------------------------------------------------------------------------

class TestCandidatePaper:
    def test_dataclass_fields(self):
        p = CandidatePaper(
            doi="10.1/x",
            openalex_id="W123",
            title="Test",
            year=2024,
            cited_by_count=5,
            is_oa=True,
            source="openalex",
            mode="cited-by",
            age_years=1.5,
        )
        assert p.doi == "10.1/x"
        assert p.openalex_id == "W123"
        assert p.mode == "cited-by"

    def test_optional_year(self):
        p = CandidatePaper(
            doi="10.1/y", openalex_id="W9", title="T", year=None,
            cited_by_count=0, is_oa=False, source="openalex", mode="cites", age_years=0.0,
        )
        assert p.year is None


# ---------------------------------------------------------------------------
# resolve_doi_to_openalex_id
# ---------------------------------------------------------------------------

class TestResolveDoi:
    @respx.mock
    def test_successful_resolution(self):
        work = _make_work(doi="10.1/seed", openalex_id="W999")
        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=work)
        )
        client = httpx.Client()
        result = resolve_doi_to_openalex_id("10.1/seed", _EMAIL, client)
        assert result == "W999"

    @respx.mock
    def test_404_returns_none(self):
        respx.get(f"{_BASE}/works/https://doi.org/10.1/unknown").mock(
            return_value=httpx.Response(404)
        )
        client = httpx.Client()
        result = resolve_doi_to_openalex_id("10.1/unknown", _EMAIL, client)
        assert result is None

    @respx.mock
    def test_network_exception_returns_none(self):
        respx.get(f"{_BASE}/works/https://doi.org/10.1/bad").mock(
            side_effect=httpx.ConnectError("unreachable")
        )
        client = httpx.Client()
        result = resolve_doi_to_openalex_id("10.1/bad", _EMAIL, client)
        assert result is None

    @respx.mock
    def test_etiquette_email_injected_as_user_agent(self):
        """User-Agent header contains email for OpenAlex polite pool."""
        work = _make_work(doi="10.1/ua", openalex_id="W1")
        route = respx.get(f"{_BASE}/works/https://doi.org/10.1/ua").mock(
            return_value=httpx.Response(200, json=work)
        )
        client = httpx.Client()
        resolve_doi_to_openalex_id("10.1/ua", "polite@org.edu", client)
        request = route.calls[0].request
        assert "polite@org.edu" in request.headers.get("user-agent", "")

    @respx.mock
    def test_rn_stripped_from_etiquette_email(self):
        """\\r\\n in etiquette_email stripped before User-Agent — prevents CRLF injection."""
        work = _make_work(doi="10.1/inject", openalex_id="W1")
        route = respx.get(f"{_BASE}/works/https://doi.org/10.1/inject").mock(
            return_value=httpx.Response(200, json=work)
        )
        client = httpx.Client()
        resolve_doi_to_openalex_id("10.1/inject", "evil\r\nX-Injected: yes", client)
        request = route.calls[0].request
        ua = request.headers.get("user-agent", "")
        # CRLF stripped → no header injection possible
        assert "\r" not in ua
        assert "\n" not in ua

    @respx.mock
    def test_cache_hit_skips_network(self, tmp_path):
        """Cache hit on second call within TTL → HTTP not called again."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        work = _make_work(doi="10.1/cached", openalex_id="W777")
        route = respx.get(f"{_BASE}/works/https://doi.org/10.1/cached").mock(
            return_value=httpx.Response(200, json=work)
        )
        client = httpx.Client()
        r1 = resolve_doi_to_openalex_id("10.1/cached", _EMAIL, client, cache=cache)
        r2 = resolve_doi_to_openalex_id("10.1/cached", _EMAIL, client, cache=cache)
        assert r1 == r2 == "W777"
        assert route.call_count == 1  # second call served from cache

    @respx.mock
    def test_cache_miss_then_store(self, tmp_path):
        """Cache miss → HTTP called → result stored; subsequent get returns value."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        work = _make_work(doi="10.1/store", openalex_id="W888")
        respx.get(f"{_BASE}/works/https://doi.org/10.1/store").mock(
            return_value=httpx.Response(200, json=work)
        )
        client = httpx.Client()
        resolve_doi_to_openalex_id("10.1/store", _EMAIL, client, cache=cache, cache_ttl_hours=24)
        cached = cache.get("10.1/store")
        assert cached is not None
        assert cached["openalex_id"] == "W888"

    @respx.mock
    def test_404_not_stored_in_cache(self, tmp_path):
        """404 response → None returned; nothing written to cache."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        respx.get(f"{_BASE}/works/https://doi.org/10.1/missing").mock(
            return_value=httpx.Response(404)
        )
        client = httpx.Client()
        resolve_doi_to_openalex_id("10.1/missing", _EMAIL, client, cache=cache)
        assert cache.get("10.1/missing") is None


# ---------------------------------------------------------------------------
# cited_by (T-20)
# ---------------------------------------------------------------------------

class TestCitedBy:
    @respx.mock
    def test_t20_basic_cited_by(self):
        """T-20: cited-by mode; DOI→OpenAlex ID resolution happens first."""
        seed_doi = "10.1/seed"
        seed_oa_id = "W100"
        seed_work = _make_work(doi=seed_doi, openalex_id=seed_oa_id)
        new_paper = _make_work(doi="10.1/new", title="New Paper", openalex_id="W200")

        # resolve call
        respx.get(f"{_BASE}/works/https://doi.org/{seed_doi}").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        # filter=cites:W100
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([new_paper]))

        client = httpx.Client()
        result = cited_by([seed_doi], _DEFAULT_CFG, _EMAIL, client, bibliography_dois=set())

        assert len(result) == 1
        assert result[0].doi == "10.1/new"
        assert result[0].mode == "cited-by"

    @respx.mock
    def test_t23_bibliography_doi_excluded(self):
        """T-23: DOI already in bibliography never returned as candidate."""
        seed_doi = "10.1/seed"
        seed_work = _make_work(doi=seed_doi, openalex_id="W100")
        already_shared = _make_work(doi="10.1/already", openalex_id="W200")

        respx.get(f"{_BASE}/works/https://doi.org/{seed_doi}").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(
            return_value=_work_list_response([already_shared])
        )

        client = httpx.Client()
        result = cited_by(
            [seed_doi], _DEFAULT_CFG, _EMAIL, client,
            bibliography_dois={"10.1/already"},
        )
        assert len(result) == 0

    @respx.mock
    def test_resolve_failure_skips_doi(self):
        """DOI that can't be resolved to OpenAlex ID is skipped gracefully."""
        respx.get(f"{_BASE}/works/https://doi.org/10.1/x").mock(
            return_value=httpx.Response(404)
        )
        client = httpx.Client()
        result = cited_by(["10.1/x"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []

    @respx.mock
    def test_t24_timeout_returns_empty(self):
        """T-24: network timeout → returns empty; no crash."""
        respx.get(f"{_BASE}/works/https://doi.org/10.1/slow").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        client = httpx.Client()
        result = cited_by(["10.1/slow"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []

    @respx.mock
    def test_deduplicates_across_seeds(self):
        """Same DOI returned by two seeds → appears only once in result."""
        for seed_doi, oa_id in [("10.1/a", "W1"), ("10.1/b", "W2")]:
            respx.get(f"{_BASE}/works/https://doi.org/{seed_doi}").mock(
                return_value=httpx.Response(200, json=_make_work(doi=seed_doi, openalex_id=oa_id))
            )
        dup_paper = _make_work(doi="10.1/dup", openalex_id="W99")
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([dup_paper]))

        client = httpx.Client()
        result = cited_by(["10.1/a", "10.1/b"], _DEFAULT_CFG, _EMAIL, client)
        assert len(result) == 1

    @respx.mock
    def test_api_error_continues_to_next_seed(self):
        """Source failure → WARNING logged; other seeds still processed."""
        bad = "10.1/bad"
        good = "10.1/good"
        new_paper = _make_work(doi="10.1/new", openalex_id="W50")

        respx.get(f"{_BASE}/works/https://doi.org/{bad}").mock(
            side_effect=httpx.ConnectError("fail")
        )
        respx.get(f"{_BASE}/works/https://doi.org/{good}").mock(
            return_value=httpx.Response(200, json=_make_work(doi=good, openalex_id="W10"))
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([new_paper]))

        client = httpx.Client()
        result = cited_by([bad, good], _DEFAULT_CFG, _EMAIL, client)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# cites (T-21)
# ---------------------------------------------------------------------------

class TestCites:
    @respx.mock
    def test_t21_cites_no_crossref(self):
        """T-21: cites mode fetches referenced_works; no Crossref API called."""
        seed_doi = "10.1/seed"
        ref_paper = _make_work(doi="10.1/ref", openalex_id="W300")
        seed_work = _make_work(
            doi=seed_doi,
            openalex_id="W100",
            referenced_works=["https://openalex.org/W300"],
        )

        respx.get(f"{_BASE}/works/https://doi.org/{seed_doi}").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        # batch fetch by IDs
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([ref_paper]))

        # Ensure no Crossref URL was called
        crossref_called = []
        respx.get("https://api.crossref.org/works/").mock(
            side_effect=lambda r: crossref_called.append(r) or httpx.Response(200, json={})
        )

        client = httpx.Client()
        result = cites([seed_doi], _DEFAULT_CFG, _EMAIL, client)

        assert len(result) == 1
        assert result[0].doi == "10.1/ref"
        assert result[0].mode == "cites"
        assert crossref_called == []  # T-21: no Crossref

    @respx.mock
    def test_empty_referenced_works(self):
        seed_work = _make_work(doi="10.1/empty", openalex_id="W1", referenced_works=[])
        respx.get(f"{_BASE}/works/https://doi.org/10.1/empty").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        client = httpx.Client()
        result = cites(["10.1/empty"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []

    @respx.mock
    def test_404_seed_returns_empty(self):
        respx.get(f"{_BASE}/works/https://doi.org/10.1/missing").mock(
            return_value=httpx.Response(404)
        )
        client = httpx.Client()
        result = cites(["10.1/missing"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []

    @respx.mock
    def test_bibliography_doi_excluded_from_cites(self):
        ref_doi = "10.1/ref"
        seed_work = _make_work(
            doi="10.1/seed", openalex_id="W1",
            referenced_works=["https://openalex.org/W300"],
        )
        ref_paper = _make_work(doi=ref_doi, openalex_id="W300")

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([ref_paper]))

        client = httpx.Client()
        result = cites(
            ["10.1/seed"], _DEFAULT_CFG, _EMAIL, client,
            bibliography_dois={ref_doi},
        )
        assert result == []

    @respx.mock
    def test_timeout_returns_empty(self):
        respx.get(f"{_BASE}/works/https://doi.org/10.1/slow").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        client = httpx.Client()
        result = cites(["10.1/slow"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []


# ---------------------------------------------------------------------------
# co_cited
# ---------------------------------------------------------------------------

class TestCoCited:
    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Phase 6"):
            co_cited(["10.1/x"], _DEFAULT_CFG, _EMAIL, None)


# ---------------------------------------------------------------------------
# DiscoveryCache
# ---------------------------------------------------------------------------

class TestDiscoveryCache:
    def test_cache_miss_returns_none(self, tmp_path):
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        assert cache.get("10.1/x") is None

    def test_cache_set_and_get(self, tmp_path):
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=24)
        result = cache.get("10.1/x")
        assert result is not None
        assert result["openalex_id"] == "W1"

    def test_expired_entry_returns_none(self, tmp_path):
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=0)
        # TTL=0 means already expired
        assert cache.get("10.1/x") is None

    def test_cache_file_is_0o600(self, tmp_path):
        import stat
        from scholarposter.discovery.cache import DiscoveryCache
        cache_file = tmp_path / "discovery_cache.json"
        cache = DiscoveryCache(cache_file)
        cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=24)
        mode = stat.S_IMODE(cache_file.stat().st_mode)
        assert mode == 0o600

    def test_prune_removes_expired_on_read(self, tmp_path):
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "discovery_cache.json")
        cache.set("10.1/expired", {"openalex_id": "W1"}, ttl_hours=0)
        cache.set("10.1/fresh", {"openalex_id": "W2"}, ttl_hours=24)
        # Both are in file; after prune, only fresh remains
        assert cache.get("10.1/expired") is None
        assert cache.get("10.1/fresh") is not None

    def test_missing_file_returns_none(self, tmp_path):
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "nonexistent.json")
        assert cache.get("10.1/any") is None


# ---------------------------------------------------------------------------
# discover CLI subcommand
# ---------------------------------------------------------------------------

cli_runner = CliRunner()

_DISC_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
    "\n"
    "[discovery]\n"
    "enabled = true\n"
    "\n"
    "[enrichment.crossref]\n"
    'etiquette_email = "test@test.com"\n'
)

_NODISC_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
)


def _make_bib(dois: list[str]) -> list[dict]:
    return [{"doi": d, "title": "T", "authors": ["A"]} for d in dois]


class TestDiscoverCmd:
    def test_no_bibliography_exits_1(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        # state.json without bibliography
        (tmp_path / "state.json").write_text("{}")
        result = cli_runner.invoke(app, ["discover", "--config", str(p)])
        assert result.exit_code == 1

    @respx.mock
    def test_mode_cited_by(self, tmp_path):
        """--mode cited-by calls cited_by(); results displayed."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        seed_work = _make_work(doi="10.1/seed", openalex_id="W1")
        new_paper = _make_work(doi="10.1/new", title="Fresh Paper", openalex_id="W2",
                               year=2025, cited_by_count=3, is_oa=True)

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([new_paper]))

        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "cited-by"]
        )
        assert result.exit_code == 0
        assert "Fresh Paper" in result.output

    @respx.mock
    def test_mode_cites(self, tmp_path):
        """--mode cites calls cites()."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        seed_work = _make_work(
            doi="10.1/seed", openalex_id="W1",
            referenced_works=["https://openalex.org/W300"],
        )
        ref_paper = _make_work(doi="10.1/ref", title="Referenced Paper", openalex_id="W300",
                               year=2020, cited_by_count=100)

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([ref_paper]))

        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "cites"]
        )
        assert result.exit_code == 0
        assert "Referenced Paper" in result.output

    def test_mode_co_cited_exits_0_with_message(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))
        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "co-cited"]
        )
        assert result.exit_code == 0
        assert "Phase 6" in result.output or "not yet implemented" in result.output

    @respx.mock
    def test_mode_all_skips_co_cited(self, tmp_path):
        """--mode all runs cited-by + cites; logs co-cited skipped."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        seed_work = _make_work(doi="10.1/seed", openalex_id="W1",
                               referenced_works=["https://openalex.org/W99"])
        paper_cited = _make_work(doi="10.1/c1", title="CitedBy", openalex_id="W2")
        paper_cites = _make_work(doi="10.1/c2", title="Cites", openalex_id="W99")

        # cited_by: resolve seed + filter
        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(
            return_value=_work_list_response([paper_cited, paper_cites])
        )

        messages = []
        from loguru import logger
        sid = logger.add(lambda m: messages.append(m.record["message"]))

        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "all"]
        )
        logger.remove(sid)

        assert result.exit_code == 0
        assert any("co-cited" in m and ("skip" in m or "Phase 6" in m) for m in messages)

    @respx.mock
    def test_t24_timeout_exits_0_no_results(self, tmp_path):
        """T-24: OpenAlex timeout → exits 0; shows 'no new papers' message."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            side_effect=httpx.TimeoutException("timeout")
        )

        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "cited-by"]
        )
        assert result.exit_code == 0

    def test_days_deprecated_warning(self, tmp_path):
        """--days emits deprecation warning to stderr."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        with patch("scholarposter.discovery.graph.cited_by", return_value=[]), \
             patch("scholarposter.discovery.graph.cites", return_value=[]):
            result = cli_runner.invoke(
                app, ["discover", "--config", str(p), "--days", "30", "--mode", "cited-by"],
                catch_exceptions=False,
            )
        # --days should emit deprecation warning
        assert "deprecated" in (result.output + (result.stderr or "")).lower()

    def test_invalid_mode_exits_1(self, tmp_path):
        """Lines 775-777: unknown --mode value → exit 1."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))
        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "typo-mode"]
        )
        assert result.exit_code == 1
        assert "Unknown" in result.output or "Unknown" in (result.stderr or "")

    def test_invalid_since_date_exits_2(self, tmp_path):
        """Lines 801-803: invalid --since date → exit 2."""
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))
        with patch("scholarposter.discovery.graph.cited_by", return_value=[]):
            result = cli_runner.invoke(
                app, ["discover", "--config", str(p), "--mode", "cited-by",
                       "--since", "bad-date"]
            )
        assert result.exit_code == 2

    @respx.mock
    def test_json_output_flag(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = _make_bib(["10.1/seed"])
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        seed_work = _make_work(doi="10.1/seed", openalex_id="W1")
        paper = _make_work(doi="10.1/new", title="JSON Paper", openalex_id="W2")

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list_response([paper]))

        result = cli_runner.invoke(
            app, ["discover", "--config", str(p), "--mode", "cited-by", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(p["doi"] == "10.1/new" for p in data)


# ---------------------------------------------------------------------------
# Coverage — internal helpers and error branches
# ---------------------------------------------------------------------------

class TestGraphInternals:
    """Direct-call tests to cover error branches in graph.py helpers."""

    def test_parse_year_invalid_prefix_returns_none(self):
        from scholarposter.discovery.graph import _parse_year
        work = {"publication_date": "abcd-01-01"}
        assert _parse_year(work) is None

    def test_parse_year_short_date_returns_none(self):
        from scholarposter.discovery.graph import _parse_year
        work = {"publication_date": "202"}  # < 4 chars
        assert _parse_year(work) is None

    def test_compute_age_years_none_returns_zero(self):
        from scholarposter.discovery.graph import _compute_age_years
        assert _compute_age_years(None) == 0.0

    def test_parse_to_candidate_missing_doi_returns_none(self):
        from scholarposter.discovery.graph import _parse_to_candidate
        work = {"id": "https://openalex.org/W1", "title": "T", "doi": ""}
        assert _parse_to_candidate(work, "cited-by") is None

    def test_parse_to_candidate_missing_title_returns_none(self):
        from scholarposter.discovery.graph import _parse_to_candidate
        work = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x"}
        assert _parse_to_candidate(work, "cites") is None

    @respx.mock
    def test_cited_by_filter_nonhttp200_logs_warning(self):
        """Lines 141-142: filter call returns 429 → warning + continue."""
        seed_work = _make_work(doi="10.1/seed", openalex_id="W1")
        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=httpx.Response(429))

        client = httpx.Client()
        result = cited_by(["10.1/seed"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []

    @respx.mock
    def test_cited_by_filter_raises_exception(self):
        """Lines 147-148: exception from filter call caught; returns empty."""
        seed_work = _make_work(doi="10.1/seed", openalex_id="W1")
        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(side_effect=httpx.ConnectError("fail"))

        client = httpx.Client()
        result = cited_by(["10.1/seed"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []

    @respx.mock
    def test_cites_batch_fetch_nonhttp200(self):
        """Lines 198-199: batch fetch returns 500 → warning + continue."""
        seed_work = _make_work(
            doi="10.1/seed", openalex_id="W1",
            referenced_works=["https://openalex.org/W300"],
        )
        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=httpx.Response(500))

        client = httpx.Client()
        result = cites(["10.1/seed"], _DEFAULT_CFG, _EMAIL, client)
        assert result == []


class TestCacheInternals:
    """Tests for error branches in DiscoveryCache."""

    def test_get_corrupt_entry_returns_none(self, tmp_path):
        """Lines 33-34: entry with bad expires_at returns None."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"10.1/x": {"value": {}, "expires_at": "not-a-date"}}))
        cache = DiscoveryCache(cache_file)
        assert cache.get("10.1/x") is None

    def test_read_corrupt_json_returns_empty(self, tmp_path):
        """Lines 60-61: corrupt JSON file → _read returns {}."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache_file = tmp_path / "cache.json"
        cache_file.write_bytes(b"{corrupt json")
        cache = DiscoveryCache(cache_file)
        assert cache.get("10.1/x") is None

    def test_write_lock_contention_skips_write(self, tmp_path):
        """Lines 80-82: LOCK_NB raises OSError → write skipped; no crash."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "cache.json")
        with patch("fcntl.flock", side_effect=OSError("busy")):
            cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=24)
        # Write was skipped — file should not exist
        assert not (tmp_path / "cache.json").exists()

    def test_lock_contention_close_also_fails(self, tmp_path):
        """Lines 84-85: flock raises AND os.close raises → error swallowed; no crash."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "cache.json")

        def close_that_fails_on_lock(fd):
            # Fail the first close (lock FD on contention path)
            raise OSError("close fail")

        with patch("fcntl.flock", side_effect=OSError("busy")), \
             patch("scholarposter.discovery.cache.os.close", side_effect=close_that_fails_on_lock):
            cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=24)
        # No crash; file was not created
        assert not (tmp_path / "cache.json").exists()

    def test_lock_open_failure_skips_write(self, tmp_path):
        """Lines 83-85: os.open for lock file fails → write skipped."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "cache.json")
        with patch("scholarposter.discovery.cache.os.open", side_effect=OSError("no perms")):
            cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=24)
        assert not (tmp_path / "cache.json").exists()

    def test_write_failure_logged(self, tmp_path):
        """Lines 101-106: os.write failure → logged; no crash."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "cache.json")
        real_open = os.open

        call_count = [0]

        def selective_fail(path, flags, mode=0o666):
            call_count[0] += 1
            fd = real_open(path, flags, mode)
            if call_count[0] == 2:  # second os.open = tmp file
                os.close(fd)
                raise OSError("disk full")
            return fd

        with patch("scholarposter.discovery.cache.os.open", side_effect=selective_fail):
            cache.set("10.1/x", {"openalex_id": "W1"}, ttl_hours=24)
        # Write failed — no cache file written
        assert not (tmp_path / "cache.json").exists()

    def test_write_failure_and_unlink_fails(self, tmp_path):
        """Lines 105-106: os.unlink also fails in write-error cleanup; error swallowed."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "cache.json")
        # os.open raises on the tmp file write attempt
        real_open = os.open
        call_count = [0]

        def fail_second(path, flags, mode=0o666):
            call_count[0] += 1
            if call_count[0] == 2:
                raise OSError("disk full")
            return real_open(path, flags, mode)

        with patch("scholarposter.discovery.cache.os.open", side_effect=fail_second), \
             patch("scholarposter.discovery.cache.os.unlink", side_effect=OSError("busy")):
            cache.set("10.1/x", {"v": 1}, ttl_hours=24)
        # Both write and unlink failed; file was not created
        assert not (tmp_path / "cache.json").exists()

    def test_finally_oserror_swallowed(self, tmp_path):
        """Lines 111-112: lock release failure is swallowed."""
        from scholarposter.discovery.cache import DiscoveryCache
        cache = DiscoveryCache(tmp_path / "cache.json")
        # First flock = acquire (succeed), second = release (fail)
        with patch("fcntl.flock", side_effect=[None, OSError("release fail")]):
            cache.set("10.1/x", {"v": 1}, ttl_hours=24)
        # Write completed despite lock release failure
        assert (tmp_path / "cache.json").exists()
