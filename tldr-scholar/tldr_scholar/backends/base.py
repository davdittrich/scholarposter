"""Abstract base class for summarization backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the following document in approximately {max_chars} characters.\n"
    "Focus on: {focus}.\n"
    "Be concise, precise, and factual. Do not add information not in the source.\n"
    "{hashtag_instruction}\n\n"
    "<document>\n{text}\n</document>"
)


class BackendBase(ABC):
    """Abstract interface for summarization backends.

    All backends return a raw response string or None on failure.

    For LLM backends: the response includes summary + embedded hashtags
    (per hashtag_instruction). The caller parses them via hashtags.py.

    For the extractive backend: hashtag_instruction is ignored. Hashtags
    are generated separately by the caller via generate_hashtags_tfidf().
    The response contains only the summary text.
    """

    @abstractmethod
    def summarize(
        self,
        text: str,
        max_chars: int,
        focus: str,
        hashtag_instruction: str,
    ) -> Optional[str]:
        """Summarize text. Returns response string or None on failure."""
