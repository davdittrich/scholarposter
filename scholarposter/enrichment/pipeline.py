"""Enrichment pipeline orchestrator for scholarposter."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin

import httpx
from loguru import logger

from scholarposter.config import EnrichmentConfig
from scholarposter.enrichment.doi import detect_dois, lookup_doi
from scholarposter.enrichment.html import extract_og_tags, extract_body_text
from scholarposter.enrichment.media import download_media
from scholarposter.enrichment.pdf import extract_pdf_metadata, extract_pdf_text
from scholarposter.enrichment.summarizer import summarize
from scholarposter.enrichment.url import unshorten_url, detect_content_type, classify_link_type
from scholarposter.models import LinkEnrichment, LinkType, UnifiedPost
from scholarposter.state import StateManager

_MAX_HTML_BYTES = 5_000_000


class EnrichmentPipeline:
    def __init__(self, config: EnrichmentConfig, cache: StateManager):
        self._cfg = config
        self._cache = cache

    def enrich(self, post: UnifiedPost) -> UnifiedPost:
        """Run the enrichment pipeline on each URL in the post."""
        links: list[LinkEnrichment] = []
        for url in post.urls:
            link = self._enrich_url(url, post.text)
            if link:
                links.append(link)
        post = post.model_copy(update={"links": links})
        return post

    def _enrich_url(self, url: str, context_text: str) -> LinkEnrichment | None:
        """Enrich a single URL: unshorten, detect type, extract metadata."""
        link = LinkEnrichment(original_url=url)

        # Stage 1: URL unshortening
        try:
            if self._cfg.url_unshorten.enabled:
                resolved = unshorten_url(
                    url,
                    timeout=self._cfg.url_unshorten.timeout_seconds,
                    max_redirects=self._cfg.url_unshorten.max_redirects,
                )
                link = link.model_copy(update={"resolved_url": resolved})
            else:
                resolved = url
        except Exception as e:
            logger.warning(f"URL unshorten failed for {url}: {e}")
            return link

        # Stage 2: Detect content type
        try:
            content_type = detect_content_type(resolved) or ""
        except Exception as e:
            logger.warning(f"Content type detection failed for {resolved}: {e}")
            content_type = ""

        # FR-15a: classify link type using resolved URL
        link_type_str = classify_link_type(content_type, resolved)
        link = link.model_copy(update={"link_type": LinkType(link_type_str)})
        is_pdf = link.link_type == LinkType.FILE and (
            content_type == "application/pdf"
            or resolved.lower().endswith(".pdf")
        )

        # Stage 3: Extract content based on type
        if is_pdf:
            link = self._enrich_pdf(link, resolved)
        else:
            link = self._enrich_html(link, resolved)

        # Stage 3.5: DOI from URL pattern (shared across HTML/PDF paths)
        if not link.doi:
            doi = self._detect_doi_from_url(resolved)
            if doi:
                link = link.model_copy(update={"doi": doi})

        # Stage 4: DOI detection + lookup
        if self._cfg.crossref.enabled:
            link = self._enrich_doi(link, context_text)

        # Stage 5: Summarization
        if self._cfg.summarization.enabled and link.body_text:
            try:
                summary = summarize(
                    text=link.body_text,
                    backend=self._cfg.summarization.backend,
                    max_chars=self._cfg.summarization.max_chars,
                    prompt=self._cfg.summarization.prompt,
                    config=self._cfg.summarization,
                )
                if summary:
                    link = link.model_copy(update={"summary": summary})
            except Exception as e:
                logger.warning(f"Summarization failed: {e}")

        return link

    def _detect_doi_from_url(self, url: str) -> Optional[str]:
        """Detect DOI from a URL. Returns None on failure."""
        try:
            dois = detect_dois([url], "")
            return dois[0] if dois else None
        except Exception as e:
            logger.warning(f"DOI detect failed for {url}: {e}")
            return None

    def _enrich_html(self, link: LinkEnrichment, url: str) -> LinkEnrichment:
        """Extract OG tags and body text from an HTML URL."""
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                with client.stream("GET", url) as resp:
                    chunks: list[str] = []
                    total = 0
                    for chunk in resp.iter_text(4096):
                        total += len(chunk.encode("utf-8"))
                        if total > _MAX_HTML_BYTES:
                            logger.warning(f"HTML from {url} exceeds {_MAX_HTML_BYTES} bytes, truncating")
                            break
                        chunks.append(chunk)
            html = "".join(chunks)
        except Exception as e:
            logger.warning(f"HTML fetch failed for {url}: {e}")
            return link

        updates: dict = {}
        try:
            og = extract_og_tags(html)
            if og.get("title"):
                updates["title"] = og["title"]
            if og.get("description"):
                updates["description"] = og["description"]
            if og.get("image"):
                thumb_url = urljoin(url, og["image"])
                updates["thumbnail_url"] = thumb_url
                try:
                    thumb_bytes = download_media(thumb_url, timeout=10)
                    if thumb_bytes:
                        updates["thumbnail_bytes"] = thumb_bytes
                except Exception:
                    logger.debug(f"Thumbnail download failed for {thumb_url}")
        except Exception as e:
            logger.warning(f"OG extraction failed: {e}")

        try:
            body = extract_body_text(html)
            if body:
                updates["body_text"] = body
        except Exception as e:
            logger.warning(f"Body text extraction failed: {e}")

        return link.model_copy(update=updates)

    def _enrich_pdf(self, link: LinkEnrichment, url: str) -> LinkEnrichment:
        """Download PDF and extract metadata + text."""
        try:
            pdf_bytes = download_media(url, timeout=30)
            if not pdf_bytes:
                return link
        except Exception as e:
            logger.warning(f"PDF download failed for {url}: {e}")
            return link

        updates: dict = {}
        try:
            meta = extract_pdf_metadata(pdf_bytes)
            if meta.get("title"):
                updates["title"] = meta["title"]
            if meta.get("description"):
                updates["description"] = meta["description"]
        except Exception as e:
            logger.warning(f"PDF metadata extraction failed: {e}")

        try:
            text = extract_pdf_text(pdf_bytes, max_pages=20)
            if text:
                updates["body_text"] = text
        except Exception as e:
            logger.warning(f"PDF text extraction failed: {e}")

        return link.model_copy(update=updates)

    def _enrich_doi(self, link: LinkEnrichment, context_text: str) -> LinkEnrichment:
        """Detect DOI and look up Crossref metadata."""
        if link.doi:
            # DOI already detected in HTML/PDF stage — skip re-detection
            doi = link.doi
        else:
            urls_to_check = [link.resolved_url or link.original_url]
            try:
                dois = detect_dois(urls_to_check, context_text)
            except Exception as e:
                logger.warning(f"DOI detection failed: {e}")
                return link

            if not dois:
                return link
            doi = dois[0]
        cache_key = f"doi:{doi}"

        # Check cache first
        cached = self._cache.cache_get(cache_key)
        if cached:
            data = cached
        else:
            try:
                data = lookup_doi(
                    doi,
                    etiquette_email=self._cfg.crossref.etiquette_email,
                    timeout=self._cfg.crossref.timeout_seconds,
                )
            except Exception as e:
                logger.warning(f"DOI lookup failed for {doi}: {e}")
                return link

            if data:
                self._cache.cache_set(cache_key, data, ttl_days=self._cfg.crossref.cache_ttl_days)

        if not data:
            return link

        updates: dict = {"doi": doi}
        if data.get("title"):
            updates["crossref_title"] = data["title"]
            if not link.title:
                updates["title"] = data["title"]
        if data.get("abstract"):
            updates["crossref_abstract"] = data["abstract"]
            if not link.description:
                updates["description"] = data["abstract"]

        return link.model_copy(update=updates)
