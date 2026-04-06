"""OAuth callback handling: desktop HTTP server and headless URL paste."""
from __future__ import annotations

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse, parse_qs


class OAuthError(Exception):
    """OAuth flow failure with user-facing message."""
    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Store result on the server instance
        if "error" in params:
            desc = params.get("error_description", [params["error"][0]])[0]
            self.server._oauth_error = f"Authorization denied: {desc}"
        elif "code" in params and "state" in params:
            self.server._oauth_code = params["code"][0]
            self.server._oauth_state = params["state"][0]
        else:
            self.server._oauth_error = "Invalid callback: missing code or state parameter"

        # Send response to browser
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>Authorization complete</h1><p>You can close this window.</p></body></html>")

        # Signal the main thread
        self.server._event.set()

    def log_message(self, format, *args):
        pass  # Suppress HTTP server logs


def is_headless() -> bool:
    """Detect headless environment: no DISPLAY, WAYLAND_DISPLAY, or BROWSER."""
    return not (
        os.environ.get("DISPLAY")
        or os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("BROWSER")
    )


def wait_for_callback_desktop(port: int, expected_state: Optional[str] = None, timeout: float = 120.0) -> str:
    """Start HTTP server on 127.0.0.1:{port}, wait for OAuth callback, return authorization code.

    Raises OAuthError on timeout, denied, invalid state, or port conflict.
    """
    try:
        server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    except OSError:
        raise OAuthError(f"Port {port} is in use. Retry with --port <N>.")

    server._event = threading.Event()
    server._oauth_code = None
    server._oauth_state = None
    server._oauth_error = None

    print(f"Waiting for authorization callback on http://localhost:{port}/callback ({int(timeout)}s timeout)...")

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    server._event.wait(timeout=timeout)
    server.server_close()

    if server._oauth_error:
        raise OAuthError(server._oauth_error)

    if not server._oauth_code:
        raise OAuthError("Authorization timed out. Run `scholarposter auth linkedin` to try again.")

    if expected_state is not None and server._oauth_state != expected_state:
        raise OAuthError("Invalid state parameter in callback. Possible CSRF attack. Try again.")

    return server._oauth_code


def wait_for_callback_headless(expected_state: Optional[str] = None) -> str:
    """Prompt user to paste callback URL, extract code, validate state.

    Raises OAuthError on malformed input or state mismatch.
    """
    print("\nOpen this URL in your browser, authorize, then paste the full callback URL here:")
    pasted = input("> ").strip()

    try:
        parsed = urlparse(pasted)
        params = parse_qs(parsed.query)
    except Exception:
        raise OAuthError(
            "Could not parse URL. Expected format: http://localhost:8080/callback?code=...&state=..."
        )

    if "error" in params:
        desc = params.get("error_description", [params["error"][0]])[0]
        raise OAuthError(f"Authorization denied: {desc}")

    code_list = params.get("code")
    state_list = params.get("state")

    if not code_list or not state_list:
        raise OAuthError(
            "Could not parse authorization code from URL. "
            "Expected format: http://localhost:8080/callback?code=...&state=..."
        )

    if expected_state is not None and state_list[0] != expected_state:
        raise OAuthError("Invalid state parameter. Possible CSRF attack. Try again.")

    return code_list[0]
