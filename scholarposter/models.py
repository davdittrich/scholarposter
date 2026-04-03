"""Data models for scholarposter."""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, model_validator

_URL_STRIP_RE = re.compile(r'https?://[^\s]+')
_TAG_STRIP_RE = re.compile(r'#\S+')


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    UNKNOWN = "unknown"

    @classmethod
    def from_mime(cls, mime_type: Optional[str]) -> "MediaType":
        if not mime_type:
            return cls.UNKNOWN
        prefix = mime_type.split("/")[0]
        if prefix == "image":
            return cls.IMAGE
        if prefix == "video":
            return cls.VIDEO
        if prefix == "audio":
            return cls.AUDIO
        if mime_type == "application/pdf":
            return cls.DOCUMENT
        return cls.UNKNOWN


class MediaAttachment(BaseModel):
    url: str
    mime_type: str
    media_type: MediaType = MediaType.UNKNOWN
    alt_text: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None

    @model_validator(mode="after")
    def derive_media_type(self) -> "MediaAttachment":
        self.media_type = MediaType.from_mime(self.mime_type)
        return self


class LinkEnrichment(BaseModel):
    original_url: str
    resolved_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    doi: Optional[str] = None
    summary: Optional[str] = None
    body_text: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_bytes: Optional[bytes] = None


class UnifiedPost(BaseModel):
    source_id: str
    text: str
    source_url: str
    created_at: datetime
    is_reblog: bool = False
    original_author: Optional[str] = None
    original_url: Optional[str] = None
    media: list[MediaAttachment] = []
    hashtags: list[str] = []
    urls: list[str] = []
    links: list[LinkEnrichment] = []
    is_sensitive: bool = False
    has_poll: bool = False

    @property
    def is_media_only(self) -> bool:
        """True if post has media but no meaningful text content (only URLs/hashtags)."""
        if not self.media:
            return False
        stripped = _URL_STRIP_RE.sub('', self.text)
        stripped = _TAG_STRIP_RE.sub('', stripped)
        return stripped.strip() == ""


class PostStatus(str, Enum):
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED = "skipped"


class PostResult(BaseModel):
    platform: str
    status: PostStatus
    post_url: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False

    @property
    def is_success(self) -> bool:
        return self.status == PostStatus.POSTED


class PlatformState(BaseModel):
    last_toot_id: Optional[int] = None
    last_status: Optional[str] = None
    last_posted_at: Optional[datetime] = None
    last_error: Optional[str] = None


class BibliographyEntry(BaseModel):
    doi: str
    title: str
    authors: list[str] = []
    abstract: str = ""
    url: str
    shared_at: datetime
    publication_year: Optional[int] = None
    platforms: list[str] = []
    source_toot_id: str = ""  # traceability: links entry back to source toot
