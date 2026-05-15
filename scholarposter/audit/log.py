"""Audit record builder for scholarposter (FR-90)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from scholarposter.models import PostResult, UnifiedPost


def build_audit_record(
    toot_id: str,
    platform: str,
    post: UnifiedPost,
    result: PostResult,
    dry_run: bool,
) -> dict:
    """Build a FR-90 audit record from post and result data.

    Always returns a dict with exactly the 17 required fields.  Engagement
    fields (bluesky_likes, bluesky_reposts, engagement_synced_at) are null at
    write time — populated later by `scholarposter sync-engagement` (WU-4).
    """
    # Status: dry_run overrides the adapter's result
    if dry_run:
        status = "dry_run"
    else:
        status = result.status.value  # "posted" | "failed" | "skipped"

    # Extract first link's metadata (most posts have 0 or 1 link)
    first_link = post.links[0] if post.links else None

    enrichment_path: list[str] = first_link.enrichment_path if first_link else []
    pdf_stage_skipped: bool = "stage_2.5_skip" in enrichment_path
    llm_backend_used: Optional[str] = first_link.llm_backend_used if first_link else None
    llm_tokens: Optional[int] = first_link.llm_tokens if first_link else None
    llm_cost_usd: Optional[float] = first_link.llm_cost_usd if first_link else None
    llm_cost_currency: Optional[str] = first_link.llm_cost_currency if first_link else None
    llm_usage_is_estimated: bool = first_link.llm_usage_is_estimated if first_link else False
    llm_cost_is_estimated: bool = first_link.llm_cost_is_estimated if first_link else False
    doi: Optional[str] = first_link.doi if first_link else None
    link_type: Optional[str] = first_link.link_type.value if first_link else None

    crossref_abstract = first_link.crossref_abstract if first_link else None
    abstract_chars: Optional[int] = len(crossref_abstract) if crossref_abstract else None

    summary = first_link.summary if first_link else None
    summary_chars: Optional[int] = len(summary) if summary else None

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toot_id": toot_id,
        "platform": platform,
        "status": status,
        "enrichment_path": enrichment_path,
        "pdf_stage_skipped": pdf_stage_skipped,
        "llm_backend_used": llm_backend_used,
        "llm_tokens": llm_tokens,
        "llm_cost_usd": llm_cost_usd,
        "llm_cost_currency": llm_cost_currency,
        "llm_usage_is_estimated": llm_usage_is_estimated,
        "llm_cost_is_estimated": llm_cost_is_estimated,
        "abstract_chars": abstract_chars,
        "summary_chars": summary_chars,
        "doi": doi,
        "link_type": link_type,
        "post_url": result.post_url,
        "bluesky_likes": None,
        "bluesky_reposts": None,
        "engagement_synced_at": None,
        "hashtags": list(post.hashtags),
        "chunk_count": result.chunk_count,
    }
