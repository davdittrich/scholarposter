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
_BRAND_SYMBOL = "⚗️"   # U+2697 + U+FE0F — 6 bytes UTF-8, 1 grapheme
_BRAND_URL = "https://github.com/davdittrich/scholarposter"
_SOURCE_SYMBOL = "🦣"


def _select_best_link(links, used_urls=None, chunk_text=None, promoted=None):
    """Select the most-enriched link for a chunk. FR-26a."""
    if not links:
        return promoted

    if used_urls is None:
        used_urls = set()

    candidates = []
    for link in links:
        url = link.resolved_url or link.original_url
        if url in used_urls:
            continue

        # If chunk_text provided, prioritize links within it
        if chunk_text is not None:
            if url in chunk_text or link.original_url in chunk_text:
                pos = chunk_text.find(link.original_url)
                if pos == -1:
                    pos = chunk_text.find(url)
                candidates.append((link, pos))

    if candidates:
        candidates.sort(key=lambda x: (-x[0].enrichment_rank, x[1]))
        return candidates[0][0]

    if promoted and (promoted.resolved_url or promoted.original_url) not in used_urls:
        return promoted

    # Fallback: best link not yet used
    unused = [l for l in links if (l.resolved_url or l.original_url) not in used_urls]
    if unused:
        return max(unused, key=lambda l: l.enrichment_rank)

    return None


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


def _append_brand(chunks: list[str], include_source: bool = True) -> tuple[list[str], bool, bool]:
    """Append brand and optionally source symbols to the last chunk if room permits.

    Returns (modified_chunks, brand_appended, source_appended).
    """
    if not chunks:
        return chunks, False, False

    last = chunks[-1]
    brand_appended = False
    source_appended = False

    # Try both symbols first
    if include_source:
        full_suffix = f" {_SOURCE_SYMBOL} {_BRAND_SYMBOL}"
        if _grapheme_len(last) + _grapheme_len(full_suffix) <= MAX_GRAPHEMES:
            return chunks[:-1] + [last + full_suffix], True, True

    # Fallback: try just brand
    brand_suffix = f" {_BRAND_SYMBOL}"
    if _grapheme_len(last) + _grapheme_len(brand_suffix) <= MAX_GRAPHEMES:
        return chunks[:-1] + [last + brand_suffix], True, False

    return chunks, False, False


def _build_facets(text: str, client: Any) -> list[models.AppBskyRichtextFacet.Main]:
    """Build AT Protocol facets for a text string."""
    facets: list[models.AppBskyRichtextFacet.Main] = []

    mentions = parse_mentions(text)
    for m in mentions[:10]:  # FR-29: cap at 10 mentions
        try:
            resp = client.com.atproto.identity.resolve_handle(params={"handle": m["handle"]})
            did = resp.did
            facets.append(models.AppBskyRichtextFacet.Main(
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=m["start"], byte_end=m["end"]
                ),
                features=[models.AppBskyRichtextFacet.Mention(did=did)],
            ))
        except Exception:
            pass  # Unresolvable mentions render as plain text
        finally:
            time.sleep(0.2)  # FR-29: always rate-limit, even on exception

    for u in parse_urls(text):
        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=u["start"], byte_end=u["end"]
            ),
            features=[models.AppBskyRichtextFacet.Link(uri=u["url"])],
        ))

    for t in parse_tags(text):
        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=t["start"], byte_end=t["end"]
            ),
            features=[models.AppBskyRichtextFacet.Tag(tag=t["tag"])],
        ))

    return facets


def _delete_bluesky_post(client: Any, uri: str) -> bool:
    """Delete a Bluesky post by its AT URI. Returns True on success, False on failure."""
    try:
        rkey = uri.split("/")[-1]
        client.com.atproto.repo.delete_record(
            models.ComAtprotoRepoDeleteRecord.Data(
                repo=client.me.did,
                collection="app.bsky.feed.post",
                rkey=rkey,
            )
        )
        return True
    except Exception as e:
        logger.warning("Rollback: failed to delete %s: %s", uri, e)
        return False


