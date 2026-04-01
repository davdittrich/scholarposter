"""Tests for scholarposter.state"""
import json
import os
import time
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
