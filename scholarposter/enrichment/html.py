"""HTML enrichment: OG tag extraction and body text extraction via trafilatura."""
from __future__ import annotations

from typing import Optional

import trafilatura
from bs4 import BeautifulSoup


def extract_og_tags(html_str: str) -> dict:
    """Extract Open Graph metadata from an HTML string.

    Falls back to <title> tag for title, first <p> text for description
    when OG tags are absent.

    Returns a dict with keys: title, description, image (each may be None).
    """
    soup = BeautifulSoup(html_str, "lxml")

    def _og(prop: str) -> Optional[str]:
        tag = soup.find("meta", property=f"og:{prop}")
        if tag and tag.get("content"):
            return tag["content"].strip() or None
        return None

    title = _og("title")
    if title is None:
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            title = title_tag.get_text(strip=True)

    description = _og("description")
    if description is None:
        p_tag = soup.find("p")
        if p_tag and p_tag.get_text(strip=True):
            description = p_tag.get_text(strip=True)

    image = _og("image")

    return {"title": title, "description": description, "image": image}


def extract_body_text(html_str: str) -> Optional[str]:
    """Extract body text from an HTML string using trafilatura.

    Returns the extracted plain text, or None if trafilatura returns empty/None.
    """
    result = trafilatura.extract(
        html_str,
        output_format="txt",
        include_comments=False,
        include_tables=True,
    )
    if not result:
        return None
    return result
