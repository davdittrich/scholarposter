"""Tests for scholarposter.enrichment.pipeline"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from scholarposter.enrichment.pipeline import EnrichmentPipeline
from scholarposter.config import EnrichmentConfig, CrossrefConfig, SummarizationConfig, UrlUnshortenConfig, ProgressiveEnrichmentConfig
from scholarposter.models import UnifiedPost, LinkEnrichment, LinkType
from scholarposter.state import StateManager


def _mock_html_client(html_text: str):
    """Build a mock httpx.Client that streams the given HTML text."""
    mock_stream = MagicMock()
    mock_stream.status_code = 200
    mock_stream.iter_text.return_value = [html_text]
    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream_ctx.__exit__ = MagicMock(return_value=False)
    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_ctx
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


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
        html_text = "<html><head><title>Test Paper</title></head></html>"
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client(html_text)),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Test Paper",
                "description": "A great paper",
                "image": None,
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value="Full article text."),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            assert len(result.links) == 1
            assert result.links[0].title == "Test Paper"
            assert result.links[0].body_text == "Full article text."

    def test_doi_detected_triggers_lookup(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://doi.org/10.1000/test"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={"title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "DOI Paper Title",
                "abstract": "Abstract text here.",
                "authors": ["Jane Researcher"],
            }),
        ):
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
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://doi.org/10.1000/cached"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={"title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[doi]),
            patch("scholarposter.enrichment.pipeline.lookup_doi") as mock_lookup,
        ):
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
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "description": None, "image": "https://example.com/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"fake-image-bytes") as mock_dl,
        ):
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            assert result.links[0].thumbnail_bytes == b"fake-image-bytes"
            mock_dl.assert_called_once()

    def test_thumbnail_bytes_none_when_download_fails(self, config, state):
        """_enrich_html gracefully handles thumbnail download failure."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "description": None, "image": "https://example.com/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", side_effect=Exception("timeout")),
        ):
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
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "image": "/images/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=""),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"thumb"),
        ):
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
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "Paper", "image": "https://cdn.example.com/thumb.jpg",
            }),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=""),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"thumb"),
        ):
            post = make_post(urls=["https://example.com/article/123"])
            result = pipeline.enrich(post)
            assert result.links[0].thumbnail_url == "https://cdn.example.com/thumb.jpg"


class TestHtmlSizeGuard:
    def test_enrich_html_truncates_oversized_response(self, config, state):
        """HTML responses exceeding _MAX_HTML_BYTES are truncated without crashing."""
        from scholarposter.enrichment.pipeline import _MAX_HTML_BYTES
        pipeline = EnrichmentPipeline(config=config, cache=state)

        # Build a mock client that yields one chunk just over the limit
        large_chunk = "x" * (_MAX_HTML_BYTES + 1)
        mock_stream = MagicMock()
        mock_stream.status_code = 200
        mock_stream.iter_text.return_value = [large_chunk]
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_client = MagicMock()
        mock_client.stream.return_value = mock_stream_ctx
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client", return_value=mock_client),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/paper"])
            # Must not raise, must return a link
            result = pipeline.enrich(post)
            assert len(result.links) == 1


