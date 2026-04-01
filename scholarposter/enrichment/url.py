"""URL unshortening and content-type detection."""
from __future__ import annotations

import mimetypes
from typing import Optional
from urllib.parse import urlparse

import httpx


def unshorten_url(url: str, timeout: int = 10, max_redirects: int = 5) -> str:
    """Follow redirects via HEAD and return the final URL. Returns original on error.

    Uses HEAD to avoid downloading the full response body. Falls back to GET
    if the server returns 405 Method Not Allowed.
    """
    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=max_redirects,
            timeout=timeout,
        ) as client:
            resp = client.head(url)
            if resp.status_code == 405:
                resp = client.get(url)
            return str(resp.url)
    except (httpx.TimeoutException, httpx.HTTPError, httpx.TransportError):
        return url


def detect_content_type(url: str, timeout: int = 5) -> Optional[str]:
    """Detect MIME type via HEAD request, falling back to URL extension."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.head(url)
            ct = resp.headers.get("content-type", "")
            if ct:
                return ct.split(";")[0].strip()
    except (httpx.TimeoutException, httpx.HTTPError, httpx.TransportError):
        pass

    # Fallback: guess from URL extension
    path = urlparse(url).path
    mime, _ = mimetypes.guess_type(path)
    return mime
