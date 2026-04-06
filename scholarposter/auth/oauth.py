"""LinkedIn OAuth 2.0 token exchange and URN lookup."""
from __future__ import annotations

import httpx
from loguru import logger


_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


class OAuthHardError(Exception):
    """Non-recoverable OAuth error (401, invalid_grant). Triggers auth_expired."""
    pass


def exchange_code(
    code: str, redirect_uri: str, client_id: str, client_secret: str
) -> dict:
    """Exchange authorization code for tokens.

    Returns dict with at minimum: access_token, expires_in.
    Raises OAuthHardError on HTTP error.
    """
    with httpx.Client(verify=True, timeout=15) as client:
        resp = client.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        logger.debug(f"Token exchange response: HTTP {resp.status_code}")
        raise OAuthHardError(f"Token exchange failed: HTTP {resp.status_code}")

    return resp.json()


def fetch_member_urn(access_token: str) -> str:
    """Fetch user's Member URN from LinkedIn userinfo endpoint.

    Returns urn:li:person:{sub}.
    """
    with httpx.Client(verify=True, timeout=15) as client:
        resp = client.get(
            _USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise OAuthHardError(f"Userinfo request failed: HTTP {resp.status_code}")

    data = resp.json()
    sub = data.get("sub")
    if not sub:
        raise OAuthHardError("Userinfo response missing 'sub' field")
    return f"urn:li:person:{sub}"
