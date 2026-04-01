"""DOI detection from URLs/text and metadata lookup via Crossref API."""
from __future__ import annotations

import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

# Matches DOI suffix patterns: 10.XXXX/anything
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-.;()/:\w]+")

# Sentence-terminating characters that should be stripped from the end of a DOI
# match. Note: ) is intentionally excluded because it can legitimately end a
# DOI suffix (e.g. 10.1000/xyz(2024)).
_TRAILING_PUNCT = ".;,:"


def _clean_doi(raw: str) -> str:
    """Strip trailing sentence punctuation from a DOI match."""
    return raw.rstrip(_TRAILING_PUNCT)


def detect_dois(urls: list[str], text: str) -> list[str]:
    """Detect DOIs in a list of URLs and a text string.

    Searches each URL and the full text with a DOI regex, deduplicates,
    and returns a list of matched DOI strings.
    """
    found: set[str] = set()

    for url in urls:
        for match in _DOI_PATTERN.finditer(url):
            cleaned = _clean_doi(match.group(0))
            if cleaned:
                found.add(cleaned)

    for match in _DOI_PATTERN.finditer(text):
        cleaned = _clean_doi(match.group(0))
        if cleaned:
            found.add(cleaned)

    return list(found)


def lookup_doi(
    doi: str,
    etiquette_email: str = "",
    timeout: int = 5,
) -> Optional[dict]:
    """Look up a DOI via the Crossref API and return structured metadata.

    Returns a dict with keys: title, abstract (HTML-stripped), authors.
    Returns None on network errors, not-found, or any exception.
    """
    try:
        resp = httpx.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": f"scholarposter/1.0 (mailto:{etiquette_email or 'unknown@example.com'})"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()["message"]
    except Exception:
        return None

    if data is None:
        return None

    result: dict = {}

    # Title: Crossref returns a list
    titles = data.get("title", [])
    result["title"] = titles[0] if titles else ""

    # Abstract: strip JATS/HTML markup
    raw_abstract = data.get("abstract", "")
    if raw_abstract:
        soup = BeautifulSoup(raw_abstract, "lxml")
        result["abstract"] = soup.get_text(separator=" ").strip()
    else:
        result["abstract"] = ""

    # Authors: list of "Given Family" strings
    raw_authors = data.get("author", [])
    authors = []
    for author in raw_authors:
        given = author.get("given", "")
        family = author.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)
    result["authors"] = authors

    return result
