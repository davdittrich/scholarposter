"""Discovery package — citation graph traversal (US-014).

Public surface: CandidatePaper dataclass.
Graph functions live in .graph; cache in .cache.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CandidatePaper:
    """A paper candidate returned by graph traversal."""

    doi: str
    openalex_id: str
    title: str
    year: Optional[int]
    cited_by_count: int
    is_oa: bool
    source: str   # "openalex"
    mode: str     # "cited-by" | "cites"
    age_years: float
