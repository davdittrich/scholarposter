"""Tests for scholarposter.audit.engagement (WU-4)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional  # noqa: F401 — used in _make_record annotation
from unittest.mock import MagicMock, mock_open, patch

import pytest
from typer.testing import CliRunner

from scholarposter.audit.engagement import (
    _write_records_atomically,
    parse_at_uri,
    sync_engagement,
)
from scholarposter.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# parse_at_uri
# ---------------------------------------------------------------------------

class TestParseAtUri:
    def test_did_url_produces_correct_at_uri(self):
        url = "https://bsky.app/profile/did:plc:abc123/post/rkey456"
        assert parse_at_uri(url) == "at://did:plc:abc123/app.bsky.feed.post/rkey456"

    def test_did_web_url_produces_correct_at_uri(self):
        url = "https://bsky.app/profile/did:web:example.com/post/abc789"
        assert parse_at_uri(url) == "at://did:web:example.com/app.bsky.feed.post/abc789"

    def test_handle_url_returns_none(self):
        # Non-DID handle — cannot construct AT URI without resolveHandle
        url = "https://bsky.app/profile/alice.bsky.social/post/rkey"
        assert parse_at_uri(url) is None

    def test_none_post_url_returns_none(self):
        assert parse_at_uri(None) is None

    def test_empty_string_returns_none(self):
        assert parse_at_uri("") is None

    def test_malformed_url_returns_none(self):
        assert parse_at_uri("https://bsky.app/profile/") is None

    def test_missing_rkey_returns_none(self):
        # URL has did but no /post/{rkey}
        url = "https://bsky.app/profile/did:plc:abc123"
        assert parse_at_uri(url) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    platform: str = "bluesky",
    post_url: Optional[str] = "https://bsky.app/profile/did:plc:abc/post/rkey1",
    status="posted",
    toot_id="123",
    bluesky_likes=None,
    bluesky_reposts=None,
    engagement_synced_at=None,
) -> dict:
    return {
        "toot_id": toot_id,
        "platform": platform,
        "status": status,
        "post_url": post_url,
        "bluesky_likes": bluesky_likes,
        "bluesky_reposts": bluesky_reposts,
        "engagement_synced_at": engagement_synced_at,
        "timestamp": "2026-04-07T00:00:00Z",
        "doi": None,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _make_mock_client(likes_count: int = 5, reposts_count: int = 2):
    """Return a mock atproto Client with paginated get_likes/get_reposted_by."""
    client = MagicMock()

    likes_resp = MagicMock()
    likes_resp.likes = [MagicMock()] * likes_count
    likes_resp.cursor = None  # single page

    reposts_resp = MagicMock()
    reposts_resp.reposted_by = [MagicMock()] * reposts_count
    reposts_resp.cursor = None

    client.app.bsky.feed.get_likes.return_value = likes_resp
    client.app.bsky.feed.get_reposted_by.return_value = reposts_resp
    return client


# ---------------------------------------------------------------------------
# sync_engagement — core logic
# ---------------------------------------------------------------------------

class TestSyncEngagementCore:
    def test_updates_engagement_fields(self, tmp_path):
        """T-29: likes/reposts written; engagement_synced_at set."""
        audit = tmp_path / "audit.jsonl"
        rec = _make_record()
        _write_jsonl(audit, [rec])

        mock_client = _make_mock_client(likes_count=5, reposts_count=2)

        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=False,
        )

        assert synced == 1
        assert skipped == 0
        assert errors == 0

        rows = _read_jsonl(audit)
        assert rows[0]["bluesky_likes"] == 5
        assert rows[0]["bluesky_reposts"] == 2
        assert rows[0]["engagement_synced_at"] is not None

    def test_skips_already_synced_without_force(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        rec = _make_record(
            bluesky_likes=3,
            bluesky_reposts=1,
            engagement_synced_at="2026-04-06T00:00:00Z",
        )
        _write_jsonl(audit, [rec])

        mock_client = _make_mock_client()
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=False,
        )

        assert synced == 0
        assert skipped == 1
        assert errors == 0
        mock_client.app.bsky.feed.get_likes.assert_not_called()

    def test_force_resyncs_already_synced(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        rec = _make_record(
            bluesky_likes=0,
            bluesky_reposts=0,
            engagement_synced_at="2026-04-06T00:00:00Z",
        )
        _write_jsonl(audit, [rec])

        mock_client = _make_mock_client(likes_count=10, reposts_count=3)
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=True,
        )

        assert synced == 1
        rows = _read_jsonl(audit)
        assert rows[0]["bluesky_likes"] == 10

    def test_null_post_url_counted_as_skipped(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        rec = _make_record(post_url=None)
        _write_jsonl(audit, [rec])

        mock_client = _make_mock_client()
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=False,
        )

        assert synced == 0
        assert skipped == 1
        assert errors == 0
        mock_client.app.bsky.feed.get_likes.assert_not_called()

    def test_non_bluesky_record_skipped(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        rec = _make_record(platform="linkedin")
        _write_jsonl(audit, [rec])

        mock_client = _make_mock_client()
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=False,
        )

        assert skipped == 1
        assert synced == 0

    def test_handle_url_counted_as_skipped(self, tmp_path):
        """Handle URLs (non-DID) can't produce AT URI — skip, not error."""
        audit = tmp_path / "audit.jsonl"
        rec = _make_record(post_url="https://bsky.app/profile/alice.bsky.social/post/rkey")
        _write_jsonl(audit, [rec])

        mock_client = _make_mock_client()
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=False,
        )

        assert skipped == 1
        assert errors == 0

    def test_missing_audit_file_returns_zeros(self, tmp_path):
        audit = tmp_path / "nonexistent.jsonl"
        mock_client = _make_mock_client()
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=False,
            force=False,
        )
        assert (synced, skipped, errors) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestEngagementPagination:
    def test_cursor_pagination_accumulates_all_pages(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        _write_jsonl(audit, [_make_record()])

        client = MagicMock()

        # Page 1 has cursor, page 2 does not
        page1 = MagicMock()
        page1.likes = [MagicMock()] * 5
        page1.cursor = "cursor_token"

        page2 = MagicMock()
        page2.likes = [MagicMock()] * 3
        page2.cursor = None

        client.app.bsky.feed.get_likes.side_effect = [page1, page2]

        reposts_resp = MagicMock()
        reposts_resp.reposted_by = []
        reposts_resp.cursor = None
        client.app.bsky.feed.get_reposted_by.return_value = reposts_resp

        sync_engagement(audit_path=audit, client=client, dry_run=False, force=False)

        rows = _read_jsonl(audit)
        assert rows[0]["bluesky_likes"] == 8  # 5 + 3


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestSyncEngagementDryRun:
    def test_dry_run_no_writes(self, tmp_path):
        """T-30: dry_run must not modify audit.jsonl."""
        audit = tmp_path / "audit.jsonl"
        rec = _make_record()
        _write_jsonl(audit, [rec])
        original_mtime = os.path.getmtime(audit)

        mock_client = _make_mock_client(likes_count=7, reposts_count=1)
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=True,
            force=False,
        )

        assert synced == 1  # 1 would-be update
        assert os.path.getmtime(audit) == original_mtime  # file untouched

        rows = _read_jsonl(audit)
        assert rows[0]["bluesky_likes"] is None  # unchanged

    def test_dry_run_returns_planned_count(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        records = [_make_record(toot_id=str(i), post_url=f"https://bsky.app/profile/did:plc:x/post/r{i}") for i in range(3)]
        _write_jsonl(audit, records)

        mock_client = _make_mock_client()
        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=mock_client,
            dry_run=True,
            force=False,
        )

        assert synced == 3
        assert errors == 0


