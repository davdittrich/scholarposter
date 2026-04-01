"""Tests for scholarposter.migration"""
import json
import pytest
from pathlib import Path
from scholarposter.migration import migrate_state_files


@pytest.fixture
def source_dir(tmp_path):
    return tmp_path


@pytest.fixture
def target_dir(tmp_path):
    d = tmp_path / "target"
    d.mkdir()
    return d


class TestMigrateStateFiles:
    def test_migrates_bluesky_state(self, source_dir, target_dir):
        (source_dir / "lasttoot_bluesky.txt").write_text("113456789012345678")
        migrate_state_files(source_dir, target_dir)
        state_file = target_dir / "state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["bluesky"]["last_toot_id"] == 113456789012345678

    def test_migrates_linkedin_state(self, source_dir, target_dir):
        (source_dir / "lasttoot.txt").write_text("113456789000000001")
        migrate_state_files(source_dir, target_dir)
        state = json.loads((target_dir / "state.json").read_text())
        assert state["linkedin"]["last_toot_id"] == 113456789000000001

    def test_skips_missing_files(self, source_dir, target_dir):
        # No lasttoot files — should not raise
        migrate_state_files(source_dir, target_dir)
        # state.json may or may not exist, but no exception

    def test_does_not_overwrite_existing_state(self, source_dir, target_dir):
        (source_dir / "lasttoot_bluesky.txt").write_text("999")
        existing_state = {"bluesky": {"last_toot_id": 12345}}
        (target_dir / "state.json").write_text(json.dumps(existing_state))
        migrate_state_files(source_dir, target_dir)
        state = json.loads((target_dir / "state.json").read_text())
        # Existing state should NOT be overwritten
        assert state["bluesky"]["last_toot_id"] == 12345

    def test_both_files_migrated(self, source_dir, target_dir):
        (source_dir / "lasttoot_bluesky.txt").write_text("100")
        (source_dir / "lasttoot.txt").write_text("200")
        migrate_state_files(source_dir, target_dir)
        state = json.loads((target_dir / "state.json").read_text())
        assert "bluesky" in state
        assert "linkedin" in state