class TestLinkTypeClassification:
    """FR-15a: pipeline sets link_type based on content type."""

    def test_pdf_url_classified_as_file(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/pdf"),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"%PDF-fake"),
            patch("scholarposter.enrichment.pipeline.extract_pdf_metadata", return_value={"title": "T"}),
            patch("scholarposter.enrichment.pipeline.extract_pdf_text", return_value="text"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/paper.pdf"])
            result = pipeline.enrich(post)
            assert result.links[0].link_type == LinkType.FILE

    def test_html_url_classified_as_webpage(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/page"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={"title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/page"])
            result = pipeline.enrich(post)
            assert result.links[0].link_type == LinkType.WEBPAGE

    def test_unknown_content_type_defaults_to_webpage(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/thing"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/octet-stream"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={"title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/thing"])
            result = pipeline.enrich(post)
            assert result.links[0].link_type == LinkType.WEBPAGE

    def test_classification_uses_resolved_url(self, config, state):
        """FR-15a: classification must use resolved URL, not original."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value=None),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"%PDF-fake"),
            patch("scholarposter.enrichment.pipeline.extract_pdf_metadata", return_value={}),
            patch("scholarposter.enrichment.pipeline.extract_pdf_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://t.co/shortlink"])
            result = pipeline.enrich(post)
            assert result.links[0].link_type == LinkType.FILE


class TestCrossrefFieldSeparation:
    """FR-20b: Crossref data stored in dedicated fields."""

    def test_crossref_title_stored_separately(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://doi.org/10.1000/test"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html><head><title>OG Title</title></head></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "OG Title", "description": "OG Desc", "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "Crossref Title", "abstract": "Crossref Abstract"}),
        ):
            post = make_post(urls=["https://doi.org/10.1000/test"])
            result = pipeline.enrich(post)
            link = result.links[0]
            assert link.crossref_title == "Crossref Title"
            assert link.crossref_abstract == "Crossref Abstract"
            assert link.title == "OG Title"
            assert link.description == "OG Desc"

    def test_crossref_fills_title_when_og_missing(self, config, state):
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://doi.org/10.1000/test"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "Crossref Title", "abstract": "Crossref Abstract"}),
        ):
            post = make_post(urls=["https://doi.org/10.1000/test"])
            result = pipeline.enrich(post)
            link = result.links[0]
            assert link.crossref_title == "Crossref Title"
            assert link.title == "Crossref Title"
            assert link.crossref_abstract == "Crossref Abstract"
            assert link.description == "Crossref Abstract"


# ─── WU-2: Progressive Enrichment Gating + Audit Metadata ────────────────────

def _make_progressive_config(enabled: bool = True) -> EnrichmentConfig:
    """Build an EnrichmentConfig with progressive gating configured."""
    return EnrichmentConfig(
        crossref=CrossrefConfig(enabled=True, etiquette_email="test@example.com"),
        summarization=SummarizationConfig(enabled=False),
        url_unshorten=UrlUnshortenConfig(enabled=False),
        progressive=ProgressiveEnrichmentConfig(enabled=enabled),
    )


class TestStage25ProgressiveGating:
    """US-013: Stage 2.5 PDF pre-check — skip _enrich_pdf() when Crossref has sufficient abstract."""

    def test_skips_pdf_when_abstract_sufficient_and_progressive_enabled(self, state):
        """PDF download must NOT happen when crossref_abstract >= 20 chars and progressive.enabled."""
        cfg = _make_progressive_config(enabled=True)
        pipeline = EnrichmentPipeline(config=cfg, cache=state)
        long_abstract = "A" * 25  # >= 20 chars

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/pdf"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "PDF Title", "abstract": long_abstract,
            }),
            patch("scholarposter.enrichment.pipeline.download_media") as mock_dl,
        ):
            post = make_post(urls=["https://example.com/paper.pdf"])
            result = pipeline.enrich(post)
            mock_dl.assert_not_called()  # PDF was NOT downloaded
            link = result.links[0]
            assert link.crossref_abstract == long_abstract
            assert "stage_2.5_skip" in link.enrichment_path

    def test_does_not_skip_pdf_when_progressive_disabled(self, state):
        """When progressive.enabled=False, PDF is always downloaded regardless of abstract."""
        cfg = _make_progressive_config(enabled=False)
        pipeline = EnrichmentPipeline(config=cfg, cache=state)
        long_abstract = "A" * 25

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/pdf"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "PDF Title", "abstract": long_abstract,
            }),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"%PDF-fake") as mock_dl,
            patch("scholarposter.enrichment.pipeline.extract_pdf_metadata", return_value={}),
            patch("scholarposter.enrichment.pipeline.extract_pdf_text", return_value="pdf text"),
        ):
            post = make_post(urls=["https://example.com/paper.pdf"])
            result = pipeline.enrich(post)
            mock_dl.assert_called_once()  # PDF WAS downloaded
            assert "stage_2.5_skip" not in result.links[0].enrichment_path

    def test_does_not_skip_pdf_when_abstract_too_short(self, state):
        """When crossref_abstract < 20 chars, PDF is downloaded even if progressive enabled."""
        cfg = _make_progressive_config(enabled=True)
        pipeline = EnrichmentPipeline(config=cfg, cache=state)
        short_abstract = "Too short"  # < 20 chars

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/pdf"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1000/test"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "PDF Title", "abstract": short_abstract,
            }),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"%PDF-fake") as mock_dl,
            patch("scholarposter.enrichment.pipeline.extract_pdf_metadata", return_value={}),
            patch("scholarposter.enrichment.pipeline.extract_pdf_text", return_value="pdf text"),
        ):
            post = make_post(urls=["https://example.com/paper.pdf"])
            result = pipeline.enrich(post)
            mock_dl.assert_called_once()  # PDF WAS downloaded
            assert "stage_2.5_skip" not in result.links[0].enrichment_path

    def test_does_not_skip_pdf_when_no_doi_found(self, state):
        """When DOI cannot be found, abstract is not available; PDF must be downloaded."""
        cfg = _make_progressive_config(enabled=True)
        pipeline = EnrichmentPipeline(config=cfg, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper.pdf"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="application/pdf"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.download_media", return_value=b"%PDF-fake") as mock_dl,
            patch("scholarposter.enrichment.pipeline.extract_pdf_metadata", return_value={}),
            patch("scholarposter.enrichment.pipeline.extract_pdf_text", return_value="pdf text"),
        ):
            post = make_post(urls=["https://example.com/paper.pdf"])
            result = pipeline.enrich(post)
            mock_dl.assert_called_once()

    def test_stage_25_only_applies_to_pdf_not_html(self, state):
        """Stage 2.5 must not be triggered for HTML URLs."""
        cfg = _make_progressive_config(enabled=True)
        pipeline = EnrichmentPipeline(config=cfg, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/paper"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": "HTML Paper", "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value="html body"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
            assert "stage_2.5_skip" not in result.links[0].enrichment_path


class TestStage5GuardAmendment:
    """FR-76: Stage 5 guard uses body_text OR crossref_abstract."""

    def test_summarizes_when_only_crossref_abstract_available(self, state):
        """Summarization must run when body_text is None but crossref_abstract is set."""
        cfg = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=True, etiquette_email="test@example.com"),
            summarization=SummarizationConfig(enabled=True, backend="extractive"),
            url_unshorten=UrlUnshortenConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=cfg, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/p"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.9999/x"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "Title", "abstract": "A long abstract that summarizer can use."}),
            patch("scholarposter.enrichment.pipeline.summarize",
                  return_value=("Summary from abstract.", "extractive", None)) as mock_summ,
        ):
            post = make_post(urls=["https://example.com/p"])
            result = pipeline.enrich(post)
            mock_summ.assert_called_once()
            # text_input to summarize() should be crossref_abstract, not body_text
            call_kwargs = mock_summ.call_args
            assert call_kwargs[1]["text"] != ""
            assert "stage_5_summarize" in result.links[0].enrichment_path

    def test_does_not_summarize_when_both_body_text_and_abstract_absent(self, state):
        """No summarization when both body_text and crossref_abstract are None."""
        cfg = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=True),
            summarization=SummarizationConfig(enabled=True),
            url_unshorten=UrlUnshortenConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=cfg, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/p"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.summarize") as mock_summ,
        ):
            post = make_post(urls=["https://example.com/p"])
            pipeline.enrich(post)
            mock_summ.assert_not_called()


class TestEnrichmentMetadataFields:
    """enrichment_path and llm_backend_used are populated by the pipeline."""

    def test_enrichment_path_records_crossref_stage(self, state):
        """'stage_4_crossref' must appear in enrichment_path when Crossref is queried."""
        cfg = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=True, etiquette_email="test@example.com"),
            summarization=SummarizationConfig(enabled=False),
            url_unshorten=UrlUnshortenConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=cfg, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/p"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value=None),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=["10.1/x"]),
            patch("scholarposter.enrichment.pipeline.lookup_doi", return_value={
                "title": "T", "abstract": "Abstract text."}),
        ):
            post = make_post(urls=["https://example.com/p"])
            result = pipeline.enrich(post)
            assert "stage_4_crossref" in result.links[0].enrichment_path

    def test_llm_usage_populated_after_summarization(self, state):
        """link.llm_tokens/cost must be set after Stage 5 if usage info available."""
        from scholarposter.gemini_client import GeminiUsage
        cfg = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=False),
            summarization=SummarizationConfig(enabled=True, backend="gemini"),
            url_unshorten=UrlUnshortenConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=cfg, cache=state)
        usage = GeminiUsage(tokens_used=123, cost_usd=0.00045, cost_currency="USD",
                           is_estimated=False, cost_is_estimated=True)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/p"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value="body"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.summarize",
                  return_value=("A summary.", "gemini", usage)),
        ):
            post = make_post(urls=["https://example.com/p"])
            result = pipeline.enrich(post)
            link = result.links[0]
            assert link.llm_backend_used == "gemini"
            assert link.llm_tokens == 123
            assert link.llm_cost_usd == 0.00045
            assert link.llm_cost_currency == "USD"
            assert link.llm_usage_is_estimated is False
            assert link.llm_cost_is_estimated is True

    def test_llm_backend_used_populated_after_summarization(self, state):
        """link.llm_backend_used must be set to the backend name after Stage 5."""
        cfg = EnrichmentConfig(
            crossref=CrossrefConfig(enabled=False),
            summarization=SummarizationConfig(enabled=True, backend="extractive"),
            url_unshorten=UrlUnshortenConfig(enabled=False),
        )
        pipeline = EnrichmentPipeline(config=cfg, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/p"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value="Full body text here."),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
            patch("scholarposter.enrichment.pipeline.summarize",
                  return_value=("A summary.", "extractive", None)),
        ):
            post = make_post(urls=["https://example.com/p"])
            result = pipeline.enrich(post)
            link = result.links[0]
            assert link.llm_backend_used == "extractive"

    def test_llm_backend_used_none_when_summarization_disabled(self, config, state):
        """link.llm_backend_used stays None when summarization is disabled."""
        pipeline = EnrichmentPipeline(config=config, cache=state)

        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", return_value="https://example.com/p"),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=_mock_html_client("<html></html>")),
            patch("scholarposter.enrichment.pipeline.extract_og_tags", return_value={
                "title": None, "description": None, "image": None}),
            patch("scholarposter.enrichment.pipeline.extract_body_text", return_value="body"),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/p"])
            result = pipeline.enrich(post)
            assert result.links[0].llm_backend_used is None

    def test_enrichment_path_default_empty_on_new_link(self):
        """LinkEnrichment.enrichment_path is [] by default."""
        link = LinkEnrichment(original_url="https://example.com")
        assert link.enrichment_path == []


class TestHttpErrorGuard:
    """Discriminating: HTTP 4xx/5xx response bodies must not leak into description or summary."""

    def _mock_error_client(self, status_code: int, body: str = "403 Forbidden"):
        mock_stream = MagicMock()
        mock_stream.status_code = status_code
        mock_stream.iter_text.return_value = [body]
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_client = MagicMock()
        mock_client.stream.return_value = mock_stream_ctx
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    def test_403_response_body_not_stored_as_description(self, config, state):
        """Discriminating: OLD stores 403 body as og_description; NEW skips enrichment on HTTP error."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=self._mock_error_client(403, "<p>403 Forbidden</p>")),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://restricted.example.com/paper"])
            result = pipeline.enrich(post)
        link = result.links[0]
        assert link.description is None or "403" not in (link.description or "")
        assert link.body_text is None or "403" not in (link.body_text or "")

    def test_500_response_body_not_stored(self, config, state):
        """HTTP 500 error body must not be used as page content."""
        pipeline = EnrichmentPipeline(config=config, cache=state)
        with (
            patch("scholarposter.enrichment.pipeline.unshorten_url", side_effect=lambda u, **kw: u),
            patch("scholarposter.enrichment.pipeline.detect_content_type", return_value="text/html"),
            patch("scholarposter.enrichment.pipeline.httpx.Client",
                  return_value=self._mock_error_client(500, "<p>Internal Server Error</p>")),
            patch("scholarposter.enrichment.pipeline.detect_dois", return_value=[]),
        ):
            post = make_post(urls=["https://example.com/paper"])
            result = pipeline.enrich(post)
        link = result.links[0]
        assert link.description is None or "Internal Server Error" not in (link.description or "")
        assert link.body_text is None or "Internal Server Error" not in (link.body_text or "")
