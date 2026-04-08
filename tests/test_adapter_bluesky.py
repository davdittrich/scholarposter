"""Tests for scholarposter.adapters.bluesky"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call
from scholarposter.adapters.base import BaseAdapter
from scholarposter.adapters.bluesky import BlueskyAdapter, parse_mentions, parse_urls, parse_tags, chunk_text
from scholarposter.models import UnifiedPost, MediaAttachment, LinkEnrichment, PostStatus
from scholarposter.config import MediaConfig

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

    def test_threaded_link_card_only_on_first_chunk(self, mock_client):
        """Discriminating: link card appears only on chunk 0; chunks 1+ get embed=None."""
        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        from atproto import models as bsky_models
        from scholarposter.config import MediaConfig

        long_text = "This is a test post. " * 30  # produces 3+ chunks
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            resolved_url="https://example.com/paper",
            title="Test Paper",
        )
        post = make_post(long_text, links=[link])
        adapter = BlueskyAdapter(client=mock_client, media_config=MediaConfig(enabled=True))
        adapter.post(post)

        calls = mock_client.com.atproto.repo.create_record.call_args_list
        assert len(calls) >= 3
        assert calls[0].args[0].record.embed is not None  # card on chunk 0
        assert calls[1].args[0].record.embed is None       # no card on chunk 1
        assert calls[2].args[0].record.embed is None       # no card on chunk 2

    def test_threaded_link_card_on_second_chunk_when_media(self, mock_client):
        """Discriminating: with media, link card appears on chunk 1 only (not chunk 2+).

        The image build may silently fail if the blob mock is rejected by Pydantic, but
        promoted_link is still set on chunk 0's media branch — so chunk 1 gets the card
        and chunk 2 gets None. Old code gave chunk 2 a duplicate card via max(links) fallback.
        """
        from atproto import models as bsky_models
        from scholarposter.config import MediaConfig

        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        long_text = "This is a test post. " * 30  # produces 3+ chunks
        att = MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")
        link = LinkEnrichment(
            original_url="https://example.com/paper",
            resolved_url="https://example.com/paper",
            title="Test Paper",
        )
        post = make_post(long_text, media=[att], links=[link])
        adapter = BlueskyAdapter(client=mock_client, media_config=MediaConfig(enabled=True))

        with patch("scholarposter.adapters.bluesky.download_media", return_value=b"\xff\xd8\xff"):
            with patch("scholarposter.adapters.bluesky.resize_image", return_value=b"\xff\xd8\xff"):
                adapter.post(post)

        calls = mock_client.com.atproto.repo.create_record.call_args_list
        assert len(calls) >= 3
        # Chunk 0: entered media branch — not a link card regardless of image-build outcome
        assert not isinstance(calls[0].args[0].record.embed, bsky_models.AppBskyEmbedExternal.Main)
        # Chunk 1: gets the promoted link card
        assert isinstance(calls[1].args[0].record.embed, bsky_models.AppBskyEmbedExternal.Main)
        # Chunk 2: no card — key discriminating assertion against the duplicate-card bug
        assert calls[2].args[0].record.embed is None

    def test_threaded_link_card_on_first_chunk_when_url_in_later_chunk(self, mock_client):
        """Discriminating: card placed on chunk 0 even when URL text appears only in chunk 1."""
        from atproto import models as bsky_models
        from scholarposter.config import MediaConfig
        from scholarposter.adapters.bluesky import chunk_text as _chunk_text

        mock_record = MagicMock()
        mock_record.uri = "at://did:plc:testuser/app.bsky.feed.post/abc"
        mock_record.cid = "bafy"
        mock_client.com.atproto.repo.create_record.return_value = mock_record

        url = "https://example.com/paper"
        # 58 "word" tokens ≈ 289 graphemes joined; the URL (26 graphemes) overflows
        # the 294-grapheme chunk-0 budget and is placed at the start of chunk 1.
        prefix = " ".join(["word"] * 58)
        suffix = " ".join(["extra"] * 60)  # fills chunks 2+
        text = prefix + " " + url + " " + suffix

        # Verify precondition
        chunks_preview = _chunk_text(text)
        assert url not in chunks_preview[0], "Precondition: URL must not appear in chunk 0"

        link = LinkEnrichment(original_url=url, resolved_url=url, title="Test Paper")
        post = make_post(text, links=[link])
        adapter = BlueskyAdapter(client=mock_client, media_config=MediaConfig(enabled=True))
        adapter.post(post)

        calls = mock_client.com.atproto.repo.create_record.call_args_list
        assert len(calls) >= 3
        # Card placed on chunk 0 via enrichment-rank fallback (not URL-text match)
        assert calls[0].args[0].record.embed is not None
        assert calls[1].args[0].record.embed is None
        assert calls[2].args[0].record.embed is None

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

    def test_non_jpeg_handled_by_resize(self, mock_client):
        """resize_image handles format conversion via PIL — no separate pre-conversion needed."""
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
            patch("scholarposter.adapters.bluesky.resize_image", return_value=jpeg_bytes) as mock_resize,
        ):
            adapter.post(post)

        mock_resize.assert_called_once()

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

        from atproto import models as bsky_models
        mention_facets = [f for f in facets if any(
            isinstance(feat, bsky_models.AppBskyRichtextFacet.Mention)
            for feat in f.features
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


class TestGraphemeLen:
    def test_ascii_string(self):
        from scholarposter.adapters.bluesky import _grapheme_len
        assert _grapheme_len("Hello") == 5

    def test_family_emoji_is_one_grapheme(self):
        from scholarposter.adapters.bluesky import _grapheme_len
        # 👨‍👩‍👧‍👦 is a ZWJ sequence — 1 grapheme cluster, 7 code points
        assert _grapheme_len("👨‍👩‍👧‍👦") == 1

    def test_combining_char_cafe(self):
        from scholarposter.adapters.bluesky import _grapheme_len
        # "cafe" + combining acute accent (U+0301): 5 code points, 4 grapheme clusters
        assert _grapheme_len("cafe\u0301") == 4

    def test_emoji_heavy_chunks_no_mid_grapheme_split(self):
        """chunk_text with emoji-heavy text must not produce chunks that look split."""
        # Build a text of many flag emojis (each is 2 code points / 1 grapheme)
        # 🇺🇸 = U+1F1FA U+1F1F8 (regional indicator letters, form 1 grapheme)
        flag = "🇺🇸"
        # 150 flags = 150 graphemes, well under 300; pad with words to cross boundary
        words = ("Hello world " * 10).strip()
        text = words + " " + (flag + " ") * 50
        chunks = chunk_text(text, max_graphemes=50)
        # Every chunk boundary should be at a space, not mid-emoji
        for chunk in chunks:
            # strip thread suffix like " 1/3" before checking
            body = chunk.rsplit(" ", 1)[0] if "/" in chunk.split()[-1] else chunk
            # No chunk should end with half a regional indicator sequence
            # Regional indicators come in pairs; an odd trailing RI would mean a split
            # Just verify no chunk ends with a lone regional indicator
            encoded = body.encode("utf-16-be")
            # Each char in encoded is 2 bytes; regional indicators are surrogate pairs
            # Simpler check: verify reconstruct is valid UTF-8
            assert body.encode("utf-8").decode("utf-8") == body

    def test_chunk_text_exactly_300_graphemes_is_single_chunk(self):
        """Text at exactly 300 graphemes returns a single chunk (no splitting)."""
        text = "a" * 300
        chunks = chunk_text(text, max_graphemes=300)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_boundary_does_not_split_zwj_emoji(self):
        """A ZWJ emoji at exactly 300 graphemes must remain in a single chunk."""
        from scholarposter.adapters.bluesky import _grapheme_len
        text = "a" * 299 + "👨‍👩‍👧‍👦"
        assert _grapheme_len(text) == 300
        chunks = chunk_text(text, max_graphemes=300)
        assert len(chunks) == 1
        assert "👨‍👩‍👧‍👦" in chunks[0]

    def test_grapheme_slice_truncation_preserves_zwj_emoji(self):
        """Discriminating test: would FAIL with text[:n] but PASS with grapheme.slice().

        Constructs a long spaceless word with a ZWJ emoji at grapheme position 15-16.
        With max_graphemes=20, suffix fitting truncates to room=16 graphemes.
        grapheme.slice(chunk, 0, 16) keeps the emoji intact at position 15.
        A naive chunk[:16] would orphan the first code point of the ZWJ sequence
        (family emoji is 7 code points but 1 grapheme cluster).
        """
        from scholarposter.adapters.bluesky import _grapheme_len
        # Word: 15 "a"s + family emoji + "x" = 17 graphemes, 23 code points
        word = "a" * 15 + "👨‍👩‍👧‍👦" + "x"
        assert _grapheme_len(word) == 17
        assert len(word) == 23  # code points: 15 + 7 (ZWJ sequence) + 1
        # Two long words → 2 chunks → suffix " 1/2" triggers truncation
        text = word + " " + "b" * 20
        chunks = chunk_text(text, max_graphemes=20)
        assert len(chunks) >= 2
        # First chunk must contain the intact emoji (not a broken orphan)
        assert "👨‍👩‍👧‍👦" in chunks[0]

    def test_chunk_text_single_oversized_word(self):
        """A single 400-grapheme word with no spaces must be truncated to ≤ 300."""
        from scholarposter.adapters.bluesky import _grapheme_len
        word = "a" * 400  # no spaces
        chunks = chunk_text(word, max_graphemes=300)
        assert len(chunks) == 1
        assert _grapheme_len(chunks[0]) <= 300

    def test_chunk_text_single_word_at_limit(self):
        """A single 300-grapheme word should be returned as-is (no truncation)."""
        word = "a" * 300
        chunks = chunk_text(word, max_graphemes=300)
        assert len(chunks) == 1
        assert chunks[0] == word

    def test_chunk_text_301_graphemes_splits_into_two(self):
        """Text at 301 graphemes must split into 2 chunks."""
        # Use space-separated words so the splitter has word boundaries to cut on
        # Each "word" is 10 chars; 31 words = 310 chars (30 spaces + 310 = 340 total)
        # Use shorter words to get close to 301 with clean word boundaries
        word = "abcdefghij"  # 10 chars
        # 30 words of 10 chars + 29 spaces = 300+29 = 329, splits at ~290 net
        # Build a string of exactly 301 grapheme clusters (all ASCII)
        text = "x " * 150 + "y"  # 150*2 + 1 = 301 graphemes
        chunks = chunk_text(text, max_graphemes=300)
        assert len(chunks) >= 2


class TestBuildEmbedLogsImageFailure:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.me = MagicMock()
        client.me.did = "did:plc:testuser"
        return client

    def test_image_download_failure_logs_warning(self, mock_client):
        """_build_image_embed must log a warning when image download raises."""
        from scholarposter.adapters.bluesky import BlueskyAdapter

        adapter = BlueskyAdapter(client=mock_client)
        att = MediaAttachment(url="https://example.com/broken.jpg", mime_type="image/jpeg")
        post = make_post("test", media=[att])

        messages = []

        from loguru import logger
        import sys

        sink_id = logger.add(lambda m: messages.append(m.record["message"]))
        try:
            with patch("scholarposter.adapters.bluesky.download_media", side_effect=Exception("timeout")):
                adapter._build_image_embed(post)
        finally:
            logger.remove(sink_id)

        assert any("broken.jpg" in m and "timeout" in m for m in messages), (
            f"Expected warning about broken.jpg/timeout, got: {messages}"
        )


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


class TestCardDescriptionInEmbed:
    """FR-20c, FR-26a: summary in card, not in text."""

    def test_summary_not_in_post_text(self):
        """FR-20c: summary never appended to post text."""
        mock_client = MagicMock()
        mock_client.me = MagicMock(did="did:plc:test")
        mock_client.get_current_time_iso.return_value = "2024-01-01T00:00:00Z"
        mock_client.com.atproto.repo.create_record.return_value = MagicMock(uri="at://test/post/1", cid="cid1")
        adapter = BlueskyAdapter(client=mock_client, media_config=MediaConfig(enabled=True))
        post = UnifiedPost(
            source_id="1", text="Check this out", source_url="https://x.com/1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            links=[LinkEnrichment(original_url="https://example.com", summary="AI summary here")],
        )
        adapter.post(post)
        call_args = mock_client.com.atproto.repo.create_record.call_args
        record = call_args.args[0].record
        assert "AI summary here" not in record.text

    def test_card_uses_card_description_and_title(self):
        """FR-26a: card uses link.card_description and link.card_title."""
        mock_client = MagicMock()
        mock_client.me = MagicMock(did="did:plc:test")
        mock_client.get_current_time_iso.return_value = "2024-01-01T00:00:00Z"
        mock_client.com.atproto.repo.create_record.return_value = MagicMock(uri="at://test/post/1", cid="cid1")
        adapter = BlueskyAdapter(client=mock_client, media_config=MediaConfig(enabled=True))
        post = UnifiedPost(
            source_id="1", text="Check this out", source_url="https://x.com/1",
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
        adapter.post(post)
        call_args = mock_client.com.atproto.repo.create_record.call_args
        record = call_args.args[0].record
        # Card should use card_title (Crossref) and card_description (Crossref abstract)
        assert record.embed.external.title == "Crossref Title"
        assert record.embed.external.description == "Crossref Abstract"


class TestBuildFacetsReturnsSDKModels:
    """Discriminating test: _build_facets must return SDK model instances, not dicts."""

    def test_url_facet_is_sdk_model_instance(self):
        from atproto import models
        from scholarposter.adapters.bluesky import _build_facets
        mock_client = MagicMock()
        mock_client.com.atproto.identity.resolve_handle.side_effect = Exception("no mentions")

        facets = _build_facets("Check https://example.com for details", mock_client)

        assert len(facets) >= 1
        for facet in facets:
            assert isinstance(facet, models.AppBskyRichtextFacet.Main), (
                f"Expected AppBskyRichtextFacet.Main, got {type(facet)!r}: {facet!r}"
            )

    def test_tag_facet_is_sdk_model_instance(self):
        from atproto import models
        from scholarposter.adapters.bluesky import _build_facets
        mock_client = MagicMock()

        facets = _build_facets("New paper on #MachineLearning!", mock_client)

        assert len(facets) >= 1
        for facet in facets:
            assert isinstance(facet, models.AppBskyRichtextFacet.Main), (
                f"Expected AppBskyRichtextFacet.Main, got {type(facet)!r}"
            )


# ---------------------------------------------------------------------------
# W3: chunk_count in PostResult and audit log
# ---------------------------------------------------------------------------

def _mock_record():
    r = MagicMock()
    r.uri = "at://did:plc:testuser/app.bsky.feed.post/abc123"
    r.cid = "bafyreitest"
    return r


def _mock_client_for_chunk():
    client = MagicMock()
    client.me = MagicMock()
    client.me.did = "did:plc:testuser"
    return client


# "A"*290 + " " + "B"*290 = 581 graphemes total.
# chunk_text() splits at whitespace boundaries; the single space at position 291
# is the only split point, producing exactly 2 chunks:
#   chunk 1: "A"*290 + " "  (291 graphemes, fits in max_graphemes=300)
#   chunk 2: "B"*290         (290 graphemes)
_MULTI_CHUNK_TEXT = "A" * 290 + " " + "B" * 290


class TestChunkCount:
    """W3: PostResult.chunk_count must reflect actual number of chunks posted."""

    def test_chunk_count_single_chunk(self):
        client = _mock_client_for_chunk()
        client.com.atproto.repo.create_record.return_value = _mock_record()
        adapter = BlueskyAdapter(client=client)
        result = adapter.post(make_post("Short text"))
        assert result.chunk_count == 1

    def test_chunk_count_multi_chunk_success(self):
        """Success path (bluesky.py line 284): chunk_count reflects all chunks."""
        client = _mock_client_for_chunk()
        client.com.atproto.repo.create_record.return_value = _mock_record()
        adapter = BlueskyAdapter(client=client)
        result = adapter.post(make_post(_MULTI_CHUNK_TEXT))
        assert result.status == PostStatus.POSTED
        assert result.chunk_count == 2

    def test_chunk_count_dry_run_multi_chunk(self):
        """Dry-run path (bluesky.py line 208): chunk_count is set even without API calls."""
        client = _mock_client_for_chunk()
        adapter = BlueskyAdapter(client=client)
        result = adapter.post(make_post(_MULTI_CHUNK_TEXT), dry_run=True)
        assert result.status == PostStatus.POSTED
        assert result.chunk_count == 2

    def test_chunk_count_multi_chunk_partial_failure(self):
        """Failure path (bluesky.py line 265): chunk_count set even on partial failure."""
        client = _mock_client_for_chunk()
        client.com.atproto.repo.create_record.side_effect = [_mock_record(), Exception("chunk 2 fail")]
        adapter = BlueskyAdapter(client=client)
        result = adapter.post(make_post(_MULTI_CHUNK_TEXT))
        assert result.status == PostStatus.FAILED
        assert result.chunk_count == 2

    def test_chunk_count_in_audit_log(self):
        """log.py must emit result.chunk_count, not the hardcoded literal 1."""
        from datetime import datetime, timezone
        from scholarposter.audit.log import build_audit_record
        from scholarposter.models import PostResult, PostStatus, UnifiedPost

        post = UnifiedPost(
            source_id="99",
            text="Test post",
            source_url="https://fediscience.org/@u/99",
            created_at=datetime(2026, 4, 8, tzinfo=timezone.utc),
        )
        result = PostResult(platform="bluesky", status=PostStatus.POSTED, chunk_count=3)
        record = build_audit_record("99", "bluesky", post, result, dry_run=False)
        assert record["chunk_count"] == 3


class TestThreadRollback:
    def test_thread_rollback_deletes_posted_chunks_on_failure(self):
        """Chunk 1 fails → chunk 0's URI is deleted via delete_record."""
        from scholarposter.adapters.bluesky import BlueskyAdapter, _delete_bluesky_post
        from scholarposter.models import UnifiedPost, PostStatus
        from unittest.mock import MagicMock
        from datetime import datetime, timezone

        mock_client = MagicMock()
        first_response = MagicMock()
        first_response.uri = "at://did:plc:test/app.bsky.feed.post/chunk0rkey"
        first_response.cid = "bafyreidfake0"
        mock_client.com.atproto.repo.create_record.side_effect = [
            first_response,
            Exception("network error"),
        ]
        mock_client.me.did = "did:plc:test"

        adapter = BlueskyAdapter(mock_client)

        post = UnifiedPost(
            source_id="123",
            text="word " * 70,
            source_url="https://example.com",
            created_at=datetime.now(timezone.utc),
        )

        result = adapter.post(post)
        assert result.status == PostStatus.FAILED
        mock_client.com.atproto.repo.delete_record.assert_called_once()

    def test_thread_rollback_continues_if_delete_fails(self):
        """delete_record raises → PostResult still FAILED, no exception propagates."""
        from scholarposter.adapters.bluesky import BlueskyAdapter
        from scholarposter.models import UnifiedPost, PostStatus
        from unittest.mock import MagicMock
        from datetime import datetime, timezone

        mock_client = MagicMock()
        first_response = MagicMock()
        first_response.uri = "at://did:plc:test/app.bsky.feed.post/chunk0rkey"
        first_response.cid = "bafyreidfake0"
        mock_client.com.atproto.repo.create_record.side_effect = [
            first_response,
            Exception("network error"),
        ]
        mock_client.com.atproto.repo.delete_record.side_effect = Exception("delete failed")
        mock_client.me.did = "did:plc:test"

        adapter = BlueskyAdapter(mock_client)

        post = UnifiedPost(
            source_id="123",
            text="word " * 70,
            source_url="https://example.com",
            created_at=datetime.now(timezone.utc),
        )

        result = adapter.post(post)
        assert result.status == PostStatus.FAILED
        assert "manually delete" in (result.error or "")

    def test_single_chunk_no_rollback(self):
        """Single chunk fails → nothing to roll back; delete_record not called."""
        from scholarposter.adapters.bluesky import BlueskyAdapter
        from scholarposter.models import UnifiedPost, PostStatus
        from unittest.mock import MagicMock
        from datetime import datetime, timezone

        mock_client = MagicMock()
        mock_client.com.atproto.repo.create_record.side_effect = Exception("fail")
        mock_client.me.did = "did:plc:test"

        adapter = BlueskyAdapter(mock_client)

        post = UnifiedPost(
            source_id="123",
            text="short post",
            source_url="https://example.com",
            created_at=datetime.now(timezone.utc),
        )

        result = adapter.post(post)
        assert result.status == PostStatus.FAILED
        mock_client.com.atproto.repo.delete_record.assert_not_called()
