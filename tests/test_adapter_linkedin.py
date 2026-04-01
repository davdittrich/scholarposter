"""Tests for scholarposter.adapters.linkedin"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import httpx
import respx
from scholarposter.adapters.linkedin import LinkedInAdapter
from scholarposter.models import UnifiedPost, MediaAttachment, LinkEnrichment, PostStatus


def make_post(text="Hello world", urls=None, media=None, links=None) -> UnifiedPost:
    return UnifiedPost(
        source_id="1",
        text=text,
        source_url="https://fediscience.org/@user/1",
        created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        urls=urls or [],
        media=media or [],
        links=links or [],
    )


class TestLinkedInAdapter:
    def test_platform_name(self):
        adapter = LinkedInAdapter(access_token="test_token", owner_urn="urn:li:person:abc123")
        assert adapter.platform_name == "linkedin"

    def test_dry_run_makes_no_api_calls(self):
        adapter = LinkedInAdapter(access_token="test_token", owner_urn="urn:li:person:abc123")
        post = make_post("Hello LinkedIn")
        result = adapter.post(post, dry_run=True)
        assert result.status == PostStatus.POSTED

    @respx.mock
    def test_text_post(self):
        respx.post("https://api.linkedin.com/rest/posts").mock(
            return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:1234"})
        )
        adapter = LinkedInAdapter(access_token="test_token", owner_urn="urn:li:person:abc123")
        post = make_post("Test post text")
        result = adapter.post(post)
        assert result.status == PostStatus.POSTED

    @respx.mock
    def test_article_post_with_link(self):
        respx.post("https://api.linkedin.com/rest/posts").mock(
            return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:5678"})
        )
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            title="Test Paper",
            description="An important paper",
        )
        adapter = LinkedInAdapter(access_token="test_token", owner_urn="urn:li:person:abc123")
        post = make_post("Check this out", links=[link])
        result = adapter.post(post)
        assert result.status == PostStatus.POSTED

    @respx.mock
    def test_api_failure_returns_failed(self):
        respx.post("https://api.linkedin.com/rest/posts").mock(
            return_value=httpx.Response(401, json={"message": "Unauthorized"})
        )
        adapter = LinkedInAdapter(access_token="test_token", owner_urn="urn:li:person:abc123")
        post = make_post("Test post")
        result = adapter.post(post)
        assert result.status == PostStatus.FAILED

    @respx.mock
    def test_image_upload_flow(self):
        # Step 1: register image upload
        respx.post("https://api.linkedin.com/rest/images?action=initializeUpload").mock(
            return_value=httpx.Response(200, json={
                "value": {
                    "uploadUrl": "https://upload.linkedin.com/media/upload",
                    "image": "urn:li:image:1234",
                }
            })
        )
        # Step 2: upload binary
        respx.put("https://upload.linkedin.com/media/upload").mock(
            return_value=httpx.Response(201)
        )
        # Step 3: post with image
        respx.post("https://api.linkedin.com/rest/posts").mock(
            return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:9999"})
        )
        att = MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg", alt_text="A chart")
        adapter = LinkedInAdapter(access_token="test_token", owner_urn="urn:li:person:abc123")
        with patch("scholarposter.adapters.linkedin.download_media", return_value=b"\xff\xd8\xff"):
            post = make_post("Post with image", media=[att])
            result = adapter.post(post)
        assert result.status == PostStatus.POSTED
