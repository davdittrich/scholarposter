"""Tests for scholarposter.state"""
import json
import os
import time
from unittest.mock import patch
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from scholarposter.state import StateManager
from scholarposter.models import PlatformState


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


class TestPruneCacheNoWrite:
    """_prune_cache must not call _save_cache when nothing expired."""

    def test_prune_cache_no_write_when_nothing_expired(self, mgr):
        mgr.cache_set("key1", {"x": 1}, ttl_days=7)
        mgr.cache_set("key2", {"x": 2}, ttl_days=7)
        with patch.object(mgr, "_save_cache") as mock_save:
            mgr._prune_cache()
            mock_save.assert_not_called()