# ---------------------------------------------------------------------------
# Atomicity / record count guard
# ---------------------------------------------------------------------------

class TestSyncEngagementAtomicity:
    def test_record_count_mismatch_raises_runtime_error(self, tmp_path):
        """If write produces wrong record count, raise RuntimeError; original unchanged."""
        audit = tmp_path / "audit.jsonl"
        rec = _make_record()
        _write_jsonl(audit, [rec])
        original = audit.read_bytes()

        mock_client = _make_mock_client()

        # Patch _write_records_atomically to simulate mismatch
        with patch(
            "scholarposter.audit.engagement._write_records_atomically",
            side_effect=RuntimeError("record count mismatch"),
        ):
            with pytest.raises(RuntimeError, match="record count mismatch"):
                sync_engagement(
                    audit_path=audit,
                    client=mock_client,
                    dry_run=False,
                    force=False,
                )

        assert audit.read_bytes() == original  # original intact

    def test_atomic_write_uses_temp_file(self, tmp_path):
        """_write_records_atomically must use a temp file then rename."""
        audit = tmp_path / "audit.jsonl"
        _write_jsonl(audit, [_make_record()])
        mock_client = _make_mock_client()

        written_paths = []
        real_open = os.open

        def capturing_open(path, flags, mode=0o666):
            written_paths.append(path)
            return real_open(path, flags, mode)

        with patch("scholarposter.audit.engagement.os.open", side_effect=capturing_open):
            sync_engagement(audit_path=audit, client=mock_client, dry_run=False, force=False)

        # At least one temp path should have been opened
        assert any(".tmp" in str(p) for p in written_paths)


