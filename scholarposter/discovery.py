"""Paper discovery via OpenAlex API."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from loguru import logger


def extract_interests(bibliography: list[dict]) -> dict:
    """Extract sharing interests from bibliography entries."""
    authors: dict[str, int] = {}
    dois: set[str] = set()
    for entry in bibliography:
        for author in entry.get("authors", []):
            if author:
                authors[author] = authors.get(author, 0) + 1
        if entry.get("doi"):
            dois.add(entry["doi"])
    top_authors = sorted(authors, key=lambda a: authors[a], reverse=True)[:10]
    return {"top_authors": top_authors, "shared_dois": dois}


def discover_papers(
    interests: dict,
    etiquette_email: str = "",
    max_results: int = 10,
    days: int = 30,
) -> list[dict]:
    """Query OpenAlex for recent papers by frequently-shared authors."""
    if not interests.get("top_authors"):
        return []

    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    mailto = etiquette_email or "scholarposter@example.com"
    results: list[dict] = []

    for author in interests["top_authors"][:5]:
        try:
            resp = httpx.get(
                "https://api.openalex.org/works",
                params={
                    "filter": (
                        f"authorships.author.display_name.search:{author},"
                        f"from_publication_date:{from_date}"
                    ),
                    "sort": "publication_date:desc",
                    "per_page": 5,
                    "mailto": mailto,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                for work in resp.json().get("results", []):
                    parsed = _parse_openalex_work(work)
                    if parsed:
                        results.append(parsed)
            elif resp.status_code == 429:
                logger.warning(f"OpenAlex rate limited for author '{author}'. Try again later.")
            else:
                logger.debug(f"OpenAlex returned {resp.status_code} for author '{author}'")
        except Exception as e:
            logger.debug(f"OpenAlex query failed for author '{author}': {e}")

    shared_dois = interests.get("shared_dois", set())
    seen: dict[str, None] = {}
    unique: list[dict] = []
    for paper in results:
        doi = paper.get("doi", "")
        if doi and doi not in shared_dois and doi not in seen:
            seen[doi] = None
            unique.append(paper)

    return unique[:max_results]


def _parse_openalex_work(work: dict) -> Optional[dict]:
    """Extract relevant fields from an OpenAlex work object."""
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    if not doi or not work.get("title"):
        return None
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in work.get("authorships", [])[:5]
    ]
    return {
        "doi": doi,
        "title": work.get("title", ""),
        "authors": [a for a in authors if a],
        "publication_date": work.get("publication_date", ""),
        "cited_by_count": work.get("cited_by_count", 0),
        "open_access_url": (work.get("open_access") or {}).get("oa_url"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
    }


_MAX_ABSTRACT_POS = 50000


def _reconstruct_abstract(inverted_index: Optional[dict]) -> str:
    """Reconstruct abstract from OpenAlex inverted index format.

    Clamps positions to [0, _MAX_ABSTRACT_POS] and ignores non-integer values.
    """
    if not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos <= _MAX_ABSTRACT_POS:
                word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)
