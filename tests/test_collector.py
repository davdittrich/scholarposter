"""Tests for scholarposter.collector"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from scholarposter.collector import MastodonCollector, strip_html, extract_urls, extract_hashtags, _clean_display_name, _mime_from_attachment

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_toot():
    return json.loads((FIXTURES / "sample_toot.json").read_text())


@pytest.fixture
def sample_reblog():
    return json.loads((FIXTURES / "sample_toot_reblog.json").read_text())


@pytest.fixture
def sample_with_media():
    return json.loads((FIXTURES / "sample_toot_with_media.json").read_text())


class TestStripHtml:
    def test_p_tags_become_double_newline(self):
        html = "<p>First para</p><p>Second para</p>"
        result = strip_html(html)
        assert "First para" in result
        assert "Second para" in result
        assert "\n\n" in result

    def test_br_becomes_newline(self):
        html = "<p>Line one<br/>Line two</p>"
        result = strip_html(html)
        assert "Line one" in result
        assert "Line two" in result

    def test_tags_removed_text_preserved(self):
        html = '<p>Check <a href="https://example.com">this link</a> out</p>'
        result = strip_html(html)
        assert "Check" in result
        assert "this link" in result
        assert "out" in result
        assert "<a" not in result

    def test_span_removed(self):
        html = "<p><span>Hello</span> world</p>"
        result = strip_html(html)
        assert "Hello" in result
        assert "<span>" not in result

    def test_empty_string(self):
        assert strip_html("") == ""


class TestExtractUrls:
    def test_finds_http_url(self):
        text = "Check out https://example.com/paper for details"
        urls = extract_urls(text)
        assert "https://example.com/paper" in urls

    def test_finds_multiple_urls(self):
        text = "See https://doi.org/10.1000/test and https://example.com"
        urls = extract_urls(text)
        assert len(urls) == 2

    def test_no_urls_returns_empty(self):
        text = "No links here, just text."
        assert extract_urls(text) == []

    def test_deduplicates(self):
        text = "https://example.com and https://example.com again"
        urls = extract_urls(text)
        assert len(urls) == 1


class TestExtractHashtags:
    def test_extracts_tag_names(self):
        tags = [
            {"name": "Economics", "url": "https://fediscience.org/tags/Economics"},
            {"name": "GameTheory", "url": "https://fediscience.org/tags/GameTheory"},
        ]
        result = extract_hashtags(tags)
        assert "Economics" in result
        assert "GameTheory" in result

    def test_empty_list(self):
        assert extract_hashtags([]) == []


class TestCleanDisplayName:
    def test_removes_emoji_shortcodes(self):
        assert _clean_display_name("Jane :verified: Researcher") == "Jane  Researcher"

    def test_no_shortcodes_unchanged(self):
        assert _clean_display_name("Jane Researcher") == "Jane Researcher"

    def test_multiple_shortcodes(self):
        result = _clean_display_name("Dr :scholar: Smith :phd:")
        assert ":scholar:" not in result
        assert ":phd:" not in result
        assert "Dr" in result
        assert "Smith" in result


class TestFetchOldestUnprocessed:
    def test_returns_none_on_empty_timeline(self):
        mock_client = MagicMock()
        mock_client.account_statuses.return_value = []
        collector = MastodonCollector(mock_client)
        result = collector.fetch_oldest_unprocessed(user_id="123", since_id=None)
        assert result is None

    def test_returns_oldest_toot(self, sample_toot):
        newer = dict(sample_toot, id="999999999999999999")
        mock_client = MagicMock()
        mock_client.account_statuses.return_value = [newer, sample_toot]
        collector = MastodonCollector(mock_client)
        result = collector.fetch_oldest_unprocessed(user_id="123", since_id=None)
        assert result is not None
        assert result.source_id == sample_toot["id"]

    def test_reblog_unwrapped_with_attribution(self, sample_reblog):
        mock_client = MagicMock()
        mock_client.account_statuses.return_value = [sample_reblog]
        collector = MastodonCollector(mock_client)
        result = collector.fetch_oldest_unprocessed(user_id="123", since_id=None)
        assert result is not None
        assert result.is_reblog is True
        assert result.original_author is not None
        assert "via" in result.text.lower() or result.original_author


class TestTootToUnifiedPost:
    def test_basic_toot(self, sample_toot):
        mock_client = MagicMock()
        collector = MastodonCollector(mock_client)
        post = collector.toot_to_unified_post(sample_toot)
        assert post.source_id == "113456789012345678"
        assert "Economics" in post.hashtags
        assert "GameTheory" in post.hashtags
        assert "https://doi.org/10.1007/s00355-023-01479-3" in post.urls

    def test_media_toot(self, sample_with_media):
        mock_client = MagicMock()
        collector = MastodonCollector(mock_client)
        post = collector.toot_to_unified_post(sample_with_media)
        assert len(post.media) == 1
        assert post.media[0].alt_text is not None

    def test_reblog_toot(self, sample_reblog):
        mock_client = MagicMock()
        collector = MastodonCollector(mock_client)
        post = collector.toot_to_unified_post(sample_reblog)
        assert post.is_reblog is True
        assert post.original_author is not None
        assert "Jane" in post.original_author or "researcher" in post.original_author.lower()


class TestTootToUnifiedPostReplyFields:
    """WU-2: reply/self-thread/visibility/cw/mention mapping."""

    def _make_toot(self, **overrides) -> dict:
        base = {
            "id": "1",
            "created_at": "2024-01-15T10:30:00.000Z",
            "content": "<p>Test</p>",
            "url": "https://example.com/1",
            "sensitive": False,
            "spoiler_text": "",
            "visibility": "public",
            "tags": [],
            "mentions": [],
            "media_attachments": [],
            "reblog": None,
            "poll": None,
            "in_reply_to_id": None,
            "in_reply_to_account_id": None,
            "account": {"id": "789", "username": "user", "acct": "user", "display_name": "User", "url": "https://example.com/@user"},
        }
        base.update(overrides)
        return base

    def test_col_reply_other(self):
        toot = self._make_toot(in_reply_to_id="123", in_reply_to_account_id="456")
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.is_reply is True
        assert post.is_self_thread_reply is False

    def test_col_reply_self(self):
        toot = self._make_toot(in_reply_to_id="123", in_reply_to_account_id="789")
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.is_reply is False
        assert post.is_self_thread_reply is True

    def test_col_not_reply(self):
        toot = self._make_toot(in_reply_to_id=None, in_reply_to_account_id=None)
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.is_reply is False
        assert post.is_self_thread_reply is False

    def test_col_reply_null_account_id(self):
        # Cross-instance: in_reply_to_id set but account unresolvable → treat as other-account reply
        toot = self._make_toot(in_reply_to_id="123", in_reply_to_account_id=None)
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.is_reply is True
        assert post.is_self_thread_reply is False

    def test_col_reblog_reply(self):
        # Boost of a reply: outer toot in_reply_to_id=None → not classified as reply
        inner = self._make_toot(in_reply_to_id="999", in_reply_to_account_id="456")
        inner["id"] = "inner1"
        toot = self._make_toot(reblog=inner, in_reply_to_id=None, in_reply_to_account_id=None)
        toot["id"] = "outer1"
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.is_reply is False
        assert post.is_self_thread_reply is False

    def test_col_visibility_private(self):
        toot = self._make_toot(visibility="private")
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.visibility == "private"

    def test_col_visibility_unlisted(self):
        # Non-default value — confirms the field is actually read from the toot dict
        toot = self._make_toot(visibility="unlisted")
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.visibility == "unlisted"

    def test_col_visibility_reblog_uses_inner(self):
        # For boosts, visibility reflects the ORIGINAL content (source = inner).
        # A boost of a "private" toot must be caught by the "private" filter.
        inner = self._make_toot(visibility="private")
        inner["id"] = "inner1"
        toot = self._make_toot(reblog=inner, visibility="public")
        toot["id"] = "outer1"
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.visibility == "private"

    def test_col_cw_set(self):
        toot = self._make_toot(spoiler_text="Content warning text")
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.has_content_warning is True

    def test_col_cw_empty(self):
        toot = self._make_toot(spoiler_text="")
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.has_content_warning is False

    def test_col_mention_present(self):
        toot = self._make_toot(mentions=[{"id": "1", "username": "other", "acct": "other", "url": "https://example.com/@other"}])
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.has_mention is True

    def test_col_mention_empty(self):
        toot = self._make_toot(mentions=[])
        collector = MastodonCollector(MagicMock())
        post = collector.toot_to_unified_post(toot)
        assert post.has_mention is False

    def test_api_no_exclude_replies(self):
        mock_client = MagicMock()
        mock_client.account_statuses.return_value = []
        collector = MastodonCollector(mock_client)
        collector.fetch_oldest_unprocessed(user_id="123", since_id=None)
        call_kwargs = mock_client.account_statuses.call_args[1]
        assert "exclude_replies" not in call_kwargs


class TestMimeFromAttachment:
    def test_png_url_returns_image_png(self):
        att = {"type": "image", "url": "https://example.com/photo.png"}
        assert _mime_from_attachment(att) == "image/png"

    def test_gif_url_returns_image_gif(self):
        att = {"type": "image", "url": "https://example.com/anim.gif"}
        assert _mime_from_attachment(att) == "image/gif"

    def test_no_url_falls_back_to_jpeg(self):
        att = {"type": "image"}
        assert _mime_from_attachment(att) == "image/jpeg"

    def test_unknown_extension_falls_back_to_type_mapping(self):
        att = {"type": "image", "url": "https://example.com/photo.unknownext"}
        assert _mime_from_attachment(att) == "image/jpeg"

    def test_remote_url_used_for_mime_detection(self):
        att = {"type": "image", "remote_url": "https://cdn.example.com/pic.png", "url": ""}
        assert _mime_from_attachment(att) == "image/png"

    def test_video_mp4_url(self):
        att = {"type": "video", "url": "https://example.com/clip.mp4"}
        assert _mime_from_attachment(att) == "video/mp4"

    def test_image_type_mismatch_falls_back(self):
        # A video URL for an "image" type attachment should not return video/mp4
        att = {"type": "image", "url": "https://example.com/clip.mp4"}
        assert _mime_from_attachment(att) == "image/jpeg"

    def test_unknown_type_no_url_returns_octet_stream(self):
        att = {"type": "unknown_type"}
        assert _mime_from_attachment(att) == "application/octet-stream"
