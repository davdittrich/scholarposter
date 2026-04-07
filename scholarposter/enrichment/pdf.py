"""PDF enrichment: metadata and text extraction via PyMuPDF and pymupdf4llm."""
from __future__ import annotations

from typing import Optional

import fitz  # PyMuPDF
import pymupdf4llm


def extract_pdf_metadata(pdf_bytes: bytes) -> dict:
    """Extract title and description from a PDF's DocInfo/XMP metadata.

    Returns a dict with 'title' and/or 'description' keys.
    Returns an empty dict if the bytes are invalid or extraction fails.
    """
    if not pdf_bytes:
        return {}
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return {}

    meta = doc.metadata or {}
    result: dict = {}

    raw_title = meta.get("title")
    if isinstance(raw_title, list):
        raw_title = raw_title[0] if raw_title else None
    title = (raw_title or "").strip()
    if title:
        result["title"] = title

    # subject field maps to Dublin Core description
    raw_subject = meta.get("subject")
    if isinstance(raw_subject, list):
        raw_subject = raw_subject[0] if raw_subject else None
    subject = (raw_subject or "").strip()
    if subject:
        result["description"] = subject

    return result


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 20) -> Optional[str]:
    """Extract text from a PDF as Markdown using pymupdf4llm.

    Reads up to max_pages pages.
    Returns None if the bytes are invalid or extraction produces no text.
    """
    if not pdf_bytes:
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        if page_count == 0:
            return None
        pages = list(range(min(max_pages, page_count)))
        text = pymupdf4llm.to_markdown(doc, pages=pages)
    except Exception:
        return None

    if not text or not text.strip():
        return None
    return text
