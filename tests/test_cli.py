"""Tests for scholarposter.cli"""
import os
import stat
import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

from scholarposter.cli import app, _redact, _build_notifiers, _print_masked_config
from scholarposter.config import NotificationBackendConfig
from scholarposter.models import PostResult, PostStatus

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helper: minimal config TOML content
# ---------------------------------------------------------------------------

_BASE_TOML = (
    '[mastodon]\n'
    'instance="https://fediscience.org"\n'
    'credentials_file="test.secret"\n'
)

_BSKY_TOML = (
    '[mastodon]\n'
    'instance="https://fediscience.org"\n'
    'credentials_file="test.secret"\n'
    '\n'
    '[platforms.bluesky]\n'
    'enabled=true\n'
)


# ---------------------------------------------------------------------------
# _redact — pure function, no loguru needed
# ---------------------------------------------------------------------------

class TestRedact:
    def test_redacts_bearer_token(self):
        msg = "Authorization: Bearer abc123secret"
        assert "abc123secret" not in _redact(msg)
        assert "Bearer [REDACTED]" in _redact(msg)

    def test_redacts_password_param(self):
        msg = "login password=mySecret123 failed"
        result = _redact(msg)
        assert "mySecret123" not in result
        assert "password=[REDACTED]" in result

    def test_redacts_token_param(self):
        msg = "token=AQXblah123 expired"
        result = _redact(msg)
        assert "AQXblah123" not in result

    def test_redacts_secret_param(self):
        msg = "secret=s3cr3t!"
        result = _redact(msg)
        assert "s3cr3t!" not in result

    def test_leaves_safe_text_unchanged(self):
        msg = "Posting toot 123 to bluesky"
        assert _redact(msg) == msg

    def test_case_insensitive_param_redaction(self):
        msg = "Password=hunter2"
        result = _redact(msg)
        assert "hunter2" not in result


# ---------------------------------------------------------------------------
# _build_notifiers
# ---------------------------------------------------------------------------

class TestBuildNotifiers:
    def test_ntfy_backend_created(self):
        cfg = [NotificationBackendConfig(type="ntfy", topic="my-topic")]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 1
        from scholarposter.notifications.ntfy import NtfyNotifier
        assert isinstance(notifiers[0], NtfyNotifier)

    def test_ntfy_uses_custom_server(self):
        cfg = [NotificationBackendConfig(type="ntfy", topic="t", server="https://custom.ntfy")]
        notifiers = _build_notifiers(cfg)
        assert notifiers[0]._server == "https://custom.ntfy"

    def test_ntfy_defaults_to_ntfysh(self):
        cfg = [NotificationBackendConfig(type="ntfy", topic="t")]
        notifiers = _build_notifiers(cfg)
        assert "ntfy.sh" in notifiers[0]._server

    def test_ntfy_without_topic_skipped(self):
        cfg = [NotificationBackendConfig(type="ntfy")]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 0

    def test_unknown_type_skipped(self):
        cfg = [NotificationBackendConfig(type="carrier_pigeon")]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 0

    def test_signal_type_skipped_without_required_fields(self):
        cfg = [NotificationBackendConfig(type="signal")]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 0

    def test_email_type_skipped_without_required_fields(self):
        cfg = [NotificationBackendConfig(type="email")]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 0

    def test_signal_backend_created_with_config(self):
        cfg = [NotificationBackendConfig(
            type="signal", api_url="http://localhost:8080",
            phone_number="+1234567890", recipients=["+0987654321"],
        )]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 1
        from scholarposter.notifications.signal import SignalNotifier
        assert isinstance(notifiers[0], SignalNotifier)

    def test_email_backend_created_with_config(self):
        cfg = [NotificationBackendConfig(
            type="email", smtp_host="smtp.test",
            from_addr="a@b.com", to_addr="c@d.com",
        )]
        notifiers = _build_notifiers(cfg)
        assert len(notifiers) == 1
        from scholarposter.notifications.email import EmailNotifier
        assert isinstance(notifiers[0], EmailNotifier)

    def test_unknown_type_warning_message(self):
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            _build_notifiers([NotificationBackendConfig(type="fax_machine")])
        finally:
            logger.remove(lid)
        assert any("Unknown notification backend type" in m for m in messages)

    def test_empty_backends_returns_empty(self):
        assert _build_notifiers([]) == []


