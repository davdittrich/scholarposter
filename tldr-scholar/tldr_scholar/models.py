"""Data models for tldr-scholar."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


class SummaryRequest(BaseModel):
    """Request parameters for summarization."""
    text: str
    max_chars: int = 500
    focus: str = "main findings and novel insights"
    hashtags: int = 0
    backend: Literal["auto", "gemini", "lemonade", "ollama", "extractive"] = "auto"
    backend_config: dict[str, Any] = {}


class SummaryMetadata(BaseModel):
    """Metadata about the summarization result."""
    source: str = ""
    input_type: str = ""       # "pdf", "html", "markdown", "text"
    backend_used: str = ""
    max_chars: int = 500
    focus: str = ""
    char_count: int = 0


class SummaryResult(BaseModel):
    """Result of a summarization operation."""
    text: str
    hashtags: list[str] = []
    metadata: SummaryMetadata = SummaryMetadata()
