"""Tests for scholarposter.auth.callback"""
import threading
import time

import httpx
import pytest

from scholarposter.auth.callback import (
    is_headless,
    wait_for_callback_desktop,
    wait_for_callback_headless,
    OAuthError,
)


class TestIsHeadless:
    def test_display_set_is_not_headless(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("BROWSER", raising=False)
        assert not is_headless()

    def test_wayland_display_set_is_not_headless(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.delenv("BROWSER", raising=False)
        assert not is_headless()

    def test_browser_set_is_not_headless(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("BROWSER", "firefox")
        assert not is_headless()

    def test_nothing_set_is_headless(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("BROWSER", raising=False)
        assert is_headless()


class TestDesktopCallback:
    def _send_callback(self, port, code, state, delay=0.1):
        """Send a GET request to the callback server after a short delay."""
        def _do():
            time.sleep(delay)
            try:
                httpx.get(f"http://127.0.0.1:{port}/callback?code={code}&state={state}", timeout=5)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def _send_error(self, port, error="access_denied", delay=0.1):
        def _do():
            time.sleep(delay)
            try:
                httpx.get(f"http://127.0.0.1:{port}/callback?error={error}&error_description=User+denied", timeout=5)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def test_extracts_code_from_valid_callback(self):
        state = "test-state-123"
        port = 18901  # Use high port to avoid conflicts
        self._send_callback(port, "AUTH_CODE_XYZ", state)
        result = wait_for_callback_desktop(port=port, expected_state=state, timeout=5.0)
        assert result == "AUTH_CODE_XYZ"

    def test_rejects_invalid_state(self):
        port = 18902
        self._send_callback(port, "code123", "wrong-state")
        with pytest.raises(OAuthError, match="Invalid state"):
            wait_for_callback_desktop(port=port, expected_state="correct-state", timeout=5.0)

    def test_handles_error_access_denied(self):
        port = 18903
        self._send_error(port)
        with pytest.raises(OAuthError, match="denied"):
            wait_for_callback_desktop(port=port, expected_state="any", timeout=5.0)

    def test_timeout_raises(self):
        port = 18904
        with pytest.raises(OAuthError, match="timed out"):
            wait_for_callback_desktop(port=port, expected_state="any", timeout=0.1)

    def test_port_conflict_raises(self):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 18905))
        sock.listen(1)
        try:
            with pytest.raises(OAuthError, match="in use"):
                wait_for_callback_desktop(port=18905, expected_state="any")
        finally:
            sock.close()


class TestHeadlessCallback:
    def test_extracts_code_from_pasted_url(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "http://localhost:8080/callback?code=ABC&state=xyz")
        result = wait_for_callback_headless(expected_state="xyz")
        assert result == "ABC"

    def test_rejects_invalid_state(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "http://localhost:8080/callback?code=ABC&state=wrong")
        with pytest.raises(OAuthError, match="Invalid state"):
            wait_for_callback_headless(expected_state="correct")

    def test_rejects_malformed_url(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "not-a-url")
        with pytest.raises(OAuthError, match="Could not parse"):
            wait_for_callback_headless(expected_state="any")