# ---------------------------------------------------------------------------
# API error handling
# ---------------------------------------------------------------------------

class TestEngagementApiErrors:
    def test_api_error_increments_errors_count(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        _write_jsonl(audit, [_make_record()])

        client = MagicMock()
        client.app.bsky.feed.get_likes.side_effect = Exception("Network error")

        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=client,
            dry_run=False,
            force=False,
        )

        assert errors == 1
        assert synced == 0

    def test_api_error_preserves_original_record(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        rec = _make_record()
        _write_jsonl(audit, [rec])

        client = MagicMock()
        client.app.bsky.feed.get_likes.side_effect = Exception("timeout")

        sync_engagement(audit_path=audit, client=client, dry_run=False, force=False)

        rows = _read_jsonl(audit)
        assert rows[0]["bluesky_likes"] is None

    def test_mixed_success_and_error(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        good = _make_record(toot_id="1", post_url="https://bsky.app/profile/did:plc:aaa/post/r1")
        bad = _make_record(toot_id="2", post_url="https://bsky.app/profile/did:plc:bbb/post/r2")
        _write_jsonl(audit, [good, bad])

        client = MagicMock()
        good_likes = MagicMock(likes=[MagicMock()] * 3, cursor=None)
        good_reposts = MagicMock(reposted_by=[], cursor=None)
        client.app.bsky.feed.get_likes.side_effect = [good_likes, Exception("bad")]
        client.app.bsky.feed.get_reposted_by.return_value = good_reposts

        synced, skipped, errors = sync_engagement(
            audit_path=audit,
            client=client,
            dry_run=False,
            force=False,
        )

        assert synced == 1
        assert errors == 1


# ---------------------------------------------------------------------------
# Coverage — exception branch, reposts pagination, count mismatch
# ---------------------------------------------------------------------------

class TestCoverageCompleteness:
    def test_parse_at_uri_urlparse_exception_returns_none(self):
        """Lines 31-32: except Exception fallback in parse_at_uri."""
        with patch("urllib.parse.urlparse", side_effect=AttributeError("boom")):
            assert parse_at_uri("https://bsky.app/profile/did:plc:x/post/r") is None

    def test_reposts_cursor_pagination(self, tmp_path):
        """Line 58: cursor branch in _fetch_reposts."""
        audit = tmp_path / "audit.jsonl"
        _write_jsonl(audit, [_make_record()])

        client = MagicMock()

        likes_resp = MagicMock(likes=[], cursor=None)
        client.app.bsky.feed.get_likes.return_value = likes_resp

        page1 = MagicMock(reposted_by=[MagicMock()] * 4, cursor="tok")
        page2 = MagicMock(reposted_by=[MagicMock()] * 2, cursor=None)
        client.app.bsky.feed.get_reposted_by.side_effect = [page1, page2]

        sync_engagement(audit_path=audit, client=client, dry_run=False, force=False)

        rows = _read_jsonl(audit)
        assert rows[0]["bluesky_reposts"] == 6  # 4 + 2

    def test_write_atomically_count_mismatch_raises(self, tmp_path):
        """Lines 86-90: count mismatch → RuntimeError + tmp cleanup."""
        audit = tmp_path / "audit.jsonl"
        records = [{"toot_id": "1"}]

        # Patch open so the read-back sees no lines (simulates truncated write)
        m = mock_open(read_data="")
        with patch("scholarposter.audit.engagement.open", m):
            with pytest.raises(RuntimeError, match="record count mismatch"):
                _write_records_atomically(audit, records)

        # Temp file should be cleaned up
        assert not (tmp_path / "audit.jsonl.tmp").exists()

    def test_write_atomically_unlink_oserror_still_raises(self, tmp_path):
        """Lines 88-89: os.unlink failure on mismatch is swallowed; RuntimeError still raised."""
        audit = tmp_path / "audit.jsonl"
        records = [{"toot_id": "1"}]

        m = mock_open(read_data="")
        with patch("scholarposter.audit.engagement.open", m), \
             patch("scholarposter.audit.engagement.os.unlink", side_effect=OSError("busy")):
            with pytest.raises(RuntimeError, match="record count mismatch"):
                _write_records_atomically(audit, records)


# ---------------------------------------------------------------------------
# sync-engagement CLI subcommand
# ---------------------------------------------------------------------------

_SYNC_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
    "\n"
    "[audit]\n"
    "enabled = true\n"
    'file = "audit.jsonl"\n'
)
_NOAUDIT_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
)