# ---------------------------------------------------------------------------
# .env permission check
# ---------------------------------------------------------------------------

class TestEnvPermissions:
    def test_world_readable_env_triggers_warning(self, tmp_path, capfd):
        env_file = tmp_path / ".env"
        env_file.write_text('BLUESKY_EMAIL="x@example.com"\n')
        env_file.chmod(0o644)

        with patch("scholarposter.cli.find_dotenv", return_value=str(env_file)):
            from scholarposter.cli import _check_env_permissions
            import io
            messages = []
            from loguru import logger
            lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
            try:
                _check_env_permissions()
            finally:
                logger.remove(lid)
        assert any("unsafe" in m or "600" in m for m in messages)

    def test_secure_env_no_warning(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('BLUESKY_EMAIL="x@example.com"\n')
        env_file.chmod(0o600)

        with patch("scholarposter.cli.find_dotenv", return_value=str(env_file)):
            from scholarposter.cli import _check_env_permissions
            messages = []
            from loguru import logger
            lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
            try:
                _check_env_permissions()
            finally:
                logger.remove(lid)
        assert not any("unsafe" in m or "chmod" in m for m in messages)

    def test_missing_env_no_error(self):
        with patch("scholarposter.cli.find_dotenv", return_value=""):
            from scholarposter.cli import _check_env_permissions
            # Should not raise
            _check_env_permissions()


# ---------------------------------------------------------------------------
# Enrichment pipeline wired in run()
# ---------------------------------------------------------------------------

class TestEnrichmentWired:
    def test_pipeline_enrich_called_before_dispatch(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)

        mock_post = MagicMock()
        mock_post.source_id = "123"
        mock_post.hashtags = []
        mock_enriched = MagicMock()
        mock_enriched.source_id = "123"
        mock_enriched.hashtags = []

        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli.evaluate_filters") as mock_filter,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            mock_collector = MagicMock()
            mock_collector.fetch_oldest_unprocessed.return_value = mock_post
            mock_col_cls.return_value = mock_collector

            mock_pipeline = MagicMock()
            mock_pipeline.enrich.return_value = mock_enriched
            mock_pipe_cls.return_value = mock_pipeline

            call_order = []
            mock_filter.side_effect = lambda p, f: (
                call_order.append("filter"), MagicMock(passed=True)
            )[1]
            mock_pipeline.enrich.side_effect = lambda p: (
                call_order.append("enrich"), mock_enriched
            )[1]
            mock_dispatch.return_value = PostResult(platform="bluesky", status=PostStatus.POSTED)

            result = runner.invoke(app, ["run", "--config", str(config_file)])

        mock_pipeline.enrich.assert_called_once()
        mock_dispatch.assert_called_once()
        # Ensure dispatch received the enriched post
        assert mock_dispatch.call_args[0][1] is mock_enriched
        # FR-8: filter must evaluate BEFORE enrichment
        assert call_order.index("filter") < call_order.index("enrich")


# ---------------------------------------------------------------------------
# Notification dispatch
# ---------------------------------------------------------------------------

class TestNotificationDispatch:
    def _make_mocks(self, tmp_path, post_status=PostStatus.FAILED):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            _BSKY_TOML
            + '\n[[notifications.backends]]\ntype="ntfy"\ntopic="alerts"\n'
        )

        mock_post = MagicMock()
        mock_post.source_id = "123"
        mock_post.hashtags = []

        return config_file, mock_post

    def test_notifier_called_on_failed(self, tmp_path):
        config_file, mock_post = self._make_mocks(tmp_path)

        mock_notifier = MagicMock()

        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli.evaluate_filters") as mock_filter,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli._build_notifiers", return_value=[mock_notifier]),
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            mock_collector = MagicMock()
            mock_collector.fetch_oldest_unprocessed.return_value = mock_post
            mock_col_cls.return_value = mock_collector

            mock_pipe = MagicMock()
            mock_pipe.enrich.return_value = mock_post
            mock_pipe_cls.return_value = mock_pipe

            mock_filter.return_value = MagicMock(passed=True)
            mock_dispatch.return_value = PostResult(
                platform="bluesky", status=PostStatus.FAILED, error="boom"
            )

            runner.invoke(app, ["run", "--config", str(config_file)])

        mock_notifier.notify.assert_called_once_with("bluesky", "123", "boom")

    def test_notifier_not_called_on_success(self, tmp_path):
        config_file, mock_post = self._make_mocks(tmp_path)
        mock_notifier = MagicMock()

        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli.evaluate_filters") as mock_filter,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli._build_notifiers", return_value=[mock_notifier]),
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            mock_collector = MagicMock()
            mock_collector.fetch_oldest_unprocessed.return_value = mock_post
            mock_col_cls.return_value = mock_collector

            mock_pipe = MagicMock()
            mock_pipe.enrich.return_value = mock_post
            mock_pipe_cls.return_value = mock_pipe

            mock_filter.return_value = MagicMock(passed=True)
            mock_dispatch.return_value = PostResult(
                platform="bluesky", status=PostStatus.POSTED, post_url="https://bsky.app/..."
            )

            runner.invoke(app, ["run", "--config", str(config_file)])

        mock_notifier.notify.assert_not_called()


