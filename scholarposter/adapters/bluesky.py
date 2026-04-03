"""Bluesky adapter for scholarposter."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import grapheme as _grapheme_mod

from atproto import models
from loguru import logger

from scholarposter.adapters.base import BaseAdapter
from scholarposter.config import HashtagRule, MediaConfig
from scholarposter.enrichment.media import download_media, resize_image
from scholarposter.filters import apply_hashtag_rules
from scholarposter.models import PostResult, PostStatus, UnifiedPost

# Byte-indexed parsing (byte positions matter for AT Protocol facets)
_MENTION_RE = re.compile(
    rb"[$|\W](@([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
)
_URL_RE = re.compile(
    rb"(https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*[-a-zA-Z0-9@%_\+~#//=])?)"
)
_TAG_RE = re.compile(rb"#(\S+)")

MAX_GRAPHEMES = 300


def parse_mentions(text: str) -> list[dict[str, Any]]:
    """Parse @mentions from text, returning byte-accurate spans."""
    spans = []
    text_bytes = text.encode("utf-8")
    for m in _MENTION_RE.finditer(text_bytes):
        spans.append({
            "start": m.start(1),
            "end": m.end(1),
            "handle": m.group(1)[1:].decode("utf-8"),
        })
    return spans


def parse_urls(text: str) -> list[dict[str, Any]]:
    """Parse URLs from text, returning byte-accurate spans."""
    spans = []
    text_bytes = text.encode("utf-8")
    for m in _URL_RE.finditer(text_bytes):
        spans.append({
            "start": m.start(1),
            "end": m.end(1),
            "url": m.group(1).decode("utf-8"),
        })
    return spans


def parse_tags(text: str) -> list[dict[str, Any]]:
    """Parse #hashtags from text, returning byte-accurate spans."""
    spans = []
    text_bytes = text.encode("utf-8")
    for t in _TAG_RE.finditer(text_bytes):
        spans.append({
            "start": t.start(1) - 1,
            "end": t.end(1),
            "tag": t.group(1).decode("utf-8"),
        })
    return spans


def _grapheme_len(text: str) -> int:
    """Count grapheme clusters (correct for AT Protocol)."""
    return _grapheme_mod.length(text)


def chunk_text(text: str, max_graphemes: int = MAX_GRAPHEMES) -> list[str]:
    """Split text into chunks that fit within max_graphemes.

    Adds 'n/total' suffix to each chunk when splitting into a thread.
    Does not break within words.
    """
    if _grapheme_len(text) <= max_graphemes:
        return [text]

    # Split on word boundaries
    words = text.split(" ")
    chunks: list[str] = []
    current: list[str] = []

    for word in words:
        test = " ".join(current + [word]) if current else word
        # Reserve space for thread suffix like " 1/5"
        if _grapheme_len(test) > max_graphemes - 6:
            if current:
                chunks.append(" ".join(current))
                current = [word]
            else:
                # Single word exceeds limit — force add
                chunks.append(word)
        else:
            current.append(word)

    if current:
        chunks.append(" ".join(current))

    # Add thread suffixes
    total = len(chunks)
    if total > 1:
        suffixed = []
        for i, chunk in enumerate(chunks):
            suffix = f" {i + 1}/{total}"
            # Truncate chunk if needed to fit suffix
            room = max_graphemes - _grapheme_len(suffix)
            if _grapheme_len(chunk) > room:
                truncated = _grapheme_mod.slice(chunk, 0, room)
                # Word-boundary truncation
                last_space = truncated.rfind(" ")
                if last_space > 0:
                    truncated = truncated[:last_space]
                chunk = truncated
            suffixed.append(chunk + suffix)
        return suffixed

    # Handle single chunk exceeding limit (e.g., very long URL with no spaces)
    if total == 1 and _grapheme_len(chunks[0]) > max_graphemes:
        chunks[0] = _grapheme_mod.slice(chunks[0], 0, max_graphemes)

    return chunks


def _build_facets(text: str, client: Any) -> list[dict[str, Any]]:
    """Build AT Protocol facets for a text string."""
    facets: list[dict[str, Any]] = []

    mentions = parse_mentions(text)
    for m in mentions[:10]:  # FR-29: cap at 10 mentions
        try:
            resp = client.com.atproto.identity.resolve_handle(params={"handle": m["handle"]})
            did = resp.did
            facets.append({
                "index": {"byteStart": m["start"], "byteEnd": m["end"]},
                "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}],
            })
        except Exception:
            pass  # Unresolvable mentions render as plain text
        finally:
            time.sleep(0.2)  # FR-29: always rate-limit, even on exception

    for u in parse_urls(text):
        facets.append({
            "index": {"byteStart": u["start"], "byteEnd": u["end"]},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": u["url"]}],
        })

    for t in parse_tags(text):
        facets.append({
            "index": {"byteStart": t["start"], "byteEnd": t["end"]},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": t["tag"]}],
        })

    return facets


