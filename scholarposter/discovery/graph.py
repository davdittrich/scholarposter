"""Citation graph traversal via OpenAlex REST API (US-014)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from loguru import logger

from scholarposter.config import DiscoveryConfig
from scholarposter.discovery import CandidatePaper
from scholarposter.discovery.cache import DiscoveryCache

_OPENALEX_BASE = "https://api.openalex.org"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_injections(text: str) -> str:
    """Remove \\r and \\n from text (prevents User-Agent header injection)."""
    return text.replace("\r", "").replace("\n", "")


def _make_headers(email: str) -> dict[str, str]:
    safe_email = _strip_injections(email)
    ua = (
        f"scholarposter/1.0 (mailto:{safe_email})"
        if safe_email
        else "scholarposter/1.0"
    )
    return {"User-Agent": ua}


def _parse_year(work: dict) -> Optional[int]:
    pub_date = (work.get("publication_date") or "")
    if len(pub_date) >= 4:
        try:
            return int(pub_date[:4])
        except ValueError:
            pass
    return None


def _compute_age_years(year: Optional[int]) -> float:
    if year is None:
        return 0.0
    current_year = datetime.now(timezone.utc).year
    return max(0.0, float(current_year - year))


def _parse_to_candidate(work: dict, mode: str) -> Optional[CandidatePaper]:
    """Parse an OpenAlex work dict into a CandidatePaper. Returns None on missing required fields."""
    doi = (work.get("doi") or "").replace("https://doi.org/", "").strip()
    if not doi or not work.get("title"):
        return None
    oa_id_url = (work.get("id") or "")
    openalex_id = oa_id_url.split("/")[-1] if oa_id_url else ""
    year = _parse_year(work)
    is_oa = bool((work.get("open_access") or {}).get("is_oa", False))
    return CandidatePaper(
        doi=doi,
        openalex_id=openalex_id,
        title=work.get("title", ""),
        year=year,
        cited_by_count=int(work.get("cited_by_count", 0)),
        is_oa=is_oa,
        source="openalex",
        mode=mode,
        age_years=_compute_age_years(year),
    )


def _deduplicate(papers: list[CandidatePaper]) -> list[CandidatePaper]:
    seen: dict[str, CandidatePaper] = {}
    for p in papers:
        if p.doi not in seen:
            seen[p.doi] = p
    return list(seen.values())


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def resolve_doi_to_openalex_id(
    doi: str,
    email: str,
    client: httpx.Client,
    cache: Optional[DiscoveryCache] = None,
    cache_ttl_hours: int = 24,
) -> Optional[str]:
    """Resolve a DOI to an OpenAlex work ID (e.g. 'W1234567').

    Uses GET /works/https://doi.org/{doi}; extracts the W-prefixed ID from
    the response's "id" field.  Returns None on 4xx/5xx or network failure.

    Results are cached in cache (if provided) with cache_ttl_hours TTL.
    """
    if cache is not None:
        cached = cache.get(doi)
        if cached is not None:
            return cached.get("openalex_id")

    url = f"{_OPENALEX_BASE}/works/https://doi.org/{doi}"
    headers = _make_headers(email)
    try:
        resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        oa_id = (data.get("id") or "")
        result = oa_id.split("/")[-1] if oa_id else None
        if result and cache is not None:
            cache.set(doi, {"openalex_id": result}, ttl_hours=cache_ttl_hours)
        return result
    except Exception as e:
        logger.warning(f"resolve_doi_to_openalex_id failed for {doi}: {e}")
        return None


def cited_by(
    dois: list[str],
    config: DiscoveryConfig,
    email: str,
    client: httpx.Client,
    bibliography_dois: Optional[set[str]] = None,
    cache: Optional[DiscoveryCache] = None,
) -> list[CandidatePaper]:
    """Return papers that cite the given seed DOIs (cited-by traversal).

    For each seed DOI:
    1. resolve_doi_to_openalex_id() to get the W-id
    2. GET /works?filter=cites:{openalex_id}

    Results are deduplicated and filtered against bibliography_dois.
    Per-seed failures are logged at WARNING and skipped; other seeds continue.
    """
    bib_dois = bibliography_dois or set()
    results: list[CandidatePaper] = []
    headers = _make_headers(email)

    for doi in dois:
        try:
            openalex_id = resolve_doi_to_openalex_id(
                doi, email, client, cache=cache,
                cache_ttl_hours=config.cache_ttl_hours,
            )
            if openalex_id is None:
                logger.warning(f"cited_by: could not resolve DOI {doi} to OpenAlex ID; skipping")
                continue
            resp = client.get(
                f"{_OPENALEX_BASE}/works",
                params={
                    "filter": f"cites:{openalex_id}",
                    "per_page": config.limit,
                },
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning(f"cited_by: OpenAlex returned {resp.status_code} for {doi}")
                continue
            for work in resp.json().get("results", []):
                paper = _parse_to_candidate(work, mode="cited-by")
                if paper and paper.doi not in bib_dois:
                    results.append(paper)
        except Exception as e:
            logger.warning(f"cited_by failed for {doi}: {e}")

    return _deduplicate(results)


def cites(
    dois: list[str],
    config: DiscoveryConfig,
    email: str,
    client: httpx.Client,
    bibliography_dois: Optional[set[str]] = None,
    cache: Optional[DiscoveryCache] = None,  # reserved; not used in Phase 5
) -> list[CandidatePaper]:
    """Return papers cited by the given seed DOIs (references traversal).

    For each seed DOI:
    1. GET /works/https://doi.org/{doi} → extract referenced_works
    2. Batch-fetch referenced work IDs via filter=ids.openalex:{W1}|{W2}|...

    No Crossref calls are made.  Per-seed failures logged at WARNING and skipped.
    """
    bib_dois = bibliography_dois or set()
    results: list[CandidatePaper] = []
    headers = _make_headers(email)

    for doi in dois:
        try:
            resp = client.get(
                f"{_OPENALEX_BASE}/works/https://doi.org/{doi}",
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning(f"cites: seed work not found for {doi}: {resp.status_code}")
                continue
            data = resp.json()
            referenced_work_urls: list[str] = data.get("referenced_works") or []
            if not referenced_work_urls:
                continue

            # Extract W-ids and batch-fetch (cap at config.limit)
            ref_ids = [url.split("/")[-1] for url in referenced_work_urls[: config.limit]]
            filter_val = "|".join(ref_ids)
            ref_resp = client.get(
                f"{_OPENALEX_BASE}/works",
                params={
                    "filter": f"ids.openalex:{filter_val}",
                    "per_page": config.limit,
                },
                headers=headers,
            )
            if ref_resp.status_code != 200:
                logger.warning(f"cites: batch fetch failed ({ref_resp.status_code}) for {doi}")
                continue
            for work in ref_resp.json().get("results", []):
                paper = _parse_to_candidate(work, mode="cites")
                if paper and paper.doi not in bib_dois:
                    results.append(paper)
        except Exception as e:
            logger.warning(f"cites failed for {doi}: {e}")

    return _deduplicate(results)


def co_cited(
    dois: list[str],
    config: DiscoveryConfig,
    email: str,
    client: Any,
    bibliography_dois: Optional[set[str]] = None,
) -> list[CandidatePaper]:
    """Not implemented — scheduled for Phase 6."""
    raise NotImplementedError("co-cited mode is Phase 6")
