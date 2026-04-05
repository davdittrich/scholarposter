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

    NOTE: No private-IP filtering. Toot URLs from untrusted sources may cause
    requests to RFC 1918 addresses. Acceptable for single-user desktop deployment.
    If deployed to cloud, add httpx transport hook to reject private IPs.
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


# MIME types that classify a link as LinkType.FILE (FR-15a)
_FILE_MIME_ALLOWLIST = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# URL extensions that classify a link as LinkType.FILE
_FILE_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def classify_link_type(content_type: Optional[str], resolved_url: str) -> str:
    """Classify a URL as 'file' or 'webpage' per FR-15a.

    Primary: Content-Type against allowlist.
    Fallback: URL extension on resolved URL.
    Default: 'webpage'.
    """
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _FILE_MIME_ALLOWLIST or ct.startswith(
            "application/vnd.openxmlformats-officedocument."
        ):
            return "file"
    # Fallback: URL extension
    path = urlparse(resolved_url).path.lower()
    for ext in _FILE_EXTENSIONS:
        if path.endswith(ext):
            return "file"
    return "webpage"
