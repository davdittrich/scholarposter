"""LinkedIn adapter for scholarposter using the Community Management API."""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from loguru import logger

from scholarposter.adapters.base import BaseAdapter
from scholarposter.config import MediaConfig
from scholarposter.enrichment.media import download_media
from scholarposter.models import PostResult, PostStatus, UnifiedPost

_API_BASE = "https://api.linkedin.com"
_LI_VERSION = "202411"
_LI_MAX_CHARS = 3000


class LinkedInAdapter(BaseAdapter):
    def __init__(
        self,
        access_token: str,
        owner_urn: str,
        media_config: Optional[MediaConfig] = None,
    ):
        self._token = access_token
        self._owner = owner_urn
        self._media_cfg: MediaConfig = media_config or MediaConfig()

    @property
    def platform_name(self) -> str:
        return "linkedin"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": _LI_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def post(self, unified_post: UnifiedPost, dry_run: bool = False) -> PostResult:
        """Post to LinkedIn using the Community Management API."""
        if dry_run:
            return PostResult(platform=self.platform_name, status=PostStatus.POSTED)

        # Upload images if present (gated on media.enabled)
        image_urn: Optional[str] = None
        if unified_post.media and self._media_cfg.enabled:
            att = unified_post.media[0]  # LinkedIn supports 1 image per post in text posts
            try:
                img_bytes = download_media(att.url)
                if img_bytes:
                    image_urn = self._upload_image(img_bytes)
            except Exception as e:
                logger.warning(f"LinkedIn image upload failed: {e}")

        # Build post payload
        payload = self._build_payload(unified_post, image_urn)

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{_API_BASE}/rest/posts",
                    headers=self._headers,
                    json=payload,
                )
        except Exception as e:
            return PostResult(
                platform=self.platform_name,
                status=PostStatus.FAILED,
                error=str(e),
            )

        if resp.status_code not in (200, 201):
            retryable = resp.status_code in (429, 500, 502, 503)
            return PostResult(
                platform=self.platform_name,
                status=PostStatus.FAILED,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=retryable,
            )

        post_id = resp.headers.get("x-restli-id", "")
        post_url = f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None
        return PostResult(
            platform=self.platform_name,
            status=PostStatus.POSTED,
            post_url=post_url,
        )

    def _upload_image(self, img_bytes: bytes) -> Optional[str]:
        """Register and upload an image, return the image URN."""
        with httpx.Client(timeout=30) as client:
            # Step 1: Initialize upload
            init_resp = client.post(
                f"{_API_BASE}/rest/images?action=initializeUpload",
                headers=self._headers,
                json={"initializeUploadRequest": {"owner": self._owner}},
            )
            init_resp.raise_for_status()
            data = init_resp.json()["value"]
            upload_url = data["uploadUrl"]
            image_urn = data["image"]

            # Validate upload URL domain before sending Bearer token
            parsed = urlparse(upload_url)
            if not parsed.hostname or not parsed.hostname.endswith(('.linkedin.com', '.licdn.com')):
                raise ValueError(f"Unexpected upload domain: {parsed.hostname}")

            # Step 2: Upload binary
            upload_headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/octet-stream",
            }
            put_resp = client.put(upload_url, headers=upload_headers, content=img_bytes)
            put_resp.raise_for_status()

        return image_urn

    def _build_payload(self, post: UnifiedPost, image_urn: Optional[str]) -> dict[str, Any]:
        """Build the LinkedIn Community Management API post payload."""
        text = post.text
        # Append first link summary if available (truncation below handles overflow)
        if post.links and post.links[0].summary:
            text = text + "\n\n" + post.links[0].summary
        if len(text) > _LI_MAX_CHARS:
            text = text[:_LI_MAX_CHARS - 1] + "…"
        payload: dict[str, Any] = {
            "author": self._owner,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if image_urn:
            payload["content"] = {
                "media": {
                    "altText": post.media[0].alt_text or "" if post.media else "",
                    "id": image_urn,
                }
            }
        elif post.links:
            link = post.links[0]
            url = link.resolved_url or link.original_url
            article: dict[str, Any] = {"source": url}
            if link.title:
                article["title"] = link.title
            if link.description:
                article["description"] = link.description
            if link.thumbnail_url:
                article["thumbnailUrl"] = link.thumbnail_url
            payload["content"] = {"article": article}

        return payload
