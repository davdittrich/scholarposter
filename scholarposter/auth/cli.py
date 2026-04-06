"""Authentication CLI commands: scholarposter auth linkedin | mastodon."""
from __future__ import annotations

import os
import secrets
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from loguru import logger

from scholarposter.auth.callback import (
    OAuthError,
    is_headless,
    wait_for_callback_desktop,
    wait_for_callback_headless,
)
from scholarposter.auth.oauth import exchange_code, fetch_member_urn
from scholarposter.env_writer import read_env, write_env
from scholarposter.models import PlatformState
from scholarposter.state import StateManager

auth_app = typer.Typer(help="Authentication management.")

SETUP_INSTRUCTIONS = """LinkedIn OAuth setup required. Follow these steps:
1. Create an app at https://www.linkedin.com/developers/apps
   (or reuse an existing app)
2. On the Products tab, enable:
   - "Share on LinkedIn" (for posting)
   - "Sign In with LinkedIn using OpenID Connect" (for refresh tokens)
   Note: "Community Management API" also works for posting if available.
3. On the Auth tab, add this redirect URI: http://localhost:8080/callback
4. Copy your Client ID and Client Secret, then add to .env:
   LINKEDIN_CLIENT_ID=your-client-id
   LINKEDIN_CLIENT_SECRET=your-client-secret
5. Re-run: scholarposter auth linkedin"""


def _expiry_iso(expires_in_seconds: int) -> str:
    """Convert expires_in seconds to an ISO 8601 timestamp."""
    return (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).isoformat()


def _expiry_date(expires_in_seconds: int) -> str:
    """Convert expires_in seconds to a human-readable date."""
    return (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).strftime("%Y-%m-%d")


@auth_app.command()
def linkedin(
    config: Path = typer.Option("config.toml", help="Path to config file"),
    port: int = typer.Option(8080, help="Local callback server port"),
) -> None:
    """Authorize scholarposter to post to LinkedIn."""
    env_path = config.parent.resolve() / ".env"
    env = read_env(env_path)

    # FR-53: prerequisite check
    client_id = env.get("LINKEDIN_CLIENT_ID")
    client_secret = env.get("LINKEDIN_CLIENT_SECRET")
    if not client_id or not client_secret:
        typer.echo(SETUP_INSTRUCTIONS)
        raise typer.Exit(code=2)

    # FR-54: generate state, build URL
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/callback"
    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code&client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        "&scope=openid%20profile%20w_member_social"
        f"&state={state}"
    )

    try:
        if is_headless():
            typer.echo(f"\nOpen this URL in your browser:\n\n  {auth_url}\n")
            code = wait_for_callback_headless(expected_state=state)
        else:
            typer.echo(f"\nAuthorization URL (also opening in browser):\n\n  {auth_url}\n")
            webbrowser.open(auth_url)
            code = wait_for_callback_desktop(port=port, expected_state=state)

        # FR-56: exchange code
        tokens = exchange_code(code, redirect_uri, client_id, client_secret)
        typer.echo(f"Authorization successful. Token expires {_expiry_date(tokens['expires_in'])}.")

        # FR-57: fetch URN
        urn = fetch_member_urn(tokens["access_token"])

        # FR-58: write to .env
        write_env(env_path, {
            "LINKEDIN_ACCESS_TOKEN": tokens["access_token"],
            "LINKEDIN_REFRESH_TOKEN": tokens["refresh_token"],
            "LINKEDIN_OWNER_URN": urn,
            "LINKEDIN_TOKEN_EXPIRES_AT": _expiry_iso(tokens["expires_in"]),
            "LINKEDIN_REFRESH_EXPIRES_AT": _expiry_iso(tokens["refresh_token_expires_in"]),
        })

        # FR-61: reset auth state on successful re-auth
        state_mgr = StateManager(state_dir=config.parent.resolve())
        with state_mgr.lock():
            state_mgr.update_platform_state("linkedin", PlatformState(
                auth_status="normal",
                refresh_failure_count=0,
                refresh_warning_last_sent=None,
            ))

        typer.echo("Credentials saved to .env. LinkedIn is now authorized.")

    except OAuthError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code)


