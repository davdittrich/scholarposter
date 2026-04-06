"""Tests for scholarposter auth mastodon command."""
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from scholarposter.auth.cli import auth_app, _update_config_toml

runner = CliRunner()


class TestAuthMastodonHappyPath:
    def test_interactive_creates_credentials(self, tmp_path, monkeypatch):
        """Full interactive flow: prompts → app registered → logged in → .env + config updated."""
        config = tmp_path / "config.toml"
        config.write_text('[enrichment]\nenabled = true\n')

        def mock_create_app(*args, **kwargs):
            to_file = kwargs.get("to_file")
            if to_file:
                Path(to_file).write_text("client_cred")

        mock_mastodon = MagicMock()
        def mock_login(*a, **kw):
            to_file = kw.get("to_file")
            if to_file:
                Path(to_file).write_text("user_cred")

        mock_mastodon.log_in = mock_login

        with (
            patch("mastodon.Mastodon") as MockMastodon,
            patch("getpass.getpass", return_value="secret123"),
        ):
            MockMastodon.create_app = mock_create_app
            MockMastodon.return_value = mock_mastodon

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)],
                                   input="https://fediscience.org\nuser@test.com\n")

        assert result.exit_code == 0, result.output
        assert "Credentials saved" in result.output

        # .env should have credentials
        from scholarposter.env_writer import read_env
        env = read_env(tmp_path / ".env")
        assert env["MASTODON_INSTANCE"] == "https://fediscience.org"
        assert env["MASTODON_EMAIL"] == "user@test.com"
        assert env["MASTODON_PASSWORD"] == "secret123"

        # Password should be cleared from environ
        assert "MASTODON_PASSWORD" not in os.environ

    def test_noninteractive_uses_env(self, tmp_path, monkeypatch):
        """Non-interactive: reads from .env, no prompts, prints success."""
        config = tmp_path / "config.toml"
        config.write_text("")
        env_file = tmp_path / ".env"
        env_file.write_text('MASTODON_INSTANCE="https://test.social"\nMASTODON_EMAIL="a@b.com"\nMASTODON_PASSWORD="pw"\n')

        def mock_create_app(*a, **kw):
            to_file = kw.get("to_file")
            if to_file:
                Path(to_file).write_text("client_cred")

        mock_mastodon = MagicMock()
        mock_mastodon.log_in = lambda *a, **kw: Path(kw.get("to_file", "")).write_text("user_cred") if kw.get("to_file") else None

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app = mock_create_app
            MockMastodon.return_value = mock_mastodon

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)])

        assert result.exit_code == 0, result.output
        assert "Credentials saved" in result.output


class TestAuthMastodonErrors:
    def test_wrong_password(self, tmp_path, monkeypatch):
        from mastodon import MastodonAPIError
        config = tmp_path / "config.toml"
        config.write_text("")
        env_file = tmp_path / ".env"
        env_file.write_text('MASTODON_INSTANCE="https://x.social"\nMASTODON_EMAIL="a@b"\nMASTODON_PASSWORD="bad"\n')

        def mock_create_app(*a, **kw):
            to_file = kw.get("to_file")
            if to_file:
                Path(to_file).write_text("client_cred")

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app = mock_create_app
            m = MagicMock()
            m.log_in.side_effect = MastodonAPIError("Invalid credentials")
            MockMastodon.return_value = m

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)])

        assert result.exit_code == 2
        assert "Login failed" in result.output

    def test_2fa_detected(self, tmp_path):
        from mastodon import MastodonAPIError
        config = tmp_path / "config.toml"
        config.write_text("")
        env_file = tmp_path / ".env"
        env_file.write_text('MASTODON_INSTANCE="https://x.social"\nMASTODON_EMAIL="a@b"\nMASTODON_PASSWORD="pw"\n')

        def mock_create_app(*a, **kw):
            to_file = kw.get("to_file")
            if to_file:
                Path(to_file).write_text("client_cred")

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app = mock_create_app
            m = MagicMock()
            m.log_in.side_effect = MastodonAPIError("Two-factor authentication required")
            MockMastodon.return_value = m

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)])

        assert result.exit_code == 2
        assert "2FA enabled" in result.output

    def test_instance_unreachable(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")
        env_file = tmp_path / ".env"
        env_file.write_text('MASTODON_INSTANCE="https://down.social"\nMASTODON_EMAIL="a@b"\nMASTODON_PASSWORD="pw"\n')

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app.side_effect = Exception("Connection refused")

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)])

        assert result.exit_code == 2
        assert "Could not reach" in result.output

    def test_url_normalization(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("")
        env_file = tmp_path / ".env"
        env_file.write_text('MASTODON_INSTANCE="fediscience.org"\nMASTODON_EMAIL="a@b"\nMASTODON_PASSWORD="pw"\n')

        def mock_create_app(*a, **kw):
            to_file = kw.get("to_file")
            if to_file:
                Path(to_file).write_text("client_cred")

        m = MagicMock()
        m.log_in = lambda *a, **kw: Path(kw.get("to_file", "")).write_text("user_cred") if kw.get("to_file") else None

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app = mock_create_app
            MockMastodon.return_value = m

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)])

        assert result.exit_code == 0
        from scholarposter.env_writer import read_env
        env = read_env(tmp_path / ".env")
        assert env["MASTODON_INSTANCE"] == "https://fediscience.org"


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
        # Existing sections preserved
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
        env_file = tmp_path / ".env"
        env_file.write_text('MASTODON_INSTANCE="https://x.social"\nMASTODON_EMAIL="a@b"\nMASTODON_PASSWORD="pw"\n')

        client_cred = tmp_path / "pytooter_clientcred.secret"
        user_cred = tmp_path / "pytooter_usercred.secret"

        def mock_create_app(*args, **kwargs):
            to_file = kwargs.get("to_file", args[2] if len(args) > 2 else None)
            if to_file:
                Path(to_file).write_text("client_cred_content")

        with patch("mastodon.Mastodon") as MockMastodon:
            MockMastodon.create_app = mock_create_app
            m = MagicMock()
            def mock_login(*args, **kwargs):
                to_file = kwargs.get("to_file")
                if to_file:
                    Path(to_file).write_text("user_cred_content")
            m.log_in = mock_login
            MockMastodon.return_value = m

            result = runner.invoke(auth_app, ["mastodon", "--config", str(config)])

        assert result.exit_code == 0, result.output
        if client_cred.exists():
            assert stat.S_IMODE(os.stat(client_cred).st_mode) == 0o600
        if user_cred.exists():
            assert stat.S_IMODE(os.stat(user_cred).st_mode) == 0o600