class BlueskyAdapter(BaseAdapter):
    def __init__(self, client: Any, hashtag_rules: Optional[list[HashtagRule]] = None,
                 media_config: Optional[MediaConfig] = None, include_source_link: bool = True):
        self._client = client
        self._hashtag_rules: list[HashtagRule] = hashtag_rules or []
        self._media_cfg: MediaConfig = media_config or MediaConfig()
        self._include_source_link = include_source_link

    @property
    def platform_name(self) -> str:
        return "bluesky"

    def post(self, unified_post: UnifiedPost, dry_run: bool = False) -> PostResult:
        """Post a UnifiedPost to Bluesky, threading if needed."""
        text = apply_hashtag_rules(unified_post.text, unified_post.hashtags, self._hashtag_rules)
        chunks = chunk_text(text)
        chunks, brand_appended, source_appended = _append_brand(chunks, self._include_source_link)

        if dry_run:
            return PostResult(platform=self.platform_name, status=PostStatus.POSTED, chunk_count=len(chunks))

        # Determine post-level best link for promotion rule
        post_best_link = None
        if unified_post.links:
            post_best_link = max(unified_post.links, key=lambda link: link.enrichment_rank)

        root_ref: Optional[Any] = None
        parent_ref: Optional[Any] = None
        used_urls = set()
        promoted_link = None
        posted_uris: list[str] = []

        for i, chunk in enumerate(chunks):
            facets = _build_facets(chunk, self._client)

            if i == len(chunks) - 1:
                chunk_bytes = chunk.encode("utf-8")
                if source_appended:
                    symbol_bytes = _SOURCE_SYMBOL.encode("utf-8")
                    byte_start = chunk_bytes.rfind(symbol_bytes)
                    byte_end = byte_start + len(symbol_bytes)
                    facets.append(models.AppBskyRichtextFacet.Main(
                        index=models.AppBskyRichtextFacet.ByteSlice(
                            byte_start=byte_start, byte_end=byte_end,
                        ),
                        features=[models.AppBskyRichtextFacet.Link(uri=unified_post.source_url)],
                    ))
                if brand_appended:
                    symbol_bytes = _BRAND_SYMBOL.encode("utf-8")
                    byte_start = chunk_bytes.rfind(symbol_bytes)
                    byte_end = byte_start + len(symbol_bytes)
                    facets.append(models.AppBskyRichtextFacet.Main(
                        index=models.AppBskyRichtextFacet.ByteSlice(
                            byte_start=byte_start, byte_end=byte_end,
                        ),
                        features=[models.AppBskyRichtextFacet.Link(uri=_BRAND_URL)],
                    ))

            if i == 0 and unified_post.media:
                embed = self._build_image_embed(unified_post)
                promoted_link = post_best_link  # Promote to next chunk
            else:
                selected = _select_best_link(unified_post.links, used_urls, chunk, promoted_link)
                embed = self._build_link_embed(selected)
                if selected:
                    used_urls.add(selected.resolved_url or selected.original_url)
                promoted_link = None

            reply = None
            if i > 0 and root_ref and parent_ref:
                reply = models.AppBskyFeedPost.ReplyRef(
                    root=root_ref,
                    parent=parent_ref,
                )

            try:
                record = models.AppBskyFeedPost.Record(
                    created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    text=chunk,
                    embed=embed,
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
                if posted_uris:
                    logger.warning(
                        "Bluesky thread failed at chunk %d/%d (toot %s) — "
                        "rolling back %d posted chunk(s).",
                        i, len(chunks), unified_post.source_id, len(posted_uris),
                    )
                orphaned = [uri for uri in posted_uris
                            if not _delete_bluesky_post(self._client, uri)]
                error_msg = str(e)
                if orphaned:
                    error_msg += f"; rollback partial — manually delete: {', '.join(orphaned)}"
                return PostResult(
                    platform=self.platform_name,
                    status=PostStatus.FAILED,
                    error=error_msg,
                    chunk_count=len(chunks),
                )

            posted_uris.append(response.uri)

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
            chunk_count=len(chunks),
        )

    def _build_image_embed(self, post):
        """Build image embed from post media."""
        if not self._media_cfg.enabled or not post.media:
            return None
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
        return None

    def _build_link_embed(self, link):
        """Build link card embed from a single LinkEnrichment."""
        if not link or not self._media_cfg.enabled:
            return None
        url = link.resolved_url or link.original_url
        card = models.AppBskyEmbedExternal.External(
            uri=url,
            title=link.card_title,
            description=link.card_description,
        )
        if link.thumbnail_bytes:
            try:
                thumb_bytes = resize_image(link.thumbnail_bytes, max_size_kb=976, max_dims=(400, 400))
                thumb_upload = self._client.com.atproto.repo.upload_blob(thumb_bytes)
                card = models.AppBskyEmbedExternal.External(
                    uri=url,
                    title=link.card_title,
                    description=link.card_description,
                    thumb=thumb_upload.blob,
                )
            except Exception:
                pass
        return models.AppBskyEmbedExternal.Main(external=card)