class BlueskyAdapter(BaseAdapter):
    def __init__(self, client: Any, hashtag_rules: Optional[list[HashtagRule]] = None,
                 media_config: Optional[MediaConfig] = None):
        self._client = client
        self._hashtag_rules: list[HashtagRule] = hashtag_rules or []
        self._media_cfg: MediaConfig = media_config or MediaConfig()

    @property
    def platform_name(self) -> str:
        return "bluesky"

    def post(self, unified_post: UnifiedPost, dry_run: bool = False) -> PostResult:
        """Post a UnifiedPost to Bluesky, threading if needed."""
        text = apply_hashtag_rules(unified_post.text, unified_post.hashtags, self._hashtag_rules)

        # Append first link summary if it fits within the grapheme limit
        if unified_post.links and unified_post.links[0].summary:
            combined = text + "\n\n" + unified_post.links[0].summary
            if _grapheme_len(combined) <= MAX_GRAPHEMES:
                text = combined

        chunks = chunk_text(text)

        if dry_run:
            return PostResult(platform=self.platform_name, status=PostStatus.POSTED)

        # Upload images (only for first chunk)
        embed = self._build_embed(unified_post)

        root_ref: Optional[Any] = None
        parent_ref: Optional[Any] = None

        for i, chunk in enumerate(chunks):
            facets = _build_facets(chunk, self._client)
            reply = None
            if i > 0 and root_ref and parent_ref:
                reply = models.AppBskyFeedPost.ReplyRef(
                    root=root_ref,
                    parent=parent_ref,
                )

            chunk_embed = embed if i == 0 else None

            try:
                record = models.AppBskyFeedPost.Record(
                    created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    text=chunk,
                    embed=chunk_embed,
                    facets=facets or None,
                    reply=reply,
                )
                response = self._client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=self._client.me.did,
                        collection="app.bsky.feed.post",
                        record=record,
                    )
                )
            except Exception as e:
                if i > 0:
                    # FR-28: already-posted chunks are NOT deleted (rollback deferred).
                    # The toot is marked failed in state; orphaned Bluesky post(s) require manual deletion.
                    logger.warning(
                        "Bluesky thread partially posted (%d/%d chunks) before failure — "
                        "orphaned post(s) remain on Bluesky and require manual deletion (toot %s)",
                        i, len(chunks), unified_post.source_id,
                    )
                return PostResult(
                    platform=self.platform_name,
                    status=PostStatus.FAILED,
                    error=str(e),
                )

            if i == 0:
                ref = models.create_strong_ref(response)
                root_ref = ref
                parent_ref = ref
            else:
                parent_ref = models.create_strong_ref(response)

        post_url = None
        if root_ref:
            did = self._client.me.did
            rkey = root_ref.uri.split("/")[-1] if root_ref.uri else ""
            post_url = f"https://bsky.app/profile/{did}/post/{rkey}"

        return PostResult(
            platform=self.platform_name,
            status=PostStatus.POSTED,
            post_url=post_url,
        )

    def _build_embed(self, post: UnifiedPost) -> Optional[Any]:
        """Build an image embed or link card embed for the post."""
        if not self._media_cfg.enabled:
            return None
        if post.media:
            images = []
            for att in post.media:
                try:
                    img_bytes = download_media(att.url)
                    if not img_bytes:
                        continue
                    img_bytes = resize_image(img_bytes, max_size_kb=self._media_cfg.max_image_size_kb, max_dims=(2048, 2048))
                    upload = self._client.com.atproto.repo.upload_blob(img_bytes)
                    images.append(
                        models.AppBskyEmbedImages.Image(
                            alt=att.alt_text or "",
                            image=upload.blob,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Bluesky image embed failed for {att.url}: {e}")
            if images:
                return models.AppBskyEmbedImages.Main(images=images)

        # Link card from first enriched link
        if post.links:
            link = post.links[0]
            url = link.resolved_url or link.original_url
            card = models.AppBskyEmbedExternal.External(
                uri=url,
                title=link.title or "",
                description=link.description or "",
            )
            if link.thumbnail_bytes:
                try:
                    # FR-10: resize thumbnail to 400×400 JPEG before upload
                    thumb_bytes = resize_image(link.thumbnail_bytes, max_size_kb=976, max_dims=(400, 400))
                    thumb_upload = self._client.com.atproto.repo.upload_blob(thumb_bytes)
                    card = models.AppBskyEmbedExternal.External(
                        uri=url,
                        title=link.title or "",
                        description=link.description or "",
                        thumb=thumb_upload.blob,
                    )
                except Exception:
                    pass
            return models.AppBskyEmbedExternal.Main(external=card)

        return None
