"""Tests for scholarposter.discovery.ranking and .digest (WU-6, US-014).

TDD-first: all tests written RED before ranking.py or digest.py exist.
"""
from __future__ import annotations

import json
import math
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import respx
import httpx
from typer.testing import CliRunner

from scholarposter.config import DiscoveryConfig, DiscoveryRankingConfig
from scholarposter.discovery import CandidatePaper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _paper(
    doi: str = "10.1/x",
    cited_by_count: int = 10,
    age_years: float = 2.0,
    is_oa: bool = False,
    mode: str = "cited-by",
    year: int = 2023,
    title: str = "",
) -> CandidatePaper:
    return CandidatePaper(
        doi=doi,
        openalex_id="W1",
        title=title or f"Paper {doi}",
        year=year,
        cited_by_count=cited_by_count,
        is_oa=is_oa,
        source="openalex",
        mode=mode,
        age_years=age_years,
    )


_DEFAULT_RANKING = DiscoveryRankingConfig(oa_weight=1.2, recency_half_life_years=2.0)


# ---------------------------------------------------------------------------
# T-25b: score() formula
# ---------------------------------------------------------------------------

class TestScore:
    def test_velocity_times_oa_times_recency(self):
        """score = (cited_by_count / max(1, age)) * oa_factor * exp(-ln(2) * age / half_life)"""
        from scholarposter.discovery.ranking import score
        p = _paper(cited_by_count=10, age_years=2.0, is_oa=False)
        expected = (10.0 / 2.0) * 1.0 * math.exp(-math.log(2) * 2.0 / 2.0)
        assert abs(score(p, _DEFAULT_RANKING) - expected) < 1e-9

    def test_oa_weight_applied(self):
        """OA paper (oa_weight=1.2) ranks above non-OA with identical citation velocity."""
        from scholarposter.discovery.ranking import score
        p_oa = _paper(cited_by_count=10, age_years=2.0, is_oa=True)
        p_non_oa = _paper(cited_by_count=10, age_years=2.0, is_oa=False)
        assert score(p_oa, _DEFAULT_RANKING) > score(p_non_oa, _DEFAULT_RANKING)
        ratio = score(p_oa, _DEFAULT_RANKING) / score(p_non_oa, _DEFAULT_RANKING)
        assert abs(ratio - 1.2) < 1e-9

    def test_recency_4yr_lower_than_1yr_same_velocity(self):
        """4-year-old paper scores lower than 1-year-old with same citation velocity.
        velocity_recent = 10/1 = 10; recency = exp(-0.693*1/2) ≈ 0.707
        velocity_old   = 40/4 = 10; recency = exp(-0.693*4/2) ≈ 0.25
        """
        from scholarposter.discovery.ranking import score
        p_recent = _paper(cited_by_count=10, age_years=1.0, is_oa=False)
        p_old = _paper(cited_by_count=40, age_years=4.0, is_oa=False)
        assert score(p_recent, _DEFAULT_RANKING) > score(p_old, _DEFAULT_RANKING)

    def test_age_zero_clamps_to_one(self):
        """age_years=0 → denominator clamped to max(1, 0)=1; no ZeroDivisionError."""
        from scholarposter.discovery.ranking import score
        p = _paper(cited_by_count=10, age_years=0.0, is_oa=False)
        assert score(p, _DEFAULT_RANKING) > 0

    def test_oa_weight_1_equals_non_oa(self):
        """oa_weight=1.0 → OA and non-OA score identically."""
        from scholarposter.discovery.ranking import score
        cfg = DiscoveryRankingConfig(oa_weight=1.0)
        p_oa = _paper(cited_by_count=10, age_years=2.0, is_oa=True)
        p_non = _paper(cited_by_count=10, age_years=2.0, is_oa=False)
        assert abs(score(p_oa, cfg) - score(p_non, cfg)) < 1e-12


# ---------------------------------------------------------------------------
# T-25b: rank()
# ---------------------------------------------------------------------------

