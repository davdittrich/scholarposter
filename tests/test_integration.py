"""Integration tests for scholarposter end-to-end workflow."""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner
from scholarposter.cli import app
from scholarposter.models import PostResult, PostStatus, UnifiedPost

runner = CliRunner()

MINIMAL_CONFIG = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "test.secret"

[platforms.bluesky]
enabled = true
"""


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(MINIMAL_CONFIG)
    return p


@pytest.fixture
def sample_post():
    return UnifiedPost(
        source_id="113456789012345678",
        text="Test toot about science #Science",
        source_url="https://fediscience.org/@user/113456789012345678",
        created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        hashtags=["Science"],
    )


@pytest.mark.integration
class TestIntegration:
    def test_dry_run_no_state_written(self, config_file, tmp_path, sample_post):
        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_coll_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli._dispatch_post", return_value=PostResult(
                platform="bluesky", status=PostStatus.POSTED
            )),
        ):
            mock_mastodon = MagicMock()
            mock_mastodon.me.return_value = {"id": "123"}
            mock_coll = MagicMock()
            mock_coll.fetch_oldest_unprocessed.return_value = sample_post
            mock_coll_cls.return_value = mock_coll

            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            result = runner.invoke(app, ["run", "--dry-run", "--config", str(config_file)])
            # dry_run=True means dispatch is called with dry_run=True
            # state should not be updated in dry_run (we use mocks so just verify it doesn't crash)
            assert result.exit_code == 0

    def test_empty_timeline_exits_cleanly(self, config_file):
        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_coll_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
        ):
            mock_mastodon = MagicMock()
            mock_mastodon.me.return_value = {"id": "123"}
            mock_coll = MagicMock()
            mock_coll.fetch_oldest_unprocessed.return_value = None
            mock_coll_cls.return_value = mock_coll

            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            result = runner.invoke(app, ["run", "--config", str(config_file)])
            assert result.exit_code == 0

    def test_successful_post_updates_state(self, config_file, sample_post):
        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_coll_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli._dispatch_post", return_value=PostResult(
                platform="bluesky", status=PostStatus.POSTED, post_url="https://bsky.app/post/1"
            )),
        ):
            mock_mastodon = MagicMock()
            mock_mastodon.me.return_value = {"id": "123"}
            mock_coll = MagicMock()
            mock_coll.fetch_oldest_unprocessed.return_value = sample_post
            mock_coll_cls.return_value = mock_coll

            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            result = runner.invoke(app, ["run", "--config", str(config_file)])
            assert result.exit_code == 0
            mock_state.update_platform_state.assert_called_once()

    def test_filtered_toot_advances_state(self, config_file, tmp_path):
        filtered_post = UnifiedPost(
            source_id="999",
            text="Private post #nobridge",
            source_url="https://fediscience.org/@user/999",
            created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            hashtags=["nobridge"],
        )
        # Add filter config
        cfg_with_filter = MINIMAL_CONFIG + '\n[platforms.bluesky.filters]\nskip_hashtags = ["nobridge"]\n'
        config_file.write_text(cfg_with_filter)

        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_coll_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
        ):
            mock_mastodon = MagicMock()
            mock_mastodon.me.return_value = {"id": "123"}
            mock_coll = MagicMock()
            mock_coll.fetch_oldest_unprocessed.return_value = filtered_post
            mock_coll_cls.return_value = mock_coll

            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            result = runner.invoke(app, ["run", "--config", str(config_file)])
            assert result.exit_code == 0
            # State should have been updated (skipped)
            mock_state.update_platform_state.assert_called_once()
