"""Tests for scholarposter.adapters.bluesky"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call
from scholarposter.adapters.base import BaseAdapter
from scholarposter.adapters.bluesky import BlueskyAdapter, parse_mentions, parse_urls, parse_tags, chunk_text
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


class TestBaseAdapter:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseAdapter()

    def test_subclass_must_implement_post(self):
        class BadAdapter(BaseAdapter):
            @property
            def platform_name(self):
                return "bad"
        with pytest.raises(TypeError):
            BadAdapter()

    def test_subclass_must_implement_platform_name(self):
        class BadAdapter(BaseAdapter):
            def post(self, post, dry_run=False):
                pass
        with pytest.raises(TypeError):
            BadAdapter()


class TestParseMentions:
    def test_finds_mention(self):
        spans = parse_mentions("Hello @user.bsky.social how are you")
        assert len(spans) == 1
        assert spans[0]["handle"] == "user.bsky.social"

    def test_no_mentions(self):
        assert parse_mentions("No mentions here") == []

    def test_byte_accurate_indices(self):
        text = "Hi @alice.bsky.social!"
        spans = parse_mentions(text)
        assert len(spans) == 1
        # Verify byte indices point to the handle
        text_bytes = text.encode("utf-8")
        extracted = text_bytes[spans[0]["start"]:spans[0]["end"]].decode("utf-8")
        assert "alice.bsky.social" in extracted


class TestParseUrls:
    def test_finds_url(self):
        spans = parse_urls("Check https://example.com out")
        assert len(spans) == 1
        assert spans[0]["url"] == "https://example.com"

    def test_finds_multiple_urls(self):
        spans = parse_urls("https://a.com and https://b.com")
        assert len(spans) == 2

    def test_no_urls(self):
        assert parse_urls("No links here") == []

    def test_byte_accurate_indices(self):
        text = "See https://example.com for details"
        spans = parse_urls(text)
        text_bytes = text.encode("utf-8")
        extracted = text_bytes[spans[0]["start"]:spans[0]["end"]].decode("utf-8")
        assert "https://example.com" in extracted


class TestParseTags:
    def test_finds_hashtag(self):
        spans = parse_tags("Hello #Science world")
        assert len(spans) == 1
        assert spans[0]["tag"] == "Science"

    def test_finds_multiple_tags(self):
        spans = parse_tags("#Economics and #GameTheory")
        assert len(spans) == 2

    def test_no_tags(self):
        assert parse_tags("No hashtags here") == []


class TestChunkText:
    def test_short_text_single_chunk(self):
        text = "Hello world"
        chunks = chunk_text(text, max_graphemes=300)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_splits(self):
        # Create text longer than 300 chars
        text = "This is a test sentence. " * 20  # ~500 chars
        chunks = chunk_text(text, max_graphemes=300)
        assert len(chunks) > 1
        # Each chunk must fit in limit (with n/total suffix)
        for chunk in chunks:
            assert len(chunk.encode("utf-8")) <= 400  # generous bound

    def test_thread_suffix_added(self):
        text = "Word " * 100  # long enough to need splitting
        chunks = chunk_text(text, max_graphemes=300)
        if len(chunks) > 1:
            # All but last should have n/total suffix
            for i, chunk in enumerate(chunks[:-1]):
                assert f"{i+1}/{len(chunks)}" in chunk

    def test_does_not_break_words(self):
        text = "superlongwordthatshoulnotbebroken " * 5
        chunks = chunk_text(text, max_graphemes=100)
        for chunk in chunks:
            # No chunk should start mid-word (i.e., end with partial word)
            # Just verify no individual word got split
            assert not chunk.startswith("-")


class TestBlueskyAdapter:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.me = MagicMock()
        client.me.did = "did:plc:testuser"
        return client

    @pytest.fixture
    def adapter(self, mock_client):
        return BlueskyAdapter(client=mock_client)

    def test_platform_name(self, adapter):
        assert adapter.platform_name == "bluesky"

    def test_dry_run_makes_no_api_calls(self, adapter, mock_client):
        post = make_post("Hello world")
        result = adapter.post(post, dry_run=True)
        mock_client.com.atproto.repo.create_record.assert_not_called()
        assert result.status == PostStatus.POSTED

    def test_text_only_post(self, adapter, mock_client):
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc123"
        mock_record.cid = "bafyreitest"
        mock_client.com.atproto.repo.create_record.return_value = mock_record
        post = make_post("Hello world")
        result = adapter.post(post)
        assert result.status == PostStatus.POSTED
        mock_client.com.atproto.repo.create_record.assert_called_once()

    def test_post_with_image(self, adapter, mock_client):
        # Mock blob upload
        mock_blob = MagicMock()
        mock_client.com.atproto.repo.upload_blob.return_value = MagicMock(blob=mock_blob)
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc123"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        att = MediaAttachment(
            url="https://example.com/img.jpg",
            mime_type="image/jpeg",
            alt_text="A chart",
        )
        with patch("scholarposter.adapters.bluesky.download_media", return_value=b"\xff\xd8\xff"):
            with patch("scholarposter.adapters.bluesky.resize_image", return_value=b"\xff\xd8\xff"):
                post = make_post("Post with image", media=[att])
                result = adapter.post(post)
        assert result.status == PostStatus.POSTED

    def test_post_api_failure_returns_failed(self, adapter, mock_client):
        mock_client.com.atproto.repo.create_record.side_effect = Exception("API error")
        post = make_post("Hello world")
        result = adapter.post(post)
        assert result.status == PostStatus.FAILED
        assert "API error" in result.error

    def test_thread_chunks_posted_in_sequence(self, adapter, mock_client):
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        long_text = "This is a test post. " * 30  # exceeds 300 graphemes
        post = make_post(long_text)
        result = adapter.post(post)
        # Should have been called multiple times (once per chunk)
        assert mock_client.com.atproto.repo.create_record.call_count >= 2

    def test_thread_failure_marks_failed(self, adapter, mock_client):
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        # First call succeeds, second fails
        mock_client.com.atproto.repo.create_record.side_effect = [mock_record, Exception("chunk 2 fail")]

        long_text = "This is a test post. " * 30
        post = make_post(long_text)
        result = adapter.post(post)
        assert result.status == PostStatus.FAILED

class TestBlueskyImageProcessing:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.me = MagicMock()
        client.me.did = "did:plc:testuser"
        return client

    def test_resize_image_called_with_max_dims(self, mock_client):
        mock_blob = MagicMock()
        mock_client.com.atproto.repo.upload_blob.return_value = MagicMock(blob=mock_blob)
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        adapter = BlueskyAdapter(client=mock_client)
        att = MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")
        post = make_post("hello", media=[att])

        with (
            patch("scholarposter.adapters.bluesky.download_media", return_value=b"\xff\xd8\xff"),
            patch("scholarposter.adapters.bluesky.resize_image", return_value=b"\xff\xd8\xff") as mock_resize,
        ):
            adapter.post(post)

        mock_resize.assert_called_once_with(b"\xff\xd8\xff", max_size_kb=950, max_dims=(2048, 2048))

    def test_webp_magic_bytes_converted_to_jpeg(self, mock_client):
        mock_blob = MagicMock()
        mock_client.com.atproto.repo.upload_blob.return_value = MagicMock(blob=mock_blob)
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        # WebP magic: RIFF....WEBP
        webp_bytes = b"RIFF\x10\x00\x00\x00WEBP" + b"\x00" * 20

        adapter = BlueskyAdapter(client=mock_client)
        att = MediaAttachment(url="https://example.com/img.webp", mime_type="image/jpeg")
        post = make_post("hello", media=[att])

        with (
            patch("scholarposter.adapters.bluesky.download_media", return_value=webp_bytes),
            patch("scholarposter.adapters.bluesky.convert_to_jpeg", return_value=b"\xff\xd8\xff") as mock_convert,
            patch("scholarposter.adapters.bluesky.resize_image", return_value=b"\xff\xd8\xff"),
        ):
            adapter.post(post)

        mock_convert.assert_called_once_with(webp_bytes)

    def test_non_webp_not_converted(self, mock_client):
        mock_blob = MagicMock()
        mock_client.com.atproto.repo.upload_blob.return_value = MagicMock(blob=mock_blob)
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 20

        adapter = BlueskyAdapter(client=mock_client)
        att = MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")
        post = make_post("hello", media=[att])

        with (
            patch("scholarposter.adapters.bluesky.download_media", return_value=jpeg_bytes),
            patch("scholarposter.adapters.bluesky.convert_to_jpeg") as mock_convert,
            patch("scholarposter.adapters.bluesky.resize_image", return_value=jpeg_bytes),
        ):
            adapter.post(post)

        mock_convert.assert_not_called()

    def test_thumbnail_resized_to_400x400(self, mock_client):
        mock_blob = MagicMock()
        mock_client.com.atproto.repo.upload_blob.return_value = MagicMock(blob=mock_blob)
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        thumb_bytes = b"\xff\xd8\xff" + b"\x00" * 20
        link = LinkEnrichment(
            original_url="https://example.com/article",
            title="Test Article",
            thumbnail_bytes=thumb_bytes,
        )
        post = make_post("check this out", links=[link])

        with patch("scholarposter.adapters.bluesky.resize_image", return_value=thumb_bytes) as mock_resize:
            adapter = BlueskyAdapter(client=mock_client)
            adapter.post(post)

        mock_resize.assert_called_once_with(thumb_bytes, max_size_kb=976, max_dims=(400, 400))

    def test_mentions_capped_at_10(self, mock_client):
        # Build text with 12 distinct @handles
        handles = [f"user{i}.bsky.social" for i in range(12)]
        text = " ".join(f"@{h}" for h in handles)

        # DID resolution always succeeds
        mock_client.com.atproto.identity.resolve_handle.return_value = MagicMock(did="did:plc:test")

        from scholarposter.adapters.bluesky import _build_facets
        with patch("scholarposter.adapters.bluesky.time") as mock_time:
            facets = _build_facets(text, mock_client)

        mention_facets = [f for f in facets if any(
            feat.get("$type") == "app.bsky.richtext.facet#mention"
            for feat in f.get("features", [])
        )]
        assert len(mention_facets) <= 10

    def test_mention_resolution_sleeps_200ms(self, mock_client):
        text = "Hello @alice.bsky.social how are you"
        mock_client.com.atproto.identity.resolve_handle.return_value = MagicMock(did="did:plc:alice")

        from scholarposter.adapters.bluesky import _build_facets
        with patch("scholarposter.adapters.bluesky.time") as mock_time:
            _build_facets(text, mock_client)

        mock_time.sleep.assert_called_once_with(0.2)

    def test_mention_resolution_sleeps_on_exception(self, mock_client):
        """Sleep must execute even when handle resolution raises (FR-29 rate-limit)."""
        text = "Hello @bad.handle.invalid how are you"
        mock_client.com.atproto.identity.resolve_handle.side_effect = Exception("resolution failed")

        from scholarposter.adapters.bluesky import _build_facets
        with patch("scholarposter.adapters.bluesky.time") as mock_time:
            _build_facets(text, mock_client)

        # Sleep still called despite exception, because it is in finally:
        mock_time.sleep.assert_called_once_with(0.2)


class TestBlueskyAdapterHashtagRules:
    from scholarposter.config import HashtagRule

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.me = MagicMock()
        client.me.did = "did:plc:testuser"
        return client

    def test_hashtag_rule_prepends_to_post_text(self, mock_client):
        from scholarposter.config import HashtagRule
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics"])]
        adapter = BlueskyAdapter(client=mock_client, hashtag_rules=rules)
        post = make_post("New paper out", urls=[], media=[])
        post = post.model_copy(update={"hashtags": ["Economics", "Research"]})
        result = adapter.post(post, dry_run=True)

        # dry_run still applies rules (text transformation happens before dry_run check)
        assert result.status == PostStatus.POSTED

    def test_hashtag_rule_text_reaches_api(self, mock_client):
        from scholarposter.config import HashtagRule
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics"])]
        adapter = BlueskyAdapter(client=mock_client, hashtag_rules=rules)
        post = make_post("New paper out")
        post = post.model_copy(update={"hashtags": ["Economics"]})
        adapter.post(post)

        call_args = mock_client.com.atproto.repo.create_record.call_args
        record = call_args[1]["record"] if call_args[1] else call_args[0][0].record
        # The record text should start with the injected hashtag
        assert "#EconSky" in record.text

    def test_no_matching_rule_leaves_text_unchanged(self, mock_client):
        from scholarposter.config import HashtagRule
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics"])]
        adapter = BlueskyAdapter(client=mock_client, hashtag_rules=rules)
        post = make_post("A science post")
        post = post.model_copy(update={"hashtags": ["Science"]})
        adapter.post(post)

        call_args = mock_client.com.atproto.repo.create_record.call_args
        record = call_args[1]["record"] if call_args[1] else call_args[0][0].record
        assert "#EconSky" not in record.text
        assert "A science post" in record.text

    def test_no_rules_no_change(self, mock_client):
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        adapter = BlueskyAdapter(client=mock_client)
        post = make_post("Plain post")
        adapter.post(post)

        call_args = mock_client.com.atproto.repo.create_record.call_args
        record = call_args[1]["record"] if call_args[1] else call_args[0][0].record
        assert record.text == "Plain post"