# ---------------------------------------------------------------------------
# CLI help / subcommand existence
# ---------------------------------------------------------------------------

class TestCliHelp:
    def test_help_works(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_run_has_platform_option(self):
        result = runner.invoke(app, ["run", "--help"])
        assert "--platform" in result.output

    def test_run_has_verbose_option(self):
        result = runner.invoke(app, ["run", "--help"])
        assert "--verbose" in result.output

    def test_run_has_quiet_option(self):
        result = runner.invoke(app, ["run", "--help"])
        assert "--quiet" in result.output

    def test_retry_subcommand_exists(self):
        result = runner.invoke(app, ["retry", "--help"])
        assert result.exit_code == 0
        assert "--toot-id" in result.output

    def test_config_subcommand_exists(self):
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0

    def test_status_subcommand_exists(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0


class TestCliRun:
    def test_invalid_platform_rejected(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)
        result = runner.invoke(app, ["run", "--platform", "invalid_platform", "--config", str(config_file)])
        assert result.exit_code != 0

    def test_dry_run_flag_accepted(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)
        with (
            patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
            patch("scholarposter.cli.MastodonCollector") as mock_collector_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline"),
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_mastodon = MagicMock()
            mock_mastodon_cls.return_value = mock_mastodon
            mock_collector = MagicMock()
            mock_collector.fetch_oldest_unprocessed.return_value = None
            mock_collector_cls.return_value = mock_collector
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state

            result = runner.invoke(app, ["run", "--dry-run", "--config", str(config_file)])
            assert result.exit_code in (0, 1)

    def test_run_exits_zero_when_lock_held(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)
        with (
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = False  # lock held
            mock_state_cls.return_value = mock_state
            result = runner.invoke(app, ["run", "--config", str(config_file)])
        assert result.exit_code == 0

    def test_mastodon_me_not_called_when_no_enabled_platforms(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[mastodon]\ninstance="https://fediscience.org"\ncredentials_file="t.secret"\n'
            '\n[platforms.bluesky]\nenabled=false\n'
        )
        with (
            patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline"),
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_mastodon = MagicMock()
            mock_mastodon_cls.return_value = mock_mastodon
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state_cls.return_value = mock_state
            runner.invoke(app, ["run", "--config", str(config_file)])
        mock_mastodon.me.assert_not_called()

    def test_run_sets_last_posted_at_on_success(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        mock_post = MagicMock()
        mock_post.source_id = "123"
        mock_post.hashtags = []
        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli.evaluate_filters") as mock_filter,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state
            mock_col_cls.return_value.fetch_oldest_unprocessed.return_value = mock_post
            mock_pipe_cls.return_value.enrich.return_value = mock_post
            mock_filter.return_value = MagicMock(passed=True)
            mock_dispatch.return_value = PostResult(platform="bluesky", status=PostStatus.POSTED, post_url="https://bsky.app/p/1")
            runner.invoke(app, ["run", "--config", str(config_file)])
        ps_arg = mock_state.update_platform_state.call_args[0][1]
        assert ps_arg.last_posted_at is not None

    def test_run_no_last_posted_at_on_failure(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        mock_post = MagicMock()
        mock_post.source_id = "123"
        mock_post.hashtags = []
        with (
            patch("scholarposter.cli.Mastodon"),
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli.evaluate_filters") as mock_filter,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state.get_since_id.return_value = None
            mock_state_cls.return_value = mock_state
            mock_col_cls.return_value.fetch_oldest_unprocessed.return_value = mock_post
            mock_pipe_cls.return_value.enrich.return_value = mock_post
            mock_filter.return_value = MagicMock(passed=True)
            mock_dispatch.return_value = PostResult(platform="bluesky", status=PostStatus.FAILED, error="boom")
            runner.invoke(app, ["run", "--config", str(config_file)])
        ps_arg = mock_state.update_platform_state.call_args[0][1]
        assert ps_arg.last_posted_at is None


# ---------------------------------------------------------------------------
# retry command
# ---------------------------------------------------------------------------

class TestRetryCommand:
    def test_retry_requires_platform(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        result = runner.invoke(app, ["retry", "--config", str(config_file), "--toot-id", "123"])
        assert result.exit_code != 0

    def test_retry_requires_toot_id(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        result = runner.invoke(app, ["retry", "--config", str(config_file), "--platform", "bluesky"])
        assert result.exit_code != 0

    def test_retry_invalid_platform_rejected(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        result = runner.invoke(app, [
            "retry", "--config", str(config_file),
            "--platform", "twitter", "--toot-id", "123",
        ])
        assert result.exit_code != 0

    def test_retry_fetches_toot_by_id_and_dispatches(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)

        mock_raw_toot = {"id": "999", "content": "<p>Hi</p>", "tags": [], "media_attachments": [], "created_at": "2024-01-01T00:00:00Z"}
        mock_post = MagicMock()
        mock_post.source_id = "999"
        mock_post.hashtags = []

        with (
            patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_mastodon = MagicMock()
            mock_mastodon.status.return_value = mock_raw_toot
            mock_mastodon_cls.return_value = mock_mastodon

            mock_collector = MagicMock()
            mock_collector._toot_to_unified_post.return_value = mock_post
            mock_col_cls.return_value = mock_collector

            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state_cls.return_value = mock_state

            mock_pipe = MagicMock()
            mock_pipe.enrich.return_value = mock_post
            mock_pipe_cls.return_value = mock_pipe

            mock_dispatch.return_value = PostResult(
                platform="bluesky", status=PostStatus.POSTED, post_url="https://bsky.app/p/1"
            )

            result = runner.invoke(app, [
                "retry", "--config", str(config_file),
                "--platform", "bluesky", "--toot-id", "999",
            ])

        mock_mastodon.status.assert_called_once_with(999)
        mock_collector._toot_to_unified_post.assert_called_once_with(mock_raw_toot)
        mock_dispatch.assert_called_once()

    def test_retry_only_updates_specified_platform_state(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)

        mock_post = MagicMock()
        mock_post.source_id = "999"

        with (
            patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_mastodon = MagicMock()
            mock_mastodon.status.return_value = {}
            mock_mastodon_cls.return_value = mock_mastodon

            mock_collector = MagicMock()
            mock_collector._toot_to_unified_post.return_value = mock_post
            mock_col_cls.return_value = mock_collector

            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state_cls.return_value = mock_state

            mock_pipe = MagicMock()
            mock_pipe.enrich.return_value = mock_post
            mock_pipe_cls.return_value = mock_pipe

            mock_dispatch.return_value = PostResult(
                platform="bluesky", status=PostStatus.POSTED
            )

            runner.invoke(app, [
                "retry", "--config", str(config_file),
                "--platform", "bluesky", "--toot-id", "999",
            ])

        # Only bluesky state update should have been called
        update_calls = mock_state.update_platform_state.call_args_list
        assert len(update_calls) == 1
        assert update_calls[0][0][0] == "bluesky"


# ---------------------------------------------------------------------------
# config validate command
# ---------------------------------------------------------------------------

class TestConfigValidate:
    def test_validate_prints_config(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)
        result = runner.invoke(app, ["config", "--config", str(config_file), "validate"])
        assert result.exit_code == 0
        assert "instance" in result.output

    def test_validate_redacts_credentials_file(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)
        result = runner.invoke(app, ["config", "--config", str(config_file), "validate"])
        assert result.exit_code == 0
        # credentials_file value should be redacted
        assert "test.secret" not in result.output
        assert "[REDACTED]" in result.output

    def test_validate_unknown_action_fails(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)
        # "export" is not a valid action; existing code returns exit code 2
        result = runner.invoke(app, ["config", "--config", str(config_file), "export"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------

class TestStatusCommand:
    def test_status_shows_last_error(self, tmp_path):
        from scholarposter.state import StateManager
        state_file = tmp_path / "state.json"
        state_mgr = StateManager(state_file=str(state_file))
        from scholarposter.models import PlatformState
        state_mgr.update_platform_state("bluesky", PlatformState(
            last_toot_id=42,
            last_status="failed",
            last_error="timeout connecting to bsky.social",
        ))

        with patch("scholarposter.cli.load_config") as mock_cfg, \
             patch("scholarposter.cli.StateManager") as mock_sm_cls, \
             patch("scholarposter.cli.Mastodon"):
            mock_cfg.return_value.state.state_file = str(state_file)
            real_state = StateManager(state_file=str(state_file))
            mock_sm_cls.return_value = real_state

            config_file = tmp_path / "config.toml"
            config_file.write_text(_BASE_TOML)
            result = runner.invoke(app, ["status", "--config", str(config_file)])

        assert result.exit_code == 0
        assert "last_error=timeout connecting to bsky.social" in result.output

    def test_status_shows_pending_count(self, tmp_path):
        from scholarposter.state import StateManager as RealSM
        from scholarposter.models import PlatformState
        state_file = tmp_path / "state.json"
        sm = RealSM(state_file=str(state_file))
        sm.update_platform_state("bluesky", PlatformState(
            last_toot_id=100, last_status="posted",
        ))

        with (
            patch("scholarposter.cli.load_config") as mock_cfg,
            patch("scholarposter.cli.StateManager") as mock_sm_cls,
            patch("scholarposter.cli.Mastodon") as mock_masto_cls,
        ):
            mock_cfg.return_value.state.state_file = str(state_file)
            mock_cfg.return_value.mastodon.credentials_file = "t.secret"
            mock_cfg.return_value.mastodon.instance = "https://fediscience.org"
            mock_sm_cls.return_value = RealSM(state_file=str(state_file))
            mock_masto = MagicMock()
            mock_masto.me.return_value = {"id": "42"}
            mock_masto.account_statuses.return_value = [{}, {}, {}]  # 3 pending
            mock_masto_cls.return_value = mock_masto
            config_file = tmp_path / "config.toml"
            config_file.write_text(_BASE_TOML)
            result = runner.invoke(app, ["status", "--config", str(config_file)])
        assert "pending=3" in result.output

    def test_status_shows_pending_unknown_on_api_failure(self, tmp_path):
        from scholarposter.state import StateManager as RealSM
        from scholarposter.models import PlatformState
        state_file = tmp_path / "state.json"
        sm = RealSM(state_file=str(state_file))
        sm.update_platform_state("bluesky", PlatformState(
            last_toot_id=100, last_status="posted",
        ))

        with (
            patch("scholarposter.cli.load_config") as mock_cfg,
            patch("scholarposter.cli.StateManager") as mock_sm_cls,
            patch("scholarposter.cli.Mastodon") as mock_masto_cls,
        ):
            mock_cfg.return_value.state.state_file = str(state_file)
            mock_cfg.return_value.mastodon.credentials_file = "t.secret"
            mock_cfg.return_value.mastodon.instance = "https://fediscience.org"
            mock_sm_cls.return_value = RealSM(state_file=str(state_file))
            mock_masto_cls.side_effect = Exception("auth failed")
            config_file = tmp_path / "config.toml"
            config_file.write_text(_BASE_TOML)
            result = runner.invoke(app, ["status", "--config", str(config_file)])
        assert "pending=?" in result.output


# ---------------------------------------------------------------------------
# _print_masked_config list-of-dicts
# ---------------------------------------------------------------------------

class TestPrintMaskedConfig:
    def test_list_of_dicts_has_bullet_prefix(self, capsys):
        from scholarposter.cli import _print_masked_config

        # Capture typer.echo output via capsys
        _print_masked_config([{"type": "ntfy", "topic": "alerts"}])
        captured = capsys.readouterr()
        assert "- type: ntfy" in captured.out

    def test_list_of_dicts_sensitive_field_redacted(self, capsys):
        from scholarposter.cli import _print_masked_config
        _print_masked_config([{"access_token": "secret123", "platform": "bluesky"}])
        captured = capsys.readouterr()
        assert "secret123" not in captured.out
        assert "[REDACTED]" in captured.out


# ---------------------------------------------------------------------------
# retry — lock released when platform not in cfg
# ---------------------------------------------------------------------------

class TestRetryLockRelease:
    def test_unconfigured_platform_releases_lock(self, tmp_path):
        # Config with NO platforms section
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BASE_TOML)  # no [platforms.*] section

        with (
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state_cls.return_value = mock_state

            result = runner.invoke(app, [
                "retry", "--config", str(config_file),
                "--platform", "bluesky", "--toot-id", "999",
            ])

        # Lock must be released even though platform was not configured
        mock_state.release_lock.assert_called_once()
        assert result.exit_code != 0

    def test_retry_exits_zero_when_lock_held(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        with (
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = False  # lock held
            mock_state_cls.return_value = mock_state
            result = runner.invoke(app, [
                "retry", "--config", str(config_file),
                "--platform", "bluesky", "--toot-id", "999",
            ])
        assert result.exit_code == 0

    def test_retry_sets_last_posted_at_on_success(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(_BSKY_TOML)
        mock_post = MagicMock()
        mock_post.source_id = "999"
        with (
            patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
            patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
            patch("scholarposter.cli.StateManager") as mock_state_cls,
            patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
            patch("scholarposter.cli._dispatch_post") as mock_dispatch,
            patch("scholarposter.cli.find_dotenv", return_value=""),
        ):
            mock_mastodon_cls.return_value.status.return_value = {}
            mock_col_cls.return_value._toot_to_unified_post.return_value = mock_post
            mock_state = MagicMock()
            mock_state.acquire_lock.return_value = True
            mock_state_cls.return_value = mock_state
            mock_pipe_cls.return_value.enrich.return_value = mock_post
            mock_dispatch.return_value = PostResult(
                platform="bluesky", status=PostStatus.POSTED, post_url="https://bsky.app/p/1"
            )
            runner.invoke(app, [
                "retry", "--config", str(config_file),
                "--platform", "bluesky", "--toot-id", "999",
            ])
        ps_arg = mock_state.update_platform_state.call_args[0][1]
        assert ps_arg.last_posted_at is not None