class TestSyncEngagementCmd:
    def test_audit_disabled_exits_1(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_NOAUDIT_TOML)
        result = runner.invoke(app, ["sync-engagement", "--config", str(p)])
        assert result.exit_code == 1
        assert "disabled" in result.output

    def test_missing_bluesky_env_exits_1(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        result = runner.invoke(
            app, ["sync-engagement", "--config", str(p)],
            env={"BLUESKY_EMAIL": "", "BLUESKY_PASSWORD": ""},
        )
        assert result.exit_code == 1

    def test_login_failure_exits_1(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        with patch("atproto.Client") as mock_cls:
            mock_cls.return_value.login.side_effect = Exception("auth fail")
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 1

    def test_successful_sync_prints_summary(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        with patch("atproto.Client"), \
             patch("scholarposter.audit.engagement.sync_engagement", return_value=(3, 1, 0)):
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 0
        assert "3 posts" in result.output
        assert "1 skipped" in result.output

    def test_dry_run_prints_prefix(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        with patch("atproto.Client"), \
             patch("scholarposter.audit.engagement.sync_engagement", return_value=(2, 0, 0)):
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p), "--dry-run"],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 0
        assert "[dry-run]" in result.output

    def test_errors_exits_1(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        with patch("atproto.Client"), \
             patch("scholarposter.audit.engagement.sync_engagement", return_value=(0, 0, 2)):
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 1
        assert "errors" in result.output

    def test_lock_contention_exits_1(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        with patch("fcntl.flock", side_effect=OSError("EAGAIN")):
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 1
        assert "lock" in result.output.lower()

    def test_config_load_error_exits_1(self, tmp_path):
        """Lines 927-929: load_config() failure → 'Config error' message."""
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        with patch("scholarposter.cli.load_config", side_effect=ValueError("bad toml")):
            result = runner.invoke(app, ["sync-engagement", "--config", str(p)])
        assert result.exit_code == 1
        assert "Config error" in result.output

    def test_lock_release_oserror_swallowed(self, tmp_path):
        """Lines 975-976: OSError during lock release is swallowed; command succeeds."""
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        # First flock call = acquire (succeeds), second = release (fails)
        with patch("fcntl.flock", side_effect=[None, OSError("release fail")]), \
             patch("atproto.Client"), \
             patch("scholarposter.audit.engagement.sync_engagement", return_value=(1, 0, 0)):
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 0
        assert "1 posts" in result.output

    # W4: lock file must be deleted after every exit path
    def test_sync_engagement_lock_file_deleted_on_success(self, tmp_path):
        """Lock file must not persist after successful sync."""
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        lock_path = tmp_path / "audit.lock"
        with patch("atproto.Client"), \
             patch("scholarposter.audit.engagement.sync_engagement", return_value=(1, 0, 0)):
            runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert not lock_path.exists(), f"Lock file was not deleted: {lock_path}"

    def test_sync_engagement_lock_file_deleted_on_inner_error(self, tmp_path):
        """Lock file must not persist when sync_engagement itself raises."""
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        lock_path = tmp_path / "audit.lock"
        with patch("atproto.Client"), \
             patch("scholarposter.audit.engagement.sync_engagement",
                   side_effect=RuntimeError("unexpected DB error")):
            runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert not lock_path.exists(), f"Lock file was not deleted: {lock_path}"

    def test_sync_engagement_lock_file_deleted_on_contention(self, tmp_path):
        """Lock file must not persist when flock reports lock already held."""
        p = tmp_path / "config.toml"
        p.write_text(_SYNC_TOML)
        (tmp_path / "audit.jsonl").write_text("")
        lock_path = tmp_path / "audit.lock"
        # Patch flock to raise (simulates EAGAIN/lock held by another process).
        # os.open still runs and creates the file; our fix must unlink it.
        with patch("fcntl.flock", side_effect=OSError("EAGAIN")):
            result = runner.invoke(
                app, ["sync-engagement", "--config", str(p)],
                env={"BLUESKY_EMAIL": "u@x.com", "BLUESKY_PASSWORD": "pw"},
            )
        assert result.exit_code == 1
        assert not lock_path.exists(), f"Lock file was not deleted on contention: {lock_path}"
