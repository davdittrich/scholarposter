"""Tests for scholarposter.state"""
import json
import os
import time
from unittest.mock import patch
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from scholarposter.state import StateManager
from scholarposter.models import BibliographyEntry, PlatformState


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path


@pytest.fixture
def mgr(state_dir):
    return StateManager(state_dir=state_dir)


class TestStateManager:
    def test_load_empty_state(self, mgr):
        state = mgr.load_state()
        assert state == {}

    def test_save_and_load_roundtrip(self, mgr):
        ps = PlatformState(
            last_toot_id=113456789012345678,
            last_status="posted",
            last_posted_at=datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc),
        )
        mgr.update_platform_state("bluesky", ps)
        state = mgr.load_state()
        assert "bluesky" in state
        assert state["bluesky"]["last_toot_id"] == 113456789012345678
        assert state["bluesky"]["last_status"] == "posted"

    def test_atomic_write_no_tmp_leftover(self, mgr, state_dir):
        ps = PlatformState(last_toot_id=999)
        mgr.update_platform_state("bluesky", ps)
        tmp_file = state_dir / "state.json.tmp"
        assert not tmp_file.exists()

    def test_atomic_write_cleans_tmp_on_failure(self, mgr, state_dir):
        """_atomic_write must remove .tmp file when json.dump raises."""
        circular: dict = {}
        circular["self"] = circular  # circular reference defeats default=str
        with pytest.raises(ValueError):
            mgr._atomic_write(state_dir / "test.json", circular)
        tmp_file = state_dir / "test.json.tmp"
        assert not tmp_file.exists()

    def test_get_since_id_none_when_missing(self, mgr):
        assert mgr.get_since_id("bluesky") is None

    def test_get_since_id_returns_id(self, mgr):
        ps = PlatformState(last_toot_id=113456789012345678)
        mgr.update_platform_state("bluesky", ps)
        assert mgr.get_since_id("bluesky") == 113456789012345678

    def test_file_permissions(self, mgr, state_dir):
        ps = PlatformState(last_toot_id=1)
        mgr.update_platform_state("bluesky", ps)
        state_file = state_dir / "state.json"
        mode = oct(os.stat(state_file).st_mode)
        assert mode.endswith("600"), f"Expected 0o600, got {mode}"

    def test_multiple_platforms(self, mgr):
        mgr.update_platform_state("bluesky", PlatformState(last_toot_id=100))
        mgr.update_platform_state("linkedin", PlatformState(last_toot_id=200))
        assert mgr.get_since_id("bluesky") == 100
        assert mgr.get_since_id("linkedin") == 200


class TestCacheManager:
    def test_cache_set_get_roundtrip(self, mgr):
        mgr.cache_set("test_key", {"title": "Test", "doi": "10.1000/test"}, ttl_days=7)
        result = mgr.cache_get("test_key")
        assert result is not None
        assert result["title"] == "Test"
        assert result["doi"] == "10.1000/test"

    def test_expired_entry_returns_none(self, mgr):
        # Set with past expiry by manipulating internally
        key = "expired_key"
        mgr.cache_set(key, {"data": "value"}, ttl_days=7)
        # Manually expire it
        cache = mgr._load_cache()
        cache[key]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mgr._save_cache(cache)
        assert mgr.cache_get(key) is None

    def test_cache_get_prunes_expired_from_file(self, mgr):
        """cache_get() must remove expired entries from the cache file, not just return None."""
        mgr.cache_set("fresh", {"x": 1}, ttl_days=7)
        mgr.cache_set("stale", {"x": 2}, ttl_days=7)
        # Expire "stale"
        cache = mgr._load_cache()
        cache["stale"]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mgr._save_cache(cache)
        # Calling cache_get triggers prune
        result = mgr.cache_get("stale")
        assert result is None
        # Verify "stale" is gone from the file, "fresh" is still there
        cache_after = mgr._load_cache()
        assert "stale" not in cache_after
        assert "fresh" in cache_after

    def test_prune_removes_expired(self, mgr):
        mgr.cache_set("good", {"x": 1}, ttl_days=7)
        mgr.cache_set("bad", {"x": 2}, ttl_days=7)
        # Expire "bad"
        cache = mgr._load_cache()
        cache["bad"]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mgr._save_cache(cache)
        mgr._prune_cache()
        cache_after = mgr._load_cache()
        assert "good" in cache_after
        assert "bad" not in cache_after