class TestRank:
    def test_returns_sorted_by_score_descending(self):
        from scholarposter.discovery.ranking import rank
        high = _paper(doi="10.1/h", cited_by_count=100, age_years=1.0)
        low = _paper(doi="10.1/l", cited_by_count=1, age_years=10.0)
        mid = _paper(doi="10.1/m", cited_by_count=20, age_years=2.0)
        result = rank([low, high, mid], _DEFAULT_RANKING, limit=3)
        assert result[0].doi == "10.1/h"
        assert len(result) == 3

    def test_dedup_keeps_highest_scoring_copy(self):
        """Same DOI from cited-by and cites → single entry; higher-scoring copy wins."""
        from scholarposter.discovery.ranking import rank
        p_low = _paper(doi="10.1/dup", cited_by_count=10, age_years=2.0, mode="cited-by")
        p_high = _paper(doi="10.1/dup", cited_by_count=50, age_years=2.0, mode="cites")
        result = rank([p_low, p_high], _DEFAULT_RANKING, limit=10)
        assert len(result) == 1
        assert result[0].cited_by_count == 50

    def test_limit_caps_output(self):
        from scholarposter.discovery.ranking import rank
        papers = [_paper(doi=f"10.1/{i}", cited_by_count=i) for i in range(20)]
        result = rank(papers, _DEFAULT_RANKING, limit=5)
        assert len(result) == 5

    def test_empty_input(self):
        from scholarposter.discovery.ranking import rank
        assert rank([], _DEFAULT_RANKING, limit=10) == []

    def test_limit_zero_returns_empty(self):
        from scholarposter.discovery.ranking import rank
        assert rank([_paper()], _DEFAULT_RANKING, limit=0) == []

    def test_oa_paper_beats_non_oa_when_otherwise_equal(self):
        """OA wins tie-break for same citation velocity."""
        from scholarposter.discovery.ranking import rank
        non_oa = _paper(doi="10.1/no", cited_by_count=10, age_years=1.0, is_oa=False)
        oa = _paper(doi="10.1/oa", cited_by_count=10, age_years=1.0, is_oa=True)
        result = rank([non_oa, oa], _DEFAULT_RANKING, limit=2)
        assert result[0].doi == "10.1/oa"


# ---------------------------------------------------------------------------
# format_table
# ---------------------------------------------------------------------------

class TestFormatTable:
    def test_doi_appears_in_output(self):
        from scholarposter.discovery.digest import format_table
        p = _paper(doi="10.1234/test")
        assert "10.1234/test" in format_table([p])

    def test_title_truncated_at_40_chars(self):
        from scholarposter.discovery.digest import format_table
        long_title = "A" * 60
        p = CandidatePaper(
            doi="10.1/x", openalex_id="W1", title=long_title, year=2024,
            cited_by_count=5, is_oa=False, source="openalex", mode="cited-by", age_years=1.0,
        )
        output = format_table([p])
        assert "…" in output
        assert long_title not in output

    def test_wide_no_truncation(self):
        from scholarposter.discovery.digest import format_table
        long_title = "A" * 60
        p = CandidatePaper(
            doi="10.1/x", openalex_id="W1", title=long_title, year=2024,
            cited_by_count=5, is_oa=False, source="openalex", mode="cited-by", age_years=1.0,
        )
        assert long_title in format_table([p], wide=True)

    def test_oa_tag(self):
        from scholarposter.discovery.digest import format_table
        assert "[OA]" in format_table([_paper(is_oa=True)])

    def test_missing_year_placeholder(self):
        from scholarposter.discovery.digest import format_table
        p = CandidatePaper(
            doi="10.1/x", openalex_id="W1", title="T", year=None,
            cited_by_count=0, is_oa=False, source="openalex", mode="cited-by", age_years=0.0,
        )
        assert "????" in format_table([p])

    def test_empty_list_returns_empty_string(self):
        from scholarposter.discovery.digest import format_table
        assert format_table([]) == ""


# ---------------------------------------------------------------------------
# T-25: send_digest
# ---------------------------------------------------------------------------

