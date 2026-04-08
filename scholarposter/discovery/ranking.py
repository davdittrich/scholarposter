"""Citation graph ranking — composite score and top-N selection (US-014)."""
from __future__ import annotations

import math

from scholarposter.config import DiscoveryRankingConfig
from scholarposter.discovery import CandidatePaper


def score(paper: CandidatePaper, config: DiscoveryRankingConfig) -> float:
    """Composite score: citation velocity * OA weight * recency decay.

    velocity = cited_by_count / max(1, age_years)
    oa       = config.oa_weight if paper.is_oa else 1.0
    recency  = exp(-ln(2) * age_years / recency_half_life_years)
    """
    velocity = paper.cited_by_count / max(1.0, paper.age_years)
    oa = config.oa_weight if paper.is_oa else 1.0
    recency = math.exp(-math.log(2) * paper.age_years / config.recency_half_life_years)
    return velocity * oa * recency


def rank(
    papers: list[CandidatePaper],
    config: DiscoveryRankingConfig,
    limit: int,
) -> list[CandidatePaper]:
    """Deduplicate by DOI (keeping the highest-scoring copy), sort descending, return top N."""
    scored = [(p, score(p, config)) for p in papers]
    best: dict[str, tuple[CandidatePaper, float]] = {}
    for p, s in scored:
        if p.doi not in best or s > best[p.doi][1]:
            best[p.doi] = (p, s)
    return [p for p, _ in sorted(best.values(), key=lambda x: x[1], reverse=True)][:limit]