class TestFileLocking:
    def test_acquire_lock_returns_true(self, mgr):
        assert mgr.acquire_lock() is True
        mgr.release_lock()

    def test_release_without_acquire_safe(self, mgr):
        # Should not raise
        mgr.release_lock()

    def test_context_manager(self, mgr):
        with mgr.lock():
            # Inside lock
            pass
        # Lock released after context

    def test_double_acquire_fails(self, state_dir):
        mgr1 = StateManager(state_dir=state_dir)
        mgr2 = StateManager(state_dir=state_dir)
        assert mgr1.acquire_lock() is True
        # Second acquire on different manager should fail
        assert mgr2.acquire_lock() is False
        mgr1.release_lock()

    def test_lock_context_manager_raises_when_already_held(self, state_dir):
        """lock() context manager must raise RuntimeError when lock is already held."""
        mgr1 = StateManager(state_dir=state_dir)
        mgr2 = StateManager(state_dir=state_dir)
        assert mgr1.acquire_lock() is True
        try:
            with pytest.raises(RuntimeError, match="Could not acquire lock"):
                with mgr2.lock():
                    pass
        finally:
            mgr1.release_lock()


class TestUpdatePlatformStateMerge:
    """Tests for the merge-into-existing behaviour of update_platform_state."""

    def test_success_update_preserves_existing_last_posted_at(self, mgr):
        """A success update that omits last_posted_at must keep the prior value."""
        prior_ts = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
        mgr.update_platform_state(
            "bluesky",
            PlatformState(
                last_toot_id=100,
                last_status="posted",
                last_posted_at=prior_ts,
            ),
        )
        # Simulate a subsequent update that has no new last_posted_at
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=200, last_status="posted"),
        )
        state = mgr.load_state()
        assert state["bluesky"]["last_posted_at"] == prior_ts.isoformat()

    def test_failure_update_preserves_existing_last_posted_at(self, mgr):
        """A failure update must not wipe last_posted_at set by a prior success."""
        prior_ts = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
        mgr.update_platform_state(
            "bluesky",
            PlatformState(
                last_toot_id=100,
                last_status="posted",
                last_posted_at=prior_ts,
            ),
        )
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=100, last_status="failed", last_error="oops"),
        )
        state = mgr.load_state()
        assert state["bluesky"]["last_posted_at"] == prior_ts.isoformat()

    def test_success_update_clears_last_error_from_prior_failure(self, mgr):
        """After a success, last_error set by a prior failure must be removed."""
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=100, last_status="failed", last_error="boom"),
        )
        # Confirm error is present
        assert mgr.load_state()["bluesky"].get("last_error") == "boom"

        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=200, last_status="posted"),
        )
        state = mgr.load_state()
        assert "last_error" not in state["bluesky"]

    def test_old_last_error_cleared_on_success_update(self, mgr, state_dir):
        """Existing state with last_error written by old code is cleared on success."""
        # Write the state file directly simulating legacy data
        legacy_state = {
            "bluesky": {
                "last_toot_id": 99,
                "last_status": "failed",
                "last_posted_at": "2024-01-01T00:00:00+00:00",
                "last_error": "legacy error",
            }
        }
        state_file = state_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(legacy_state, f)

        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=200, last_status="posted"),
        )
        state = mgr.load_state()
        assert "last_error" not in state["bluesky"]
        # last_posted_at from legacy data must be preserved
        assert state["bluesky"]["last_posted_at"] == "2024-01-01T00:00:00+00:00"

    def test_auth_status_survives_toot_update(self, state_dir):
        mgr = StateManager(state_dir=state_dir)
        with mgr.lock():
            mgr.update_platform_state("linkedin", PlatformState(auth_status="auth_expired"))
            mgr.update_platform_state("linkedin", PlatformState(last_toot_id=999))
        state = mgr.load_state()
        assert state["linkedin"]["auth_status"] == "auth_expired"
        assert state["linkedin"]["last_toot_id"] == 999

    def test_refresh_failure_count_preserved(self, state_dir):
        mgr = StateManager(state_dir=state_dir)
        with mgr.lock():
            mgr.update_platform_state("linkedin", PlatformState(refresh_failure_count=2))
            mgr.update_platform_state("linkedin", PlatformState(last_status="posted"))
        state = mgr.load_state()
        assert state["linkedin"]["refresh_failure_count"] == 2

    def test_refresh_warning_cleared_by_explicit_none(self, state_dir):
        from datetime import date
        mgr = StateManager(state_dir=state_dir)
        with mgr.lock():
            mgr.update_platform_state("linkedin", PlatformState(
                refresh_warning_last_sent=date(2026, 4, 1),
            ))
            mgr.update_platform_state("linkedin", PlatformState(
                refresh_warning_last_sent=None,
            ))
        state = mgr.load_state()
        assert "refresh_warning_last_sent" not in state["linkedin"]

    def test_failure_appends_to_recent_errors(self, mgr):
        """Single failure call → raw state dict has 'recent_errors' list of length 1."""
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=101, last_status="failed", last_error="connection refused"),
        )
        state = mgr.load_state()
        errors = state["bluesky"].get("recent_errors")
        assert errors is not None, "recent_errors must be present after a failure"
        assert len(errors) == 1
        assert errors[0]["toot_id"] == "101"
        assert errors[0]["error"] == "connection refused"
        assert "timestamp" in errors[0]

    def test_multiple_failures_accumulate(self, mgr):
        """Two failure calls with different toot IDs → list length 2, IDs in call order."""
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=200, last_status="failed", last_error="err A"),
        )
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=201, last_status="failed", last_error="err B"),
        )
        state = mgr.load_state()
        errors = state["bluesky"]["recent_errors"]
        assert len(errors) == 2
        assert errors[0]["toot_id"] == "200"
        assert errors[1]["toot_id"] == "201"

    def test_success_clears_recent_errors(self, mgr):
        """Two failures then a posted success → 'recent_errors' key absent from state."""
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=300, last_status="failed", last_error="boom"),
        )
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=301, last_status="failed", last_error="boom2"),
        )
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=302, last_status="posted", last_error=None),
        )
        state = mgr.load_state()
        assert "recent_errors" not in state["bluesky"]

    def test_recent_errors_capped_at_20(self, mgr):
        """25 failure calls → list length exactly 20 (oldest 5 dropped)."""
        for i in range(25):
            mgr.update_platform_state(
                "bluesky",
                PlatformState(last_toot_id=i, last_status="failed", last_error=f"err {i}"),
            )
        state = mgr.load_state()
        errors = state["bluesky"]["recent_errors"]
        assert len(errors) == 20
        # Oldest 5 (toot_ids 0-4) dropped; newest 20 (5-24) remain
        assert errors[0]["toot_id"] == "5"
        assert errors[-1]["toot_id"] == "24"

    def test_skipped_status_does_not_clear_recent_errors(self, mgr):
        """Two failures then a skipped call → 'recent_errors' still present with 2 entries."""
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=400, last_status="failed", last_error="fail A"),
        )
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=401, last_status="failed", last_error="fail B"),
        )
        mgr.update_platform_state(
            "bluesky",
            PlatformState(last_toot_id=402, last_status="skipped", last_error=None),
        )
        state = mgr.load_state()
        errors = state["bluesky"].get("recent_errors")
        assert errors is not None, "recent_errors must survive a skipped status"
        assert len(errors) == 2