class TestSendDigest:
    def _disc_cfg(self, email: str = "user@example.com") -> DiscoveryConfig:
        return DiscoveryConfig(enabled=True, digest_email=email)

    def _fake_smtp_class(self, captured: list):
        class FakeSMTP:
            def __init__(self, host, port, timeout=10):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def send_message(self, msg):
                captured.append(msg)
        return FakeSMTP

    def test_subject_matches_fr88(self):
        """T-25: subject = 'scholarposter discovery digest — {date}: {N} new candidates'"""
        from scholarposter.discovery.digest import send_digest
        papers = [_paper(doi="10.1/a"), _paper(doi="10.1/b")]
        cfg = self._disc_cfg()
        today = date(2026, 4, 7)
        captured = []

        with patch("smtplib.SMTP", self._fake_smtp_class(captured)):
            send_digest(papers, cfg, today)

        assert len(captured) == 1
        expected = f"scholarposter discovery digest — {today}: {len(papers)} new candidates"
        assert captured[0]["Subject"] == expected

    def test_to_address_from_digest_email(self):
        from scholarposter.discovery.digest import send_digest
        cfg = self._disc_cfg("researcher@university.edu")
        captured = []
        with patch("smtplib.SMTP", self._fake_smtp_class(captured)):
            send_digest([_paper()], cfg, date(2026, 4, 7))
        assert "researcher@university.edu" in captured[0]["To"]

    def test_body_contains_table(self):
        from scholarposter.discovery.digest import send_digest
        p = _paper(doi="10.1234/bodycheck")
        captured = []
        with patch("smtplib.SMTP", self._fake_smtp_class(captured)):
            send_digest([p], self._disc_cfg(), date(2026, 4, 7))
        assert "10.1234/bodycheck" in captured[0].get_payload()

    def test_invalid_email_raises_before_smtp(self):
        """send_digest raises ValueError before opening any SMTP connection.
        Empty string → parseaddr returns empty to_addr → ValueError.
        """
        from scholarposter.discovery.digest import send_digest
        cfg = MagicMock()
        cfg.digest_email = ""
        smtp_opened = []
        with patch("smtplib.SMTP", side_effect=lambda *a, **k: smtp_opened.append(1) or MagicMock()):
            with pytest.raises(ValueError, match="digest_email"):
                send_digest([_paper()], cfg, date(2026, 4, 7))
        assert smtp_opened == []  # SMTP never opened

    def test_no_digest_email_raises(self):
        """Empty/None digest_email → ValueError."""
        from scholarposter.discovery.digest import send_digest
        cfg = MagicMock()
        cfg.digest_email = None
        with pytest.raises(ValueError, match="digest_email"):
            send_digest([_paper()], cfg, date(2026, 4, 7))

    def test_custom_smtp_host_and_port(self):
        """Custom smtp_host/smtp_port kwargs are forwarded to smtplib.SMTP."""
        from scholarposter.discovery.digest import send_digest
        cfg = self._disc_cfg()
        smtp_args = []

        def fake_smtp(host, port, timeout=10):
            smtp_args.append((host, port))
            s = MagicMock()
            s.__enter__ = lambda _: s
            s.__exit__ = MagicMock(return_value=False)
            return s

        with patch("smtplib.SMTP", side_effect=fake_smtp):
            send_digest([_paper()], cfg, date(2026, 4, 7),
                        smtp_host="mail.example.com", smtp_port=587)

        assert smtp_args == [("mail.example.com", 587)]

    def test_smtp_failure_reraises(self):
        """SMTP send_message failure → exception propagates to caller."""
        from scholarposter.discovery.digest import send_digest
        cfg = self._disc_cfg()

        class FailSMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def send_message(self, msg): raise OSError("connection refused")

        with patch("smtplib.SMTP", FailSMTP):
            with pytest.raises(OSError, match="connection refused"):
                send_digest([_paper()], cfg, date(2026, 4, 7))

    def test_crlf_in_digest_email_stripped(self):
        """CRLF in digest_email is stripped before reaching parseaddr (defense-in-depth).

        DiscoveryConfig blocks CRLF at construction time via @model_validator, but
        send_digest adds a second layer for callers that bypass Pydantic validation
        (MagicMock, model_construct, etc.).  Discriminating: without the .replace()
        fix the raw CRLF string would be passed to parseaddr.
        """
        from scholarposter.discovery.digest import send_digest
        cfg = MagicMock()
        cfg.digest_email = "user@example.com\r\nBcc: injected@host.com"
        with patch("scholarposter.discovery.digest.parseaddr") as mock_pa, \
             patch("scholarposter.discovery.digest.smtplib"):
            mock_pa.return_value = ("", "user@example.com")
            send_digest([_paper()], cfg, date(2026, 4, 7))
            called_arg = mock_pa.call_args[0][0]
            assert "\r" not in called_arg
            assert "\n" not in called_arg


# ---------------------------------------------------------------------------
# CLI: --email-digest + --limit wiring (added to discover command)
# ---------------------------------------------------------------------------

_BASE = "https://api.openalex.org"

_DISC_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
    "\n"
    "[discovery]\n"
    "enabled = true\n"
    'digest_email = "digest@example.com"\n'
    "\n"
    "[enrichment.crossref]\n"
    'etiquette_email = "test@test.com"\n'
)

cli_runner = CliRunner()


def _make_work(doi: str, title: str = "Test", openalex_id: str = "W1",
               year: int = 2024, cited_by_count: int = 5) -> dict:
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "publication_date": f"{year}-01-01",
        "cited_by_count": cited_by_count,
        "open_access": {"is_oa": False},
        "referenced_works": [],
    }


