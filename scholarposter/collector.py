"""Mastodon toot fetching and parsing for scholarposter."""
from __future__ import annotations

import mimetypes
import re
from datetime import datetime, timezone
from typing import Any, Optional

from bs4 import BeautifulSoup

from scholarposter.models import MediaAttachment, UnifiedPost

_URL_RE = re.compile(r'https?://[^\s<>"\']+')
_EMOJI_RE = re.compile(r':[A-Za-z0-9_]+:')


def strip_html(html: str) -> str:
    """Convert Mastodon HTML content to plain text, preserving structure."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    # Replace <br> with newline marker
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # Replace <p> with double-newline markers
    for p in soup.find_all("p"):
        p.insert_before("\n\n")
        p.unwrap()
    # Unwrap spans and anchors
    for tag in soup.find_all(["span", "a"]):
        tag.unwrap()
    text = soup.get_text()
    # Collapse runs of 3+ newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_urls(text: str) -> list[str]:
    """Extract unique HTTP/HTTPS URLs from plain text, preserving order."""
    return list(dict.fromkeys(_URL_RE.findall(text)))


def extract_hashtags(tags: list[dict[str, Any]]) -> list[str]:
    """Extract hashtag names from Mastodon tag dicts."""
    return [t["name"] for t in tags if "name" in t]


def _clean_display_name(name: str) -> str:
    """Strip :emoji: shortcodes from a display name."""
    return _EMOJI_RE.sub("", name)


def _mime_from_attachment(att: dict[str, Any]) -> str:
    """Guess MIME type from Mastodon media attachment type field.

    First attempts to detect MIME from the URL extension (remote_url or url).
    Falls back to a type-based mapping when the URL has no recognisable extension
    or the guessed MIME does not match the declared attachment type category.
    """
    atype = att.get("type", "unknown")
    url = att.get("remote_url") or att.get("url", "")
    if url:
        guessed, _ = mimetypes.guess_type(url)
        if guessed and guessed.startswith(f"{atype}/"):
            return guessed
    mapping = {"image": "image/jpeg", "video": "video/mp4", "gifv": "video/mp4", "audio": "audio/mpeg"}
    return mapping.get(atype, "application/octet-stream")


class MastodonCollector:
    def __init__(self, mastodon_client: Any):
        self._client = mastodon_client

    def fetch_oldest_unprocessed(
        self, user_id: str, since_id: Optional[int]
    ) -> Optional[UnifiedPost]:
        """Fetch the oldest unprocessed toot for the given user.

        Returns None if the timeline is empty or all toots are already processed.

        Note: Uses limit=50. If backlog exceeds 50, only the 50 newest are fetched.
        Run frequently to prevent backlog buildup.
        """
        kwargs: dict[str, Any] = {"exclude_replies": True, "limit": 50}
        if since_id is not None:
            kwargs["min_id"] = since_id

        toots = self._client.account_statuses(user_id, **kwargs)
        if not toots:
            return None

        # Return the oldest (last item in the list — Mastodon returns newest first)
        oldest = toots[-1]
        return self._toot_to_unified_post(oldest)

    def _toot_to_unified_post(self, toot: dict[str, Any]) -> UnifiedPost:
        """Convert a raw Mastodon toot dict to a UnifiedPost."""
        is_reblog = bool(toot.get("reblog"))
        original_author: Optional[str] = None
        original_url: Optional[str] = None
        source = toot

        if is_reblog:
            inner = toot["reblog"]
            display_name = inner.get("account", {}).get("display_name", "")
            acct = inner.get("account", {}).get("acct", "")
            original_author = _clean_display_name(display_name).strip() or acct
            original_url = inner.get("url")
            source = inner

        raw_content = source.get("content", "")
        plain_text = strip_html(raw_content)

        if is_reblog and original_author:
            plain_text = f"via {original_author}:\n{plain_text}"

        tags = source.get("tags", [])
        hashtags = extract_hashtags(tags)
        urls = extract_urls(plain_text)

        # Parse media attachments
        media: list[MediaAttachment] = []
        for att in source.get("media_attachments", []):
            url = att.get("remote_url") or att.get("url", "")
            mime = _mime_from_attachment(att)
            meta = att.get("meta", {}).get("original", {})
            media.append(
                MediaAttachment(
                    url=url,
                    mime_type=mime,
                    alt_text=att.get("description"),
                    width=meta.get("width"),
                    height=meta.get("height"),
                )
            )

        created_raw = toot.get("created_at")
        if isinstance(created_raw, datetime):
            created_at = created_raw
        elif isinstance(created_raw, str):
            created_at = datetime.fromisoformat(
                created_raw.replace("Z", "+00:00")
            )
        else:
            created_at = datetime.now(timezone.utc)

        return UnifiedPost(
            source_id=str(toot["id"]),
            text=plain_text,
            source_url=toot.get("url", ""),
            created_at=created_at,
            is_reblog=is_reblog,
            original_author=original_author,
            original_url=original_url,
            media=media,
            hashtags=hashtags,
            urls=urls,
            is_sensitive=bool(source.get("sensitive", False)),
            has_poll=source.get("poll") is not None,
        )