@auth_app.command(name="mastodon")
def mastodon_auth(
    config: Path = typer.Option("config.toml", help="Path to config file"),
    port: int = typer.Option(8080, help="Local callback server port (desktop mode)"),
) -> None:
    """Register app and log in to a Mastodon instance via OAuth."""
    from mastodon import Mastodon

    env_path = config.parent.resolve() / ".env"
    env = read_env(env_path)

    # FR-66: use instance from .env if available
    instance = env.get("MASTODON_INSTANCE")
    interactive = not instance

    if interactive:
        instance = typer.prompt("Mastodon instance URL", default="")

    # FR-65: URL normalization + reject http://
    if instance and not instance.startswith(("http://", "https://")):
        instance = f"https://{instance}"
    if instance:
        instance = instance.rstrip("/")
    if not instance:
        typer.echo("Instance URL is required.", err=True)
        raise typer.Exit(code=2)
    if instance.startswith("http://"):
        typer.echo("Mastodon instances require HTTPS. Use https:// instead.", err=True)
        raise typer.Exit(code=2)

    config_dir = config.parent.resolve()
    client_cred = config_dir / "pytooter_clientcred.secret"
    user_cred = config_dir / "pytooter_usercred.secret"

    # FR-70: re-run idempotency
    if interactive and user_cred.exists():
        if not typer.confirm("Existing credentials will be overwritten. Continue?", default=False):
            raise typer.Exit(code=0)

    # Determine redirect URI based on environment
    headless = is_headless()
    if headless:
        redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    else:
        redirect_uri = f"http://localhost:{port}/callback"

    # FR-67: register app
    typer.echo(f"Registering app with {instance}...")
    try:
        Mastodon.create_app(
            "scholarposter",
            api_base_url=instance,
            redirect_uris=redirect_uri,
            scopes=["read"],
            to_file=str(client_cred),
        )
        os.chmod(client_cred, 0o600)
    except Exception as e:
        typer.echo(f"Could not reach {instance}. Check the URL and try again.\n  Detail: {e}", err=True)
        raise typer.Exit(code=2)

    # FR-68: OAuth code flow
    typer.echo("Authorizing...")
    mastodon = Mastodon(client_id=str(client_cred), api_base_url=instance)
    auth_url = mastodon.auth_request_url(redirect_uris=redirect_uri, scopes=["read"])

    try:
        if headless:
            typer.echo(f"\nOpen this URL in your browser:\n\n  {auth_url}\n")
            auth_code = typer.prompt("Paste the code shown on the Mastodon authorization page")
        else:
            typer.echo(f"\nAuthorization URL (also opening in browser):\n\n  {auth_url}\n")
            typer.echo("Click 'Authorize' in your browser, then return here.\n")
            webbrowser.open(auth_url)
            auth_code = wait_for_callback_desktop(port=port, expected_state=None)
    except OAuthError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code)

    # Exchange code for token
    try:
        mastodon.log_in(code=auth_code, redirect_uri=redirect_uri, scopes=["read"], to_file=str(user_cred))
        os.chmod(user_cred, 0o600)
    except Exception as e:
        typer.echo(f"Token exchange failed: {e}", err=True)
        raise typer.Exit(code=2)

    # FR-69: store instance in .env (no password needed)
    write_env(env_path, {"MASTODON_INSTANCE": instance})

    # FR-70: update config.toml
    _update_config_toml(config.parent.resolve() / config.name, instance, str(user_cred))

    typer.echo(f"Mastodon authorized. Credentials saved to {config_dir}.")


def _update_config_toml(config_path: Path, instance: str, credentials_file: str) -> None:
    """Update [mastodon] section in config.toml via tomli_w. Preserves other sections."""
    import tomllib
    import tomli_w

    resolved = config_path if config_path.is_absolute() else config_path.resolve()
    if resolved.exists():
        with open(resolved, "rb") as f:
            data = tomllib.load(f)
    else:
        data = {}

    data.setdefault("mastodon", {})
    data["mastodon"]["instance"] = instance
    data["mastodon"]["credentials_file"] = credentials_file

    # Atomic write
    tmp = resolved.parent / (resolved.name + ".tmp")
    fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(data, f)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.rename(str(tmp), str(resolved))