def _work_list(works: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"results": works, "meta": {"count": len(works)}})


class TestDiscoverCLIWU6:
    @respx.mock
    def test_limit_caps_output(self, tmp_path):
        """--limit 5 returns at most 5 results."""
        from scholarposter.cli import app
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = [{"doi": "10.1/seed", "title": "Seed", "authors": ["A"]}]
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        seed_work = _make_work("10.1/seed", openalex_id="W1")
        papers = [_make_work(f"10.1/{i}", openalex_id=f"W{i+10}", title=f"Paper {i}")
                  for i in range(20)]

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list(papers))

        result = cli_runner.invoke(app, ["discover", "--config", str(p), "--limit", "5",
                                          "--mode", "cited-by"])
        assert result.exit_code == 0
        # Count occurrence of "DOI:" — one per paper
        doi_count = result.output.count("DOI:")
        assert doi_count <= 5

    @respx.mock
    def test_email_digest_flag_sends_email(self, tmp_path):
        """T-25: --email-digest calls send_digest; SMTP receives message."""
        from scholarposter.cli import app
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = [{"doi": "10.1/seed", "title": "Seed", "authors": ["A"]}]
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        seed_work = _make_work("10.1/seed", openalex_id="W1")
        new_paper = _make_work("10.1/new", title="New Paper", openalex_id="W2")

        respx.get(f"{_BASE}/works/https://doi.org/10.1/seed").mock(
            return_value=httpx.Response(200, json=seed_work)
        )
        respx.get(f"{_BASE}/works").mock(return_value=_work_list([new_paper]))

        captured = []

        class FakeSMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def send_message(self, msg): captured.append(msg)

        with patch("smtplib.SMTP", FakeSMTP):
            result = cli_runner.invoke(app, ["discover", "--config", str(p),
                                              "--mode", "cited-by", "--email-digest"])
        assert result.exit_code == 0
        assert len(captured) == 1
        assert "new candidates" in captured[0]["Subject"]

    def test_email_digest_no_digest_email_configured_exits_1(self, tmp_path):
        """--email-digest without digest_email in config → exit 1 with error."""
        from scholarposter.cli import app
        toml_no_email = (
            "[mastodon]\n"
            'instance = "https://fediscience.org"\n'
            'credentials_file = "test.secret"\n'
            "\n"
            "[discovery]\n"
            "enabled = true\n"
        )
        p = tmp_path / "config.toml"
        p.write_text(toml_no_email)
        bib = [{"doi": "10.1/seed", "title": "Seed", "authors": ["A"]}]
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        with patch("scholarposter.discovery.graph.cited_by", return_value=[_paper(doi="10.1/new")]):
            result = cli_runner.invoke(app, ["discover", "--config", str(p),
                                              "--mode", "cited-by", "--email-digest"])
        assert result.exit_code == 1
        assert "digest_email" in (result.output + (result.stderr or "")).lower() or \
               "email" in (result.output + (result.stderr or "")).lower()

    def test_rank_used_for_dedup(self, tmp_path):
        """discover uses rank() — same DOI from two modes → appears once in output."""
        from scholarposter.cli import app
        from scholarposter.discovery import CandidatePaper as _CP
        p = tmp_path / "config.toml"
        p.write_text(_DISC_TOML)
        bib = [{"doi": "10.1/seed", "title": "Seed", "authors": ["A"]}]
        (tmp_path / "bibliography.json").write_text(json.dumps(bib))

        # Both cited_by and cites return the same DOI (different modes)
        dup_cb = _CP(doi="10.1/dup", openalex_id="W2", title="Duplicate Paper",
                     year=2024, cited_by_count=10, is_oa=False, source="openalex",
                     mode="cited-by", age_years=2.0)
        dup_cites = _CP(doi="10.1/dup", openalex_id="W2", title="Duplicate Paper",
                        year=2024, cited_by_count=10, is_oa=False, source="openalex",
                        mode="cites", age_years=2.0)

        with patch("scholarposter.discovery.graph.cited_by", return_value=[dup_cb]), \
             patch("scholarposter.discovery.graph.cites", return_value=[dup_cites]):
            result = cli_runner.invoke(app, ["discover", "--config", str(p), "--mode", "all"])
        assert result.exit_code == 0
        # "Duplicate Paper" appears exactly once in output
        assert result.output.count("Duplicate Paper") == 1
