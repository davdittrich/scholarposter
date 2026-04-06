"""LinkedIn OAuth 2.0 token exchange, URN lookup, and token refresh."""
from __future__ import annotations

import httpx
from loguru import logger


_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


class OAuthHardError(Exception):
    """Non-recoverable OAuth error (401, invalid_grant). Triggers auth_expired."""
    pass


class OAuthTransientError(Exception):
    """Transient OAuth error (5xx, timeout). Proceed with old token."""
    pass


def exchange_code(
    code: str, redirect_uri: str, client_id: str, client_secret: str
) -> dict:
    """Exchange authorization code for tokens.

    Returns dict with: access_token, expires_in, refresh_token, refresh_token_expires_in.
    Raises OAuthHardError if refresh_token absent or HTTP error.
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

    data = resp.json()

    if "refresh_token" not in data:
        raise OAuthHardError(
            "LinkedIn did not return a refresh token. "
            "Ensure your app has 'Sign In with LinkedIn using OpenID Connect' enabled."
        )
    return data


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


def refresh_access_token(
    refresh_token: str, client_id: str, client_secret: str
) -> dict:
    """Refresh the access token.

    Returns dict with access_token, expires_in, and optionally
    refresh_token + refresh_token_expires_in (if rotated).

    Raises OAuthHardError on 401/invalid_grant.
    Raises OAuthTransientError on 5xx/timeout.
    """
    try:
        with httpx.Client(verify=True, timeout=15) as client:
            resp = client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise OAuthTransientError(f"Network error during token refresh: {e}")

    logger.debug(f"Token refresh response: HTTP {resp.status_code}")

    if resp.status_code == 401:
        raise OAuthHardError("Token refresh failed: HTTP 401 (token revoked or expired)")

    if resp.status_code >= 500:
        raise OAuthTransientError(f"Token refresh failed: HTTP {resp.status_code}")

    if resp.status_code != 200:
        # Check for invalid_grant in response body
        try:
            body = resp.json()
            if body.get("error") == "invalid_grant":
                raise OAuthHardError("Token refresh failed: invalid_grant")
        except (ValueError, httpx.DecodingError):
            pass
        raise OAuthHardError(f"Token refresh failed: HTTP {resp.status_code}")

    return resp.json()