class TestUpdatePlatformStateLockWarning:
    def test_warning_logged_when_called_without_lock(self, mgr):
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            mgr.update_platform_state("bluesky", PlatformState(last_toot_id=1))
        finally:
            logger.remove(lid)
        assert any("without holding lock" in m for m in messages), f"Expected lock warning, got: {messages}"

    def test_no_warning_when_called_with_lock(self, mgr):
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            with mgr.lock():
                mgr.update_platform_state("bluesky", PlatformState(last_toot_id=1))
        finally:
            logger.remove(lid)
        assert not any("without holding lock" in m for m in messages), f"Unexpected lock warning: {messages}"


class TestPruneCacheNoWrite:
    """_prune_cache must not call _save_cache when nothing expired."""

    def test_prune_cache_no_write_when_nothing_expired(self, mgr):
        mgr.cache_set("key1", {"x": 1}, ttl_days=7)
        mgr.cache_set("key2", {"x": 2}, ttl_days=7)
        with patch.object(mgr, "_save_cache") as mock_save:
            mgr._prune_cache()
            mock_save.assert_not_called()


class TestCacheGetSingleLoad:
    """cache_get must load the cache only once (via _prune_cache)."""

    def test_cache_get_single_file_read(self, mgr):
        mgr.cache_set("k", {"v": 1}, ttl_days=7)
        with patch.object(mgr, "_load_cache", wraps=mgr._load_cache) as mock_load:
            mgr.cache_get("k")
        # _prune_cache calls _load_cache once; cache_get must not add a second call
        mock_load.assert_called_once()


def _make_entry(**kwargs) -> BibliographyEntry:
    defaults = dict(
        doi="10.1000/test",
        title="Test Paper",
        url="https://doi.org/10.1000/test",
        shared_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        platforms=["bluesky"],
    )
    defaults.update(kwargs)
    return BibliographyEntry(**defaults)


