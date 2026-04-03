"""Gemini backend via shared gemini-acp package."""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from tldr_scholar.backends.base import BackendBase

_PROMPT_TEMPLATE = (
    "Summarize the following document in approximately {max_chars} characters.\n"
    "Focus on: {focus}.\n"
    "Be concise, precise, and factual. Do not add information not in the source.\n"
    "{hashtag_instruction}\n\n"
    "<document>\n{text}\n</document>"
)


class GeminiBackend(BackendBase):
    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._model = cfg.get("model", "")
        self._timeout = cfg.get("timeout", 30)

    def summarize(self, text: str, max_chars: int, focus: str,
                  hashtag_instruction: str) -> Optional[str]:
        try:
            from gemini_acp import summarize_via_gemini, ACP_AVAILABLE
        except ImportError:
            logger.debug("gemini-acp not installed")
            return None

        if not ACP_AVAILABLE:
            logger.debug("ACP library not available")
            return None

        prompt = _PROMPT_TEMPLATE.format(
            max_chars=max_chars, focus=focus,
            hashtag_instruction=hashtag_instruction, text=text,
        )
        return summarize_via_gemini(
            text="", prompt=prompt,
            model=self._model, timeout=self._timeout,
        )
