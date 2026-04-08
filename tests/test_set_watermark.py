"""Tests for set-watermark CLI command and _find_watermark_for_date helper (US-016)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from scholarposter.cli import app, _TOOT_URL_RE
from scholarposter.models import PlatformState
from scholarposter.state import StateManager as RealStateManager

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config_toml(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mastodon]\ninstance = "https://mastodon.social"\ncredentials_file = "cred.secret"\n')
    return cfg


def _make_toot(toot_id: int, created_at: datetime) -> dict:
    return {"id": str(toot_id), "created_at": created_at}


def _real_sm(tmp_path: Path) -> RealStateManager:
    return RealStateManager(state_dir=tmp_path)


# ---------------------------------------------------------------------------
# _TOOT_URL_RE
# ---------------------------------------------------------------------------

class TestTootUrlRe:
    def test_at_user_format(self):
        m = _TOOT_URL_RE.match("https://mastodon.social/@alice/123456789")
        assert m is not None
        assert m.group(1) == "123456789"

    def test_at_user_with_dots_in_username(self):
        m = _TOOT_URL_RE.match("https://fediscience.org/@alice.smith/999000111")
        assert m is not None
        assert m.group(1) == "999000111"

    def test_users_statuses_format(self):
        m = _TOOT_URL_RE.match("https://example.social/users/bob/statuses/888000777")
        assert m is not None
        assert m.group(1) == "888000777"

    def test_non_numeric_id_no_match(self):
        assert _TOOT_URL_RE.match("https://mastodon.social/@alice/not-an-id") is None

    def test_bare_domain_no_match(self):
        assert _TOOT_URL_RE.match("https://mastodon.social/") is None

    def test_http_scheme_matches(self):
        m = _TOOT_URL_RE.match("http://mastodon.social/@alice/123")
        assert m is not None
        assert m.group(1) == "123"


# ---------------------------------------------------------------------------
# _find_watermark_for_date
# ---------------------------------------------------------------------------

class TestFindWatermarkForDate:
    """Unit tests for the date-search helper (imported after implementation)."""

    @pytest.fixture(autouse=True)
    def import_helper(self):
        from scholarposter.cli import _find_watermark_for_date
        self.fn = _find_watermark_for_date

    def _dt(self, y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
        return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)

    def test_happy_path_first_page_match(self):
        """Toot before cutoff found on first page → returned immediately (1 API call)."""
        target = date(2026, 3, 1)  # midnight UTC on March 1
        cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
        mastodon = MagicMock()
        mastodon.account_statuses.return_value = [
            _make_toot(300, self._dt(2026, 3, 2)),   # after cutoff — skip
            _make_toot(200, self._dt(2026, 2, 28)),  # before cutoff — use this
            _make_toot(100, self._dt(2026, 2, 27)),  # before cutoff — not reached
        ]
        result = self.fn(mastodon, 42, target)
        assert result == 200
        assert mastodon.account_statuses.call_count == 1

    def test_paging_match_on_second_page(self):
        """No match on page 1 → pages forward; match on page 2 (2 API calls)."""
        target = date(2026, 1, 1)
        mastodon = MagicMock()
        mastodon.account_statuses.side_effect = [
            # page 1: all after cutoff
            [_make_toot(500, datetime(2026, 1, 5, tzinfo=timezone.utc)),
             _make_toot(400, datetime(2026, 1, 3, tzinfo=timezone.utc))],
            # page 2: first toot is before cutoff
            [_make_toot(200, datetime(2025, 12, 31, tzinfo=timezone.utc))],
        ]
        result = self.fn(mastodon, 42, target)
        assert result == 200
        assert mastodon.account_statuses.call_count == 2
        # Second call must use max_id = 400 (last of page 1)
        _, kwargs = mastodon.account_statuses.call_args
        assert kwargs.get("max_id") == 400

    def test_empty_page_sentinel_returns_none(self):
        """First API call returns empty list → None immediately (account has no toots)."""
        target = date(2026, 1, 1)
        mastodon = MagicMock()
        mastodon.account_statuses.return_value = []
        result = self.fn(mastodon, 42, target)
        assert result is None
        assert mastodon.account_statuses.call_count == 1

    def test_500_page_cap_returns_none(self):
        """Mock always returns non-empty pages with toots after cutoff → cap exits after 500."""
        target = date(2020, 1, 1)  # very old date
        mastodon = MagicMock()
        # Always returns a page with one recent toot (never before cutoff)
        mastodon.account_statuses.return_value = [
            _make_toot(9999, datetime(2026, 4, 1, tzinfo=timezone.utc))
        ]
        result = self.fn(mastodon, 42, target)
        assert result is None
        assert mastodon.account_statuses.call_count == 500

    def test_exception_propagates_to_caller(self):
        """Exception from account_statuses is re-raised (not swallowed)."""
        target = date(2026, 1, 1)
        mastodon = MagicMock()
        mastodon.account_statuses.side_effect = RuntimeError("network fail")
        with pytest.raises(RuntimeError, match="network fail"):
            self.fn(mastodon, 42, target)

    def test_midnight_utc_boundary_23_59_59_included(self):
        """Toot at 23:59:59 on the day before target → included (strictly before midnight)."""
        target = date(2026, 3, 1)
        mastodon = MagicMock()
        mastodon.account_statuses.return_value = [
            _make_toot(100, datetime(2026, 2, 28, 23, 59, 59, tzinfo=timezone.utc)),
        ]
        result = self.fn(mastodon, 42, target)
        assert result == 100

    def test_midnight_utc_boundary_00_00_00_excluded(self):
        """Toot at 00:00:00 on the target date → NOT included (equal to cutoff, not less-than)."""
        target = date(2026, 3, 1)
        mastodon = MagicMock()
        mastodon.account_statuses.side_effect = [
            # Page 1: toot exactly at cutoff — not before it
            [_make_toot(300, datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc))],
            # Page 2: empty (exhausted)
            [],
        ]
        result = self.fn(mastodon, 42, target)
        assert result is None


# ---------------------------------------------------------------------------
# set_watermark_cmd — anchor flag validation
# ---------------------------------------------------------------------------

class TestSetWatermarkAnchorValidation:
    def test_zero_anchor_flags_exits_2(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        result = runner.invoke(app, ["set-watermark", "--config", str(cfg_file)])
        assert result.exit_code == 2
        assert "exactly one" in result.output.lower() or "exactly one" in (result.stdout + (result.stderr or "")).lower()

    def test_two_anchor_flags_exits_2(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        result = runner.invoke(app, [
            "set-watermark", "--config", str(cfg_file),
            "--toot-id", "123", "--toot-url", "https://mastodon.social/@u/123"
        ])
        assert result.exit_code == 2

    def test_three_anchor_flags_exits_2(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        result = runner.invoke(app, [
            "set-watermark", "--config", str(cfg_file),
            "--toot-id", "123",
            "--toot-url", "https://mastodon.social/@u/123",
            "--date", "2026-01-01",
        ])
        assert result.exit_code == 2

    def test_invalid_platform_exits_2(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        result = runner.invoke(app, [
            "set-watermark", "--config", str(cfg_file),
            "--toot-id", "123", "--platform", "twitter"
        ])
        assert result.exit_code == 2

    def test_config_load_failure_exits_1(self, tmp_path):
        """load_config raises → exit 1 with Config error message."""
        cfg_file = _make_config_toml(tmp_path)
        with patch("scholarposter.cli.load_config", side_effect=ValueError("bad toml")):
            result = runner.invoke(app, [
                "set-watermark", "--config", str(cfg_file),
                "--toot-id", "123", "--yes",
            ])
        assert result.exit_code == 1
        assert "Config error" in result.output


# ---------------------------------------------------------------------------
# set_watermark_cmd — --toot-id mode
# ---------------------------------------------------------------------------

class TestSetWatermarkTootId:
    def _invoke(self, tmp_path, extra_args=(), input_str=None):
        cfg_file = _make_config_toml(tmp_path)
        sm = _real_sm(tmp_path)
        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls:
            mock_cfg.return_value.state.state_file = "state.json"
            mock_sm_cls.return_value = sm
            args = ["set-watermark", "--config", str(cfg_file),
                    "--toot-id", "113456789012345678"] + list(extra_args)
            result = runner.invoke(app, args, input=input_str)
        return result, sm

    def test_bluesky_platform_sets_correct_value(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        state = sm.load_state()
        assert state["bluesky"]["last_toot_id"] == 113456789012345678

    def test_linkedin_platform_sets_correct_value(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "linkedin", "--yes"])
        assert result.exit_code == 0
        state = sm.load_state()
        assert state["linkedin"]["last_toot_id"] == 113456789012345678

    def test_all_platform_sets_both(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "all", "--yes"])
        assert result.exit_code == 0
        state = sm.load_state()
        assert state["bluesky"]["last_toot_id"] == 113456789012345678
        assert state["linkedin"]["last_toot_id"] == 113456789012345678

    def test_preserves_pre_existing_fields(self, tmp_path):
        sm = _real_sm(tmp_path)
        sm.update_platform_state("bluesky", PlatformState(
            last_toot_id=999, last_status="posted", last_error="old_error"
        ))
        cfg_file = _make_config_toml(tmp_path)
        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls:
            mock_cfg.return_value.state.state_file = "state.json"
            mock_sm_cls.return_value = sm
            runner.invoke(app, [
                "set-watermark", "--config", str(cfg_file),
                "--toot-id", "123", "--platform", "bluesky", "--yes"
            ])
        state = sm.load_state()
        assert state["bluesky"]["last_toot_id"] == 123
        assert state["bluesky"]["last_status"] == "posted"
        assert state["bluesky"]["last_error"] == "old_error"

    def test_dry_run_does_not_write_state(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "bluesky", "--dry-run"])
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert sm.load_state() == {}  # nothing written

    def test_dry_run_does_not_acquire_lock(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        sm = _real_sm(tmp_path)
        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls, \
             patch.object(sm, "acquire_lock", wraps=sm.acquire_lock) as spy_lock:
            mock_cfg.return_value.state.state_file = "state.json"
            mock_sm_cls.return_value = sm
            runner.invoke(app, [
                "set-watermark", "--config", str(cfg_file),
                "--toot-id", "123", "--platform", "bluesky", "--dry-run"
            ])
        spy_lock.assert_not_called()

    def test_yes_skips_confirmation_prompt(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        # No prompt text in output
        assert "Continue?" not in result.output
        assert sm.load_state()["bluesky"]["last_toot_id"] == 113456789012345678

    def test_confirmation_prompt_n_exits_0_no_write(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "bluesky"], input_str="n\n")
        assert result.exit_code == 0
        assert sm.load_state() == {}

    def test_confirmation_prompt_y_writes_state(self, tmp_path):
        result, sm = self._invoke(tmp_path, ["--platform", "bluesky"], input_str="y\n")
        assert result.exit_code == 0
        assert sm.load_state()["bluesky"]["last_toot_id"] == 113456789012345678

    def test_lock_contention_exits_1(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        sm = _real_sm(tmp_path)
        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls, \
             patch.object(sm, "acquire_lock", return_value=False):
            mock_cfg.return_value.state.state_file = "state.json"
            mock_sm_cls.return_value = sm
            result = runner.invoke(app, [
                "set-watermark", "--config", str(cfg_file),
                "--toot-id", "123", "--platform", "bluesky", "--yes"
            ])
        assert result.exit_code == 1
        assert "Another scholarposter process is running" in result.output


# ---------------------------------------------------------------------------
# set_watermark_cmd — --toot-url mode
# ---------------------------------------------------------------------------

class TestSetWatermarkTootUrl:
    def _invoke_url(self, tmp_path, url: str, extra_args=()):
        cfg_file = _make_config_toml(tmp_path)
        sm = _real_sm(tmp_path)
        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls:
            mock_cfg.return_value.state.state_file = "state.json"
            mock_sm_cls.return_value = sm
            args = ["set-watermark", "--config", str(cfg_file),
                    "--toot-url", url, "--yes"] + list(extra_args)
            result = runner.invoke(app, args)
        return result, sm

    def test_at_user_format_parsed(self, tmp_path):
        url = "https://mastodon.social/@alice/113456789012345678"
        result, sm = self._invoke_url(tmp_path, url, ["--platform", "bluesky"])
        assert result.exit_code == 0
        assert sm.load_state()["bluesky"]["last_toot_id"] == 113456789012345678

    def test_users_statuses_format_parsed(self, tmp_path):
        url = "https://mastodon.social/users/alice/statuses/999000111222333"
        result, sm = self._invoke_url(tmp_path, url, ["--platform", "linkedin"])
        assert result.exit_code == 0
        assert sm.load_state()["linkedin"]["last_toot_id"] == 999000111222333

    def test_unknown_url_format_exits_2(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        result = runner.invoke(app, [
            "set-watermark", "--config", str(cfg_file),
            "--toot-url", "https://mastodon.social/not-a-toot-url", "--yes"
        ])
        assert result.exit_code == 2
        assert "Cannot parse toot ID from URL" in result.output


# ---------------------------------------------------------------------------
# set_watermark_cmd — --date mode
# ---------------------------------------------------------------------------

class TestSetWatermarkDate:
    def _mastodon_mock(self, statuses_side_effect):
        mock_masto = MagicMock()
        mock_masto.me.return_value = {"id": "42"}
        if isinstance(statuses_side_effect, list) and statuses_side_effect and isinstance(statuses_side_effect[0], list):
            mock_masto.account_statuses.side_effect = statuses_side_effect
        else:
            mock_masto.account_statuses.return_value = statuses_side_effect
        return mock_masto

    def _invoke_date(self, tmp_path, date_str: str, mastodon, extra_args=(), input_str=None):
        cfg_file = _make_config_toml(tmp_path)
        sm = _real_sm(tmp_path)
        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls, \
             patch("scholarposter.cli._build_mastodon_client", return_value=mastodon):
            mock_cfg.return_value.state.state_file = "state.json"
            mock_sm_cls.return_value = sm
            args = ["set-watermark", "--config", str(cfg_file),
                    "--date", date_str] + list(extra_args)
            result = runner.invoke(app, args, input=input_str)
        return result, sm

    def test_date_bisects_timeline_correct_id(self, tmp_path):
        """Newest-first: two toots after cutoff, one before → correct ID found."""
        mastodon = self._mastodon_mock([
            _make_toot(500, datetime(2026, 3, 5, tzinfo=timezone.utc)),
            _make_toot(400, datetime(2026, 3, 3, tzinfo=timezone.utc)),
            _make_toot(300, datetime(2026, 2, 28, tzinfo=timezone.utc)),  # before March 1
        ])
        result, sm = self._invoke_date(tmp_path, "2026-03-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        assert sm.load_state()["bluesky"]["last_toot_id"] == 300

    def test_date_before_all_toots_deletes_key(self, tmp_path):
        """No toot before given date → last_toot_id key deleted from state (not null)."""
        mastodon = self._mastodon_mock([])  # first page empty
        result, sm = self._invoke_date(tmp_path, "2020-01-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        state = sm.load_state()
        assert "bluesky" not in state or "last_toot_id" not in state.get("bluesky", {})

    def test_date_future_sets_most_recent_toot(self, tmp_path):
        """Future date: all toots before cutoff → most recent (first) toot ID set."""
        mastodon = self._mastodon_mock([
            _make_toot(999, datetime(2026, 4, 7, tzinfo=timezone.utc)),
        ])
        result, sm = self._invoke_date(tmp_path, "2030-01-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        assert sm.load_state()["bluesky"]["last_toot_id"] == 999

    def test_dry_run_date_api_still_executes_no_write(self, tmp_path):
        """--dry-run + --date: API call to account_statuses still executes; no state write."""
        mastodon = self._mastodon_mock([
            _make_toot(500, datetime(2026, 2, 28, tzinfo=timezone.utc)),
        ])
        result, sm = self._invoke_date(tmp_path, "2026-03-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--dry-run"])
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert mastodon.account_statuses.called  # API call DID execute
        assert sm.load_state() == {}  # no state written

    def test_network_error_exits_1_state_unchanged(self, tmp_path):
        """Network error during date lookup → exit 1; state not written."""
        mastodon = MagicMock()
        mastodon.me.return_value = {"id": "42"}
        mastodon.account_statuses.side_effect = ConnectionError("network fail")
        result, sm = self._invoke_date(tmp_path, "2026-03-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 1
        assert "Error fetching timeline" in result.output or "network fail" in result.output
        assert sm.load_state() == {}

    def test_invalid_date_format_exits_2(self, tmp_path):
        cfg_file = _make_config_toml(tmp_path)
        result = runner.invoke(app, [
            "set-watermark", "--config", str(cfg_file),
            "--date", "not-a-date", "--yes"
        ])
        assert result.exit_code == 2

    def test_500_page_exhaustion_deletes_key(self, tmp_path):
        """500 pages exhausted without finding toot before cutoff → last_toot_id deleted.
        Distinct from empty-page test: mock returns non-empty pages every time."""
        # Use a very old target date so no toot is before it
        mastodon = MagicMock()
        mastodon.me.return_value = {"id": "42"}
        mastodon.account_statuses.return_value = [
            _make_toot(9999, datetime(2026, 4, 1, tzinfo=timezone.utc))
        ]
        result, sm = self._invoke_date(tmp_path, "2020-01-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        assert mastodon.account_statuses.call_count == 500
        state = sm.load_state()
        # Key deleted (not null)
        assert "bluesky" not in state or "last_toot_id" not in state.get("bluesky", {})

    def test_midnight_utc_boundary_day_before_included(self, tmp_path):
        """Toot at 23:59:59 the day before target → included as watermark."""
        mastodon = self._mastodon_mock([
            _make_toot(100, datetime(2026, 2, 28, 23, 59, 59, tzinfo=timezone.utc)),
        ])
        result, sm = self._invoke_date(tmp_path, "2026-03-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        assert sm.load_state()["bluesky"]["last_toot_id"] == 100

    def test_midnight_utc_boundary_target_date_excluded(self, tmp_path):
        """Toot at 00:00:00 on the target date → NOT included (not strictly before cutoff)."""
        mastodon = self._mastodon_mock([
            # Page 1: toot exactly at cutoff — not before
            [_make_toot(300, datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc))],
            # Page 2: empty (account exhausted)
            [],
        ])
        result, sm = self._invoke_date(tmp_path, "2026-03-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        state = sm.load_state()
        assert "bluesky" not in state or "last_toot_id" not in state.get("bluesky", {})

    def test_empty_account_immediately_deletes_key(self, tmp_path):
        """First account_statuses() call returns [] → account has no toots; key deleted."""
        mastodon = self._mastodon_mock([])
        result, sm = self._invoke_date(tmp_path, "2026-01-01", mastodon,
                                        extra_args=["--platform", "bluesky", "--yes"])
        assert result.exit_code == 0
        state = sm.load_state()
        assert "bluesky" not in state or "last_toot_id" not in state.get("bluesky", {})
