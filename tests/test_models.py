"""Tests for scholarposter.models"""
import pytest
from datetime import datetime, timezone
from scholarposter.models import (
    MediaType,
    MediaAttachment,
    LinkEnrichment,
    UnifiedPost,
    PostStatus,
    PostResult,
    PlatformState,
)


class TestMediaType:
    def test_enum_values(self):
        assert MediaType.IMAGE.value == "image"
        assert MediaType.VIDEO.value == "video"
        assert MediaType.AUDIO.value == "audio"
        assert MediaType.DOCUMENT.value == "document"
        assert MediaType.UNKNOWN.value == "unknown"

    def test_from_mime_image(self):
        assert MediaType.from_mime("image/jpeg") == MediaType.IMAGE
        assert MediaType.from_mime("image/png") == MediaType.IMAGE
        assert MediaType.from_mime("image/gif") == MediaType.IMAGE

    def test_from_mime_video(self):
        assert MediaType.from_mime("video/mp4") == MediaType.VIDEO
        assert MediaType.from_mime("video/webm") == MediaType.VIDEO

    def test_from_mime_audio(self):
        assert MediaType.from_mime("audio/mpeg") == MediaType.AUDIO
        assert MediaType.from_mime("audio/ogg") == MediaType.AUDIO

    def test_from_mime_document(self):
        assert MediaType.from_mime("application/pdf") == MediaType.DOCUMENT

    def test_from_mime_unknown(self):
        assert MediaType.from_mime("application/octet-stream") == MediaType.UNKNOWN
        assert MediaType.from_mime("text/plain") == MediaType.UNKNOWN
        assert MediaType.from_mime(None) == MediaType.UNKNOWN


class TestMediaAttachment:
    def test_minimal(self):
        att = MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")
        assert att.url == "https://example.com/img.jpg"
        assert att.mime_type == "image/jpeg"
        assert att.media_type == MediaType.IMAGE
        assert att.alt_text is None
        assert att.width is None
        assert att.height is None
        assert att.size_bytes is None
        assert att.duration_seconds is None

    def test_full(self):
        att = MediaAttachment(
            url="https://example.com/video.mp4",
            mime_type="video/mp4",
            alt_text="A research presentation",
            width=1920,
            height=1080,
            size_bytes=5_000_000,
            duration_seconds=120.5,
        )
        assert att.media_type == MediaType.VIDEO
        assert att.alt_text == "A research presentation"
        assert att.duration_seconds == 120.5

    def test_media_type_derived_from_mime(self):
        att = MediaAttachment(url="https://example.com/doc.pdf", mime_type="application/pdf")
        assert att.media_type == MediaType.DOCUMENT


class TestLinkEnrichment:
    def test_minimal(self):
        link = LinkEnrichment(original_url="https://doi.org/10.1000/xyz123")
        assert link.original_url == "https://doi.org/10.1000/xyz123"
        assert link.resolved_url is None
        assert link.title is None
        assert link.description is None
        assert link.doi is None
        assert link.summary is None
        assert link.body_text is None
        assert link.thumbnail_url is None
        assert link.thumbnail_bytes is None

    def test_full(self):
        link = LinkEnrichment(
            original_url="https://doi.org/10.1000/xyz123",
            resolved_url="https://journal.example.com/article/xyz123",
            title="Nash Equilibria in Dynamic Games",
            description="We study Nash equilibria in games with multiple stages.",
            doi="10.1000/xyz123",
            summary="A study of Nash equilibria with applications to auction theory.",
            body_text="Full article text here...",
            thumbnail_url="https://journal.example.com/og-image.jpg",
            thumbnail_bytes=b"\xff\xd8\xff\xe0",
        )
        assert link.doi == "10.1000/xyz123"
        assert link.thumbnail_bytes == b"\xff\xd8\xff\xe0"


class TestUnifiedPost:
    def test_minimal(self):
        post = UnifiedPost(
            source_id="113456789012345678",
            text="Hello world",
            source_url="https://fediscience.org/@user/113456789012345678",
            created_at=datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc),
        )
        assert post.source_id == "113456789012345678"
        assert post.text == "Hello world"
        assert post.is_reblog is False
        assert post.original_author is None
        assert post.original_url is None
        assert post.media == []
        assert post.hashtags == []
        assert post.urls == []
        assert post.links == []
        assert post.is_sensitive is False
        assert post.has_poll is False

    def test_reblog_fields(self):
        post = UnifiedPost(
            source_id="99999",
            text="Boosted: some text",
            source_url="https://fediscience.org/@user/99999",
            created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            is_reblog=True,
            original_author="researcher@other.instance",
            original_url="https://other.instance/@researcher/12345",
        )
        assert post.is_reblog is True
        assert post.original_author == "researcher@other.instance"

    def test_with_media_and_hashtags(self):
        att = MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")
        post = UnifiedPost(
            source_id="111",
            text="A post with media #Science",
            source_url="https://fediscience.org/@user/111",
            created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            media=[att],
            hashtags=["Science"],
            urls=["https://example.com/paper"],
            is_sensitive=False,
            has_poll=False,
        )
        assert len(post.media) == 1
        assert "Science" in post.hashtags
        assert "https://example.com/paper" in post.urls


class TestPostStatus:
    def test_enum_values(self):
        assert PostStatus.POSTED.value == "posted"
        assert PostStatus.FAILED.value == "failed"
        assert PostStatus.SKIPPED.value == "skipped"


class TestPostResult:
    def test_success(self):
        result = PostResult(
            platform="bluesky",
            status=PostStatus.POSTED,
            post_url="https://bsky.app/profile/user/post/abc123",
        )
        assert result.is_success is True
        assert result.error is None

    def test_failure(self):
        result = PostResult(
            platform="linkedin",
            status=PostStatus.FAILED,
            error="Rate limit exceeded",
        )
        assert result.is_success is False
        assert result.post_url is None

    def test_skipped(self):
        result = PostResult(platform="bluesky", status=PostStatus.SKIPPED)
        assert result.is_success is False


class TestPlatformState:
    def test_empty(self):
        state = PlatformState()
        assert state.last_toot_id is None
        assert state.last_status is None
        assert state.last_posted_at is None
        assert state.last_error is None

    def test_full(self):
        state = PlatformState(
            last_toot_id=113456789012345678,
            last_status="posted",
            last_posted_at=datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc),
            last_error=None,
        )
        assert state.last_toot_id == 113456789012345678
        assert state.last_status == "posted"


class TestIsMediaOnly:
    def _make_post(self, text="", media=None):
        return UnifiedPost(
            source_id="1", text=text,
            source_url="https://fediscience.org/@user/1",
            created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            media=media or [],
        )

    def _make_media(self):
        return [MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")]

    def test_is_media_only_true_for_url_only_text(self):
        post = self._make_post(text="https://example.com", media=self._make_media())
        assert post.is_media_only is True

    def test_is_media_only_true_for_hashtag_only_text(self):
        post = self._make_post(text="#photo #science", media=self._make_media())
        assert post.is_media_only is True

    def test_is_media_only_true_for_whitespace_only_text(self):
        post = self._make_post(text="   ", media=self._make_media())
        assert post.is_media_only is True

    def test_is_media_only_true_for_empty_text(self):
        post = self._make_post(text="", media=self._make_media())
        assert post.is_media_only is True

    def test_is_media_only_false_with_prose(self):
        post = self._make_post(text="Great paper! https://example.com", media=self._make_media())
        assert post.is_media_only is False

    def test_is_media_only_false_without_media(self):
        post = self._make_post(text="", media=[])
        assert post.is_media_only is False
