"""LinkedIn OAuth CLI command: scholarposter auth linkedin."""
from __future__ import annotations

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
2. Request access to "Community Management API" and "Sign In with LinkedIn using OpenID Connect"
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
