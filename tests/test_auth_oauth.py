"""Tests for scholarposter.auth.oauth"""
import pytest
import respx
import httpx

from scholarposter.auth.oauth import (
    exchange_code,
    fetch_member_urn,
    refresh_access_token,
    OAuthHardError,
    OAuthTransientError,
)


class TestExchangeCode:
    @respx.mock
    def test_success_returns_tokens(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(200, json={
                "access_token": "new-access-token",
                "expires_in": 5184000,
                "refresh_token": "new-refresh-token",
                "refresh_token_expires_in": 31536000,
            })
        )
        result = exchange_code("code123", "http://localhost:8080/callback", "cid", "csecret")
        assert result["access_token"] == "new-access-token"
        assert result["refresh_token"] == "new-refresh-token"

    @respx.mock
    def test_missing_refresh_token_raises(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(200, json={
                "access_token": "token",
                "expires_in": 5184000,
            })
        )
        with pytest.raises(OAuthHardError, match="refresh token"):
            exchange_code("code", "http://localhost:8080/callback", "cid", "cs")

    @respx.mock
    def test_http_error_raises(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(400, json={"error": "invalid_request"})
        )
        with pytest.raises(OAuthHardError, match="400"):
            exchange_code("bad", "http://localhost:8080/callback", "cid", "cs")


class TestFetchMemberUrn:
    @respx.mock
    def test_success_returns_urn(self):
        respx.get("https://api.linkedin.com/v2/userinfo").mock(
            return_value=httpx.Response(200, json={"sub": "abc123", "name": "Test"})
        )
        result = fetch_member_urn("access-token")
        assert result == "urn:li:person:abc123"

    @respx.mock
    def test_http_error_raises(self):
        respx.get("https://api.linkedin.com/v2/userinfo").mock(
            return_value=httpx.Response(401)
        )
        with pytest.raises(OAuthHardError, match="401"):
            fetch_member_urn("bad-token")


class TestRefreshAccessToken:
    @respx.mock
    def test_success_returns_new_access_token(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(200, json={
                "access_token": "refreshed-token",
                "expires_in": 5184000,
            })
        )
        result = refresh_access_token("refresh-tok", "cid", "cs")
        assert result["access_token"] == "refreshed-token"

    @respx.mock
    def test_rotation_returns_new_refresh_token(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(200, json={
                "access_token": "new-at",
                "expires_in": 5184000,
                "refresh_token": "rotated-rt",
                "refresh_token_expires_in": 31536000,
            })
        )
        result = refresh_access_token("old-rt", "cid", "cs")
        assert result["refresh_token"] == "rotated-rt"

    @respx.mock
    def test_401_raises_hard_error(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(401)
        )
        with pytest.raises(OAuthHardError, match="401"):
            refresh_access_token("expired-rt", "cid", "cs")

    @respx.mock
    def test_503_raises_transient_error(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(503)
        )
        with pytest.raises(OAuthTransientError, match="503"):
            refresh_access_token("rt", "cid", "cs")

    def test_timeout_raises_transient_error(self):
        with respx.mock:
            respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
                side_effect=httpx.TimeoutException("timed out")
            )
            with pytest.raises(OAuthTransientError, match="Network error"):
                refresh_access_token("rt", "cid", "cs")
