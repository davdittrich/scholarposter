"""Tests for scholarposter auth mastodon command (OAuth code flow)."""
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from scholarposter.auth.cli import auth_app, _update_config_toml

runner = CliRunner()


def _mock_mastodon_class(tmp_path):
    """Create a mock Mastodon class that writes credential files on create_app/log_in."""
    client_cred = tmp_path / "pytooter_clientcred.secret"
    user_cred = tmp_path / "pytooter_usercred.secret"

    def mock_create_app(*args, **kwargs):
        to_file = kwargs.get("to_file")
        if to_file:
            Path(to_file).write_text("client_cred_content")

    mock_instance = MagicMock()
    mock_instance.auth_request_url.return_value = "https://mastodon.example/oauth/authorize?code=test"

    def mock_log_in(*args, **kwargs):
        to_file = kwargs.get("to_file")
        if to_file:
            Path(to_file).write_text("user_cred_content")
        return "access_token_123"

    mock_instance.log_in = mock_log_in

    MockMastodon = MagicMock()
    MockMastodon.create_app = mock_create_app
    MockMastodon.return_value = mock_instance

    return MockMastodon, mock_instance


class TestAuthMastodonHappyPath:
    def test_desktop_flow(self, tmp_path):
        """OAuth desktop flow: browser opens, callback captured, token exchanged."""
        config = tmp_path / "config.toml"
        config.write_text('[enrichment]\nenabled = true\n')

        MockMastodon, _ = _mock_mastodon_class(tmp_path)

        with (
            patch("mastodon.Mastodon", MockMastodon),
            patch("scholarposter.auth.cli.is_headless", return_value=False),
            patch("scholarposter.auth.cli.webbrowser.open"),
            patch("scholarposter.auth.cli.wait_for_callback_desktop", return_value="auth_code_xyz"),
        ):
            result = runner.invoke(auth_app, [
                "mastodon", "--config", str(config), "--port", "18999",
            ], input="https://fediscience.org\ny\n")

        assert result.exit_code == 0, result.output
        assert "Mastodon authorized" in result.output

        from scholarposter.env_writer import read_env
        env = read_env(tmp_path / ".env")
        assert env["MASTODON_INSTANCE"] == "https://fediscience.org"
        assert "MASTODON_PASSWORD" not in env
        assert "MASTODON_EMAIL" not in env

    def test_headless_oob_flow(self, tmp_path, monkeypatch):
        """Headless OOB flow: user pastes code directly."""
        config = tmp_path / "config.toml"
        config.write_text("")

        MockMastodon, _ = _mock_mastodon_class(tmp_path)

        with (
            patch("mastodon.Mastodon", MockMastodon),
            patch("scholarposter.auth.cli.is_headless", return_value=True),
        ):
            result = runner.invoke(auth_app, [
                "mastodon", "--config", str(config),
            ], input="https://fediscience.org\ny\noob_auth_code\n")

        assert result.exit_code == 0, result.output
        assert "Mastodon authorized" in result.output


class TestAuthMastodonErrors:
    def test_instance_unreachable(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app.side_effect = Exception("Connection refused")
            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)],
                                   input="https://down.social\n")

        assert result.exit_code == 2
        assert "Could not reach" in result.output

    def test_url_normalization(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")

        MockMastodon, _ = _mock_mastodon_class(tmp_path)

        with (
            patch("mastodon.Mastodon", MockMastodon),
            patch("scholarposter.auth.cli.is_headless", return_value=True),
        ):
            result = runner.invoke(auth_app, [
                "mastodon", "--config", str(config),
            ], input="fediscience.org\ny\noob_code\n")

        assert result.exit_code == 0
        from scholarposter.env_writer import read_env
        env = read_env(tmp_path / ".env")
        assert env["MASTODON_INSTANCE"] == "https://fediscience.org"

    def test_rejects_http_scheme(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")

        result = runner.invoke(auth_app, ["mastodon", "--config", str(config)],
                               input="http://insecure.social\n")

        assert result.exit_code == 2
        assert "HTTPS" in result.output

    def test_authorization_denied(self, tmp_path):
        from scholarposter.auth.callback import OAuthError
        config = tmp_path / "config.toml"
        config.write_text("")

        MockMastodon, _ = _mock_mastodon_class(tmp_path)

        with (
            patch("mastodon.Mastodon", MockMastodon),
            patch("scholarposter.auth.cli.is_headless", return_value=False),
            patch("scholarposter.auth.cli.webbrowser.open"),
            patch("scholarposter.auth.cli.wait_for_callback_desktop",
                  side_effect=OAuthError("Authorization denied.")),
        ):
            result = runner.invoke(auth_app, [
                "mastodon", "--config", str(config),
            ], input="https://test.social\ny\n")

        assert result.exit_code == 2
        assert "denied" in result.output.lower()

    def test_token_exchange_failure(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")

        MockMastodon, mock_instance = _mock_mastodon_class(tmp_path)
        mock_instance.log_in = MagicMock(side_effect=Exception("invalid_grant"))

        with (
            patch("mastodon.Mastodon", MockMastodon),
            patch("scholarposter.auth.cli.is_headless", return_value=True),
        ):
            result = runner.invoke(auth_app, [
                "mastodon", "--config", str(config),
            ], input="https://test.social\ny\noob_code\n")

        assert result.exit_code == 2
        assert "Token exchange failed" in result.output


class TestConfigTomlUpdate:
    def test_updates_mastodon_section(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text('[enrichment]\nenabled = true\n\n[logging]\nlevel = "INFO"\n')

        _update_config_toml(config, "https://test.social", "/path/to/cred.secret")

        import tomllib
        with open(config, "rb") as f:
            data = tomllib.load(f)
        assert data["mastodon"]["instance"] == "https://test.social"
        assert data["mastodon"]["credentials_file"] == "/path/to/cred.secret"
        assert data["enrichment"]["enabled"] is True
        assert data["logging"]["level"] == "INFO"

    def test_creates_config_if_missing(self, tmp_path):
        config = tmp_path / "config.toml"
        _update_config_toml(config, "https://new.social", "cred.secret")

        import tomllib
        with open(config, "rb") as f:
            data = tomllib.load(f)
        assert data["mastodon"]["instance"] == "https://new.social"

    def test_secret_file_permissions(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")

        MockMastodon, _ = _mock_mastodon_class(tmp_path)

        with (
            patch("mastodon.Mastodon", MockMastodon),
            patch("scholarposter.auth.cli.is_headless", return_value=True),
        ):
            result = runner.invoke(auth_app, [
                "mastodon", "--config", str(config),
            ], input="https://test.social\ny\noob_code\n")

        assert result.exit_code == 0, result.output
        client_cred = tmp_path / "pytooter_clientcred.secret"
        user_cred = tmp_path / "pytooter_usercred.secret"
        if client_cred.exists():
            assert stat.S_IMODE(os.stat(client_cred).st_mode) == 0o600
        if user_cred.exists():
            assert stat.S_IMODE(os.stat(user_cred).st_mode) == 0o600