class TestBibliography:
    def test_load_bibliography_missing_file(self, mgr):
        """Returns empty list when bibliography.json does not exist."""
        assert mgr.load_bibliography() == []

    def test_load_bibliography_corrupt_json(self, mgr, state_dir):
        """Returns empty list and logs a warning on corrupt JSON."""
        bib_path = state_dir / "bibliography.json"
        bib_path.write_text("{not valid json")
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            result = mgr.load_bibliography()
        finally:
            logger.remove(lid)
        assert result == []
        assert any("corrupt" in m for m in messages)

    def test_append_bibliography_new_entry(self, mgr, state_dir):
        """Appending a new entry writes it to bibliography.json."""
        entry = _make_entry()
        with mgr.lock():
            mgr.append_bibliography(entry)
        bib = mgr.load_bibliography()
        assert len(bib) == 1
        assert bib[0]["doi"] == "10.1000/test"
        assert bib[0]["title"] == "Test Paper"
        assert "bluesky" in bib[0]["platforms"]

    def test_append_bibliography_dedup_merges_platforms(self, mgr):
        """Appending the same DOI twice merges platforms instead of duplicating entries."""
        entry1 = _make_entry(platforms=["bluesky"])
        entry2 = _make_entry(platforms=["linkedin"])
        with mgr.lock():
            mgr.append_bibliography(entry1)
            mgr.append_bibliography(entry2)
        bib = mgr.load_bibliography()
        assert len(bib) == 1
        platforms = set(bib[0]["platforms"])
        assert platforms == {"bluesky", "linkedin"}

    def test_append_bibliography_with_malformed_existing(self, mgr, state_dir):
        """Existing entry missing 'doi' key must not crash; new entry is still appended."""
        bib_path = state_dir / "bibliography.json"
        # Write an entry without 'doi'
        with open(bib_path, "w") as f:
            json.dump([{"title": "Orphan", "platforms": []}], f)
        entry = _make_entry(doi="10.9999/new")
        with mgr.lock():
            mgr.append_bibliography(entry)
        bib = mgr.load_bibliography()
        dois = [e.get("doi") for e in bib]
        assert "10.9999/new" in dois


def test_bibliography_custom_filename(tmp_path):
    mgr = StateManager(state_dir=tmp_path, bibliography_file="refs.json")
    entry = BibliographyEntry(
        doi="10.1234/foo", title="Test", url="https://doi.org/10.1234/foo",
        shared_at=datetime(2024, 1, 1, tzinfo=timezone.utc), platforms=["bluesky"],
    )
    mgr.append_bibliography(entry)
    assert (tmp_path / "refs.json").exists()
    assert not (tmp_path / "bibliography.json").exists()


def test_audit_rotation_renames_file_when_size_exceeded(tmp_path):
    """Rotation triggers when audit file reaches rotation_max_bytes."""
    mgr = StateManager(
        state_dir=tmp_path,
        audit_file=tmp_path / "audit.jsonl",
        audit_rotation_max_bytes=50,  # tiny threshold
    )
    # Write a record that exceeds 50 bytes
    with mgr.lock():
        mgr.append_audit({"timestamp": "2024-01-01T00:00:00Z", "status": "posted", "platform": "bluesky"})
    # The original audit.jsonl should no longer exist; an archive should
    archives = list(tmp_path.glob("audit.*.jsonl"))
    assert len(archives) == 1
    assert (tmp_path / "audit.jsonl").stat().st_size < 50  # new file is smaller


def test_audit_rotation_disabled_when_zero(tmp_path):
    """rotation_max_bytes=0 disables rotation; file grows normally."""
    mgr = StateManager(
        state_dir=tmp_path,
        audit_file=tmp_path / "audit.jsonl",
        audit_rotation_max_bytes=0,
    )
    with mgr.lock():
        mgr.append_audit({"timestamp": "2024-01-01T00:00:00Z", "status": "posted"})
        mgr.append_audit({"timestamp": "2024-01-02T00:00:00Z", "status": "posted"})
    assert not list(tmp_path.glob("audit.*.jsonl"))  # no archives


def test_audit_prune_removes_old_records(tmp_path):
    """prune_audit removes records older than retention_days."""
    audit_file = tmp_path / "audit.jsonl"
    # Write 3 records: 2 old (40 days ago), 1 recent (1 day ago)
    old1 = {"timestamp": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), "status": "posted"}
    old2 = {"timestamp": (datetime.now(timezone.utc) - timedelta(days=35)).isoformat(), "status": "failed"}
    recent = {"timestamp": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "status": "posted"}
    audit_file.write_text(
        json.dumps(old1) + "\n" + json.dumps(old2) + "\n" + json.dumps(recent) + "\n"
    )
    mgr = StateManager(
        state_dir=tmp_path,
        audit_file=audit_file,
        audit_retention_days=30,
    )
    with mgr.lock():
        pruned = mgr.prune_audit()
    assert pruned == 2
    lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "posted"


def test_audit_prune_noop_when_disabled(tmp_path):
    """retention_days=0 → prune_audit() is a no-op, returns 0."""
    audit_file = tmp_path / "audit.jsonl"
    old = {"timestamp": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(), "status": "posted"}
    audit_file.write_text(json.dumps(old) + "\n")
    mgr = StateManager(state_dir=tmp_path, audit_file=audit_file, audit_retention_days=0)
    with mgr.lock():
        pruned = mgr.prune_audit()
    assert pruned == 0
    # File unchanged
    assert audit_file.read_text().strip()
