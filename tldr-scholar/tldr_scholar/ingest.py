"""Input handling: PDF, HTML (URL), Markdown, and plain text ingestion."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
import trafilatura
from loguru import logger

# fitz and pymupdf4llm are heavy C-extension imports (~200ms).
# Imported lazily inside _ingest_pdf and _ingest_url's PDF branch.

_MAX_INPUT_BYTES = 5_000_000  # 5 MB cap


class UnsupportedInputError(Exception):
    """Raised when input type is not supported."""


class PasswordProtectedError(Exception):
    """Raised when PDF is password-protected."""


class EmptyTextError(Exception):
    """Raised when no text could be extracted."""


def ingest(source: str) -> tuple[str, str]:
    """Dispatch input by type. Returns (text, input_type).

    Raises UnsupportedInputError, PasswordProtectedError, EmptyTextError.
    """
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return _ingest_url(source)
    if parsed.scheme and parsed.scheme not in ("", "file"):
        raise UnsupportedInputError(f"Unsupported URL scheme: {parsed.scheme}")

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {source}")

    ext = path.suffix.lower()
    if ext == ".pdf":
        text = _ingest_pdf(path)
        input_type = "pdf"
    elif ext == ".md":
        text = _ingest_markdown(path)
        input_type = "markdown"
    elif ext == ".txt":
        text = _ingest_text(path)
        input_type = "text"
    else:
        raise UnsupportedInputError(f"Unsupported file type: {ext}")

    if not text or not text.strip():
        raise EmptyTextError(f"No text could be extracted from {source}")
    return text, input_type


def _ingest_pdf(path: Path) -> str:
    """Extract text from PDF (first 20 pages). Detects password-protected PDFs."""
    import fitz
    import pymupdf4llm

    pdf_bytes = path.read_bytes()
    if len(pdf_bytes) > _MAX_INPUT_BYTES:
        logger.warning(f"PDF {path} exceeds {_MAX_INPUT_BYTES} bytes, truncating")
        pdf_bytes = pdf_bytes[:_MAX_INPUT_BYTES]

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        if "password" in str(e).lower() or "encrypted" in str(e).lower():
            raise PasswordProtectedError(f"PDF is password-protected: {path}")
        raise

    if doc.is_encrypted:
        raise PasswordProtectedError(f"PDF is password-protected: {path}")


    page_count = len(doc)
    pages = list(range(min(20, page_count)))
    try:
        text = pymupdf4llm.to_markdown(doc, pages=pages)
    except Exception:
        return ""

    return text.strip() if text else ""


def _ingest_url(url: str) -> tuple[str, str]:
    """Fetch URL, detect type, extract text. Returns (text, input_type)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsupportedInputError(f"Only http/https URLs supported, got: {parsed.scheme}")

    # Detect content type
    content_type = ""
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            head = client.head(url)
            content_type = head.headers.get("content-type", "")
    except Exception:
        pass

    is_pdf = "pdf" in content_type.lower() or parsed.path.lower().endswith(".pdf")

    if is_pdf:
        # Download PDF bytes
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=30)
            pdf_bytes = resp.content
            if len(pdf_bytes) > _MAX_INPUT_BYTES:
                logger.warning(f"PDF from {url} exceeds {_MAX_INPUT_BYTES} bytes, truncating")
                pdf_bytes = pdf_bytes[:_MAX_INPUT_BYTES]
        except Exception as e:
            raise EmptyTextError(f"Failed to download PDF from {url}: {e}")

        import fitz
        import pymupdf4llm
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = list(range(min(20, len(doc))))
            text = pymupdf4llm.to_markdown(doc, pages=pages)
        except Exception:
            text = ""

        if not text or not text.strip():
            raise EmptyTextError(f"No text could be extracted from {url}")
        return text.strip(), "pdf"
    else:
        # HTML — streaming fetch with size cap
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                with client.stream("GET", url) as resp:
                    chunks: list[str] = []
                    total = 0
                    for chunk in resp.iter_text(4096):
                        total += len(chunk.encode("utf-8"))
                        if total > _MAX_INPUT_BYTES:
                            logger.warning(f"HTML from {url} exceeds {_MAX_INPUT_BYTES} bytes, truncating")
                            break
                        chunks.append(chunk)
                html = "".join(chunks)
        except Exception as e:
            raise EmptyTextError(f"Failed to fetch {url}: {e}")

    
        text = trafilatura.extract(html, output_format="txt",
                                   include_comments=False, include_tables=True)
        if not text or not text.strip():
            raise EmptyTextError(f"No text could be extracted from {url}")
        return text.strip(), "html"


def _ingest_markdown(path: Path) -> str:
    """Read markdown file, strip formatting."""
    raw = _read_with_limit(path)
    # Strip markdown formatting: headings, links, emphasis, images
    text = re.sub(r'^#+\s+', '', raw, flags=re.MULTILINE)  # headings
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)  # images
    text = re.sub(r'\[([^\]]*)\]\([^\)]*\)', r'\1', text)  # links → text
    text = re.sub(r'[*_]{1,3}([^*_]+)[*_]{1,3}', r'\1', text)  # emphasis
    text = re.sub(r'`([^`]+)`', r'\1', text)  # inline code
    text = re.sub(r'```[\s\S]*?```', '', text)  # code blocks
    return text.strip()


def _ingest_text(path: Path) -> str:
    """Read plain text file."""
    return _read_with_limit(path).strip()


def _read_with_limit(path: Path) -> str:
    """Read file with 5MB limit. Warns on truncation."""
    size = path.stat().st_size
    if size > _MAX_INPUT_BYTES:
        logger.warning(f"{path} is {size} bytes, truncating to {_MAX_INPUT_BYTES}")
    with open(path, "r", errors="replace") as f:
        return f.read(_MAX_INPUT_BYTES)
