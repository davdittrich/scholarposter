"""Tests for scholarposter.adapters.linkedin"""
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
import respx
from scholarposter.adapters.linkedin import LinkedInAdapter
from scholarposter.config import EnrichmentConfig, MediaConfig, ThumbnailFallbackConfig
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
        """Updated: thumbnail_bytes required; mock _upload_image and posts API."""
        respx.post("https://api.linkedin.com/rest/posts").mock(
            return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:5678"})
        )
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            title="Test Paper",
            description="An important paper",
            thumbnail_bytes=b"IMGDATA",
        )
        adapter = LinkedInAdapter(
            access_token="test_token",
            owner_urn="urn:li:person:abc123",
            media_config=MediaConfig(enabled=True),
        )
        post = make_post("Check this out", links=[link])
        with patch.object(adapter, '_upload_image', return_value="urn:li:image:5678"):
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

class TestCardDescriptionInPayload:
    """FR-20c, FR-34a: summary in article card, not in text."""

    def test_summary_not_in_commentary(self):
        """FR-20c: summary never appended to commentary text."""
        adapter = LinkedInAdapter(
            access_token="test-token", owner_urn="urn:li:person:test",
            media_config=MediaConfig(enabled=True),
        )
        post = UnifiedPost(
            source_id="1", text="Check this out", source_url="https://x.com/1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            links=[LinkEnrichment(
                original_url="https://example.com",
                summary="AI summary that should NOT be in text",
            )],
        )
        payload = adapter._build_payload(post, image_urn=None)
        assert "AI summary that should NOT be in text" not in payload["commentary"]

    def test_article_uses_card_description(self):
        """FR-34a: article description from link.card_description."""
        adapter = LinkedInAdapter(
            access_token="test-token", owner_urn="urn:li:person:test",
            media_config=MediaConfig(enabled=True),
        )
        post = UnifiedPost(
            source_id="1", text="Paper", source_url="https://x.com/1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            links=[LinkEnrichment(
                original_url="https://example.com",
                doi="10.1000/test",
                crossref_title="Crossref Title",
                crossref_abstract="Crossref Abstract",
                title="OG Title",
                description="OG Desc",
            )],
        )
        payload = adapter._build_payload(post, image_urn=None)
        # DOI link → card_description uses crossref_abstract
        assert payload["content"]["article"]["description"] == "Crossref Abstract"
        assert payload["content"]["article"]["title"] == "Crossref Title"

    def test_most_enriched_link_selected(self):
        """FR-34a: most enriched URL drives the article card."""
        adapter = LinkedInAdapter(
            access_token="test-token", owner_urn="urn:li:person:test",
            media_config=MediaConfig(enabled=True),
        )
        post = UnifiedPost(
            source_id="1", text="Two links", source_url="https://x.com/1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            links=[
                LinkEnrichment(original_url="https://bare.com"),
                LinkEnrichment(original_url="https://doi.org/10.1000/x", doi="10.1000/x",
                               crossref_title="DOI Paper", crossref_abstract="DOI Abstract"),
            ],
        )
        payload = adapter._build_payload(post, image_urn=None)
        assert payload["content"]["article"]["source"] == "https://doi.org/10.1000/x"


