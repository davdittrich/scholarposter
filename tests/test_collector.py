"""Tests for scholarposter.collector"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from scholarposter.collector import MastodonCollector, strip_html, extract_urls, extract_hashtags, _clean_display_name

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
        post = collector._toot_to_unified_post(sample_toot)
        assert post.source_id == "113456789012345678"
        assert "Economics" in post.hashtags
        assert "GameTheory" in post.hashtags
        assert "https://doi.org/10.1007/s00355-023-01479-3" in post.urls

    def test_media_toot(self, sample_with_media):
        mock_client = MagicMock()
        collector = MastodonCollector(mock_client)
        post = collector._toot_to_unified_post(sample_with_media)
        assert len(post.media) == 1
        assert post.media[0].alt_text is not None

    def test_reblog_toot(self, sample_reblog):
        mock_client = MagicMock()
        collector = MastodonCollector(mock_client)
        post = collector._toot_to_unified_post(sample_reblog)
        assert post.is_reblog is True
        assert post.original_author is not None
        assert "Jane" in post.original_author or "researcher" in post.original_author.lower()
