"""tldr-scholar: Standalone academic text summarizer.

Public API:
    summarize(text=...) or summarize(request=SummaryRequest(...))
    summarize_file(path=...)
    summarize_url(url=...)
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from loguru import logger

from tldr_scholar.backends import run_with_fallback
from tldr_scholar.hashtags import (
    build_hashtag_instruction,
    generate_hashtags_tfidf,
    parse_hashtags_from_response,
)
from tldr_scholar.ingest import ingest
from tldr_scholar.models import SummaryMetadata, SummaryRequest, SummaryResult

__all__ = [
    "summarize", "summarize_file", "summarize_url",
    "SummaryRequest", "SummaryResult", "SummaryMetadata",
]


def _strip_url_credentials(url: str) -> str:
    """Remove userinfo (user:pass@) from URL for safe metadata storage."""
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        hostname = parsed.hostname or ""
        netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
        return parsed._replace(netloc=netloc).geturl()
    return url


def summarize(
    text: str | None = None,
    *,
    request: SummaryRequest | None = None,
    max_chars: int = 500,
    focus: str = "main findings and novel insights",
    hashtags: int = 0,
    backend: str = "auto",
    backend_config: dict[str, Any] | None = None,
) -> SummaryResult:
    """Summarize text. Accepts either text= or request=, not both.

    Returns SummaryResult with .text, .hashtags, and .metadata.
    """
    if text is not None and request is not None:
        raise ValueError("Pass either text= or request=, not both")
    if text is None and request is None:
        raise ValueError("text or request required")

    if request is not None:
        req = request
    else:
        req = SummaryRequest(
            text=text,
            max_chars=max_chars,
            focus=focus,
            hashtags=hashtags,
            backend=backend,
            backend_config=backend_config or {},
        )

    hashtag_instruction = build_hashtag_instruction(req.hashtags)
    response, backend_used = run_with_fallback(
        text=req.text,
        max_chars=req.max_chars,
        focus=req.focus,
        hashtag_instruction=hashtag_instruction,
        backend=req.backend,
        config=req.backend_config if req.backend_config else None,
    )

    if not response:
        return SummaryResult(
            text="",
            hashtags=[],
            metadata=SummaryMetadata(
                backend_used=backend_used,
                max_chars=req.max_chars,
                focus=req.focus,
            ),
        )

    # Hashtag dispatch: extractive uses TF-IDF, LLM backends parse from response
    if backend_used == "extractive" and req.hashtags > 0:
        summary_text = response
        hashtag_list = generate_hashtags_tfidf(req.text, req.hashtags)
    elif req.hashtags > 0:
        summary_text, hashtag_list = parse_hashtags_from_response(response)
        if len(hashtag_list) < req.hashtags:
            logger.debug(f"LLM returned {len(hashtag_list)} hashtags, requested {req.hashtags}")
    else:
        summary_text = response
        hashtag_list = []

    return SummaryResult(
        text=summary_text.strip(),
        hashtags=hashtag_list,
        metadata=SummaryMetadata(
            backend_used=backend_used,
            max_chars=req.max_chars,
            focus=req.focus,
            char_count=len(summary_text.strip()),
        ),
    )


def summarize_file(path: str, **kwargs) -> SummaryResult:
    """Summarize a local file (PDF, Markdown, or plain text)."""
    text, input_type = ingest(path)
    result = summarize(text=text, **kwargs)
    result.metadata.source = path
    result.metadata.input_type = input_type
    return result


def summarize_url(url: str, **kwargs) -> SummaryResult:
    """Summarize a web page or PDF URL."""
    text, input_type = ingest(url)
    result = summarize(text=text, **kwargs)
    result.metadata.source = _strip_url_credentials(url)
    result.metadata.input_type = input_type
    return result