class TestArticleThumbnailAndTitle:
    """WU-1: LinkedIn article payload — required title + thumbnail ImageUrn."""

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _adapter(media_enabled: bool = True) -> LinkedInAdapter:
        return LinkedInAdapter(
            access_token="tok",
            owner_urn="urn:li:person:test",
            media_config=MediaConfig(enabled=media_enabled),
        )

    # ── test 1: title netloc fallback when card_title is "" ──────────────────

    def test_article_title_always_present_when_no_card_title(self):
        """Discriminating: OLD omits 'title'; NEW uses netloc fallback."""
        adapter = self._adapter()
        post = UnifiedPost(
            source_id="1",
            text="no title link",
            source_url="https://x.com/1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            links=[LinkEnrichment(original_url="https://example.com/paper")],
        )
        # No title, no crossref_title → card_title == ""
        payload = adapter._build_payload(post, image_urn=None, article_thumbnail_urn=None)
        article = payload["content"]["article"]
        assert "title" in article, "title key must always be present"
        assert article["title"] == "example.com"

    # ── test 2: title present when card_title truthy (regression guard) ──────

    def test_article_title_present_when_card_title_truthy(self):
        """Regression guard: crossref_title → article['title']."""
        adapter = self._adapter()
        post = UnifiedPost(
            source_id="1",
            text="paper",
            source_url="https://x.com/1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            links=[LinkEnrichment(
                original_url="https://example.com/paper",
                crossref_title="Paper X",
            )],
        )
        payload = adapter._build_payload(post, image_urn=None, article_thumbnail_urn=None)
        assert payload["content"]["article"]["title"] == "Paper X"

    # ── test 3: thumbnailUrl must NOT appear; bytes=None → FAILED ────────────

    def test_article_no_thumbnailUrl_field(self):
        """Discriminating: OLD sets thumbnailUrl; NEW must not; also FAILED when bytes=None."""
        adapter = self._adapter()
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            thumbnail_url="https://example.com/img.jpg",
            thumbnail_bytes=None,
        )
        post = make_post("link post", links=[link])
        result = adapter.post(post)
        assert result.status == PostStatus.FAILED
        assert result.error and "thumbnail" in result.error.lower()

    # ── test 4: thumbnail uploaded → article["thumbnail"] = urn ─────────────

    @respx.mock
    def test_article_thumbnail_uploaded_when_bytes_present(self):
        """Discriminating: when bytes present, article gets 'thumbnail' URN; no thumbnailUrl."""
        respx.post("https://api.linkedin.com/rest/posts").mock(
            return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:X"})
        )
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            thumbnail_bytes=b"IMGDATA",
            title="Paper",
        )
        adapter = self._adapter()
        post = make_post("link post", links=[link])
        with patch.object(adapter, '_upload_image', return_value="urn:li:image:ABC") as mock_up:
            result = adapter.post(post)
            mock_up.assert_called_once_with(b"IMGDATA")
        assert result.status == PostStatus.POSTED
        # Verify payload via _build_payload directly with the urn
        payload = adapter._build_payload(post, image_urn=None, article_thumbnail_urn="urn:li:image:ABC")
        article = payload["content"]["article"]
        assert article.get("thumbnail") == "urn:li:image:ABC"
        assert "thumbnailUrl" not in article

    # ── test 5: upload raises → FAILED, post API never called ────────────────

    def test_article_thumbnail_upload_failure_returns_failed(self):
        """Discriminating: _upload_image raises → PostStatus.FAILED; no post API call."""
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            thumbnail_bytes=b"IMGDATA",
        )
        adapter = self._adapter()
        post = make_post("link post", links=[link])
        with patch.object(adapter, '_upload_image', side_effect=httpx.HTTPError("timeout")):
            with patch("scholarposter.adapters.linkedin.httpx.Client") as mock_client:
                result = adapter.post(post)
                # httpx.Client should not be called for the posts API
                mock_client.assert_not_called()
        assert result.status == PostStatus.FAILED
        assert result.error and "thumbnail" in result.error.lower()

    # ── test 6: bytes=None, fallback disabled → FAILED (T-47) ───────────────

    def test_article_no_bytes_returns_failed(self):
        """Discriminating: link present, bytes=None, fallback disabled → FAILED before posts API."""
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            thumbnail_bytes=None,
        )
        adapter = LinkedInAdapter(
            access_token="tok",
            owner_urn="urn:li:person:test",
            media_config=MediaConfig(enabled=True),
            enrichment_cfg=EnrichmentConfig(thumbnail_fallback=ThumbnailFallbackConfig(enabled=False)),
        )
        post = make_post("link post", links=[link])
        with patch("scholarposter.adapters.linkedin.httpx.Client") as mock_client:
            result = adapter.post(post)
            mock_client.assert_not_called()
        assert result.status == PostStatus.FAILED
        assert result.error and "thumbnail" in result.error.lower()

    # ── test 7: media.enabled=False → FAILED with "media.enabled=False" ──────

    def test_article_media_disabled_returns_failed(self):
        """Discriminating: media disabled → FAILED before posts API."""
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            thumbnail_bytes=b"IMGDATA",
        )
        adapter = self._adapter(media_enabled=False)
        post = make_post("link post", links=[link])
        with patch("scholarposter.adapters.linkedin.httpx.Client") as mock_client:
            result = adapter.post(post)
            mock_client.assert_not_called()
        assert result.status == PostStatus.FAILED
        assert result.error and "media.enabled=False" in result.error
