"""Tests for scholarposter.auth.oauth"""
import pytest
import respx
import httpx

from scholarposter.auth.oauth import (
    exchange_code,
    fetch_member_urn,
    OAuthHardError,
)


class TestExchangeCode:
    @respx.mock
    def test_success_returns_tokens(self):
        respx.post("https://www.linkedin.com/oauth/v2/accessToken").mock(
            return_value=httpx.Response(200, json={
                "access_token": "new-access-token",
                "expires_in": 5184000,
            })
        )
        result = exchange_code("code123", "http://localhost:8080/callback", "cid", "csecret")
        assert result["access_token"] == "new-access-token"
        assert result["expires_in"] == 5184000

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
