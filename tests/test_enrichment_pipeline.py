"""Tests for scholarposter.enrichment.pipeline"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from scholarposter.enrichment.pipeline import EnrichmentPipeline
from scholarposter.config import EnrichmentConfig, CrossrefConfig, SummarizationConfig, UrlUnshortenConfig
from scholarposter.models import UnifiedPost, LinkEnrichment
from scholarposter.state import StateManager


def make_post(urls=None, text="A post", hashtags=None) -> UnifiedPost:
    return UnifiedPost(
        source_id="1",
        text=text,
        source_url="https://fediscience.org/@user/1",
        created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        urls=urls or [],
        hashtags=hashtags or [],
    )


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path


@pytest.fixture
def state(state_dir):
    return StateManager(state_dir=state_dir)


@pytest.fixture
def config():
    return EnrichmentConfig(
        crossref=CrossrefConfig(enabled=True, etiquette_email="test@example.com"),
        summarization=SummarizationConfig(enabled=False),
        url_unshorten=UrlUnshortenConfig(enabled=True),
    )


class TestEnrichmentPipeline:
    def test_no_urls_returns_empty_links(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        post = make_post(urls=[])
        result = pipeline.enrich(post)
        assert result.links == []

    def test_url_produces_link_enrichment(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        mock_response = MagicMock()
        mock_response.text = "<html><head><title>Test Paper</title></head></html>"
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx") as mock_httpx,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Test Paper",
                "description": "A great paper",
                "image": None,
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value="Full article text."),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            mock_httpx.get.return_value = mock_response
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            assert len(result.links) == 1
            assert result.links[0].title == "Test Paper"
            assert result.links[0].body_text == "Full article text."

    def test_doi_detected_triggers_lookup(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://doi.org/10.1000/test"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx") as mock_httpx,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={"title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "DOI Paper Title",
                "abstract": "Abstract text here.",
                "authors": ["Jane Researcher"],
            }),
        ):
            mock_httpx.get.return_value = mock_response
            post = make_post(urls=["https://doi.org/10.1000/test"])
            result = pipeline.enrich(post)
            assert len(result.links) == 1
            assert result.links[0].doi == "10.1000/test"
            assert result.links[0].title == "DOI Paper Title"

    def test_stage_failure_does_not_abort(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=Exception("network error")),
        ):
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            # Should not raise; post still returned
            assert isinstance(result, UnifiedPost)

    def test_cached_doi_skips_api_lookup(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        doi = "10.1000/cached"
        state.cache_set(f"doi:{doi}", {"title": "Cached Title", "abstract": "Cached abstract.", "authors": []}, ttl_days=7)
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://doi.org/10.1000/cached"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx") as mock_httpx,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={"title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[doi]),
            patch("scholarposter.enrichment.pipeline.lookup_doi") as mock_lookup,
        ):
            mock_httpx.get.return_value = mock_response
            post = make_post(urls=["https://doi.org/10.1000/cached"])
            result = pipeline.enrich(post)
            mock_lookup.assert_not_called()
            assert result.links[0].title == "Cached Title"

    def test_pdf_url_uses_pdf_extractor(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/pdf"),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"%PDF-fake"),
            patch("scholarposter.enrichment.pipeline.extract_pdf_metadata", return_value={"title": "PDF Title", "description": "PDF Desc"}),
            patch("scholarposter.enrichment.pipeline.extract_pdf_text", return_value="PDF body text here"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/paper.pdf"])
            result = pipeline.enrich(post)
            assert result.links[0].title == "PDF Title"
            assert result.links[0].body_text == "PDF body text here"


class TestThumbnailDownload:
    def test_thumbnail_bytes_set_when_og_image_present(self, config, state):
        """_enrich_html must download thumbnail bytes when OG image is present."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx") as mock_httpx,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "description": None, "image": "https://example.com/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"fake-image-bytes") as mock_dl,
        ):
            mock_httpx.get.return_value = mock_response
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            assert result.links[0].thumbnail_bytes == b"fake-image-bytes"
            mock_dl.assert_called_once()

    def test_thumbnail_bytes_none_when_download_fails(self, config, state):
        """_enrich_html gracefully handles thumbnail download failure."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx") as mock_httpx,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "description": None, "image": "https://example.com/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", side_effect=Exception("timeout")),
        ):
            mock_httpx.get.return_value = mock_response
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            assert result.links[0].thumbnail_bytes is None
            assert result.links[0].thumbnail_url == "https://example.com/thumb.jpg"


class TestDoiDedup:
    def test_enrich_doi_skips_detection_when_doi_already_set(self, config, state):
        """_enrich_doi skips detect_dois when link.doi is already set by HTML/PDF stage."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        link = LinkEnrichment(original_url="https://doi.org/10.1234/test", doi="10.1234/test")
        with (
            patch("scholarposter.enrichment.pipeline.detect_dois") as mock_detect,
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "Test Title", "abstract": "Abstract", "authors": [],
            }),
        ):
            result = pipeline._enrich_doi(link, "some context")
            mock_detect.assert_not_called()
            assert result.title == "Test Title"

    def test_enrich_doi_calls_detection_when_doi_not_set(self, config, state):
        """_enrich_doi calls detect_dois when link.doi is None."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        link = LinkEnrichment(original_url="https://example.com/paper", resolved_url="https://example.com/paper")
        with (
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1234/found"]) as mock_detect,
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "Found Title", "abstract": "Abstract", "authors": [],
            }),
        ):
            result = pipeline._enrich_doi(link, "contains 10.1234/found")
            mock_detect.assert_called_once()
            assert result.doi == "10.1234/found"


class TestUrlJoinResolution:
    def test_relative_og_image_resolved(self, state):
        """FR-21: OG image with relative path is resolved against page URL."""
        config = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=False),
            summarization=SummarizationConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.get") as mock_get,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "image": "/images/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=""),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"thumb"),
        ):
            mock_get.return_value = MagicMock(text="<html></html>")
            post = make_post(urls=["https://example.com/article/123"])
            result = pipeline.enrich(post)
            assert result.links[0].thumbnail_url == "https://example.com/images/thumb.jpg"

    def test_absolute_og_image_unchanged(self, state):
        """urljoin is a no-op for absolute URLs — no regression."""
        config = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=False),
            summarization=SummarizationConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.get") as mock_get,
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "image": "https://cdn.example.com/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=""),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"thumb"),
        ):
            mock_get.return_value = MagicMock(text="<html></html>")
            post = make_post(urls=["https://example.com/article/123"])
            result = pipeline.enrich(post)
            assert result.links[0].thumbnail_url == "https://cdn.example.com/thumb.jpg"
