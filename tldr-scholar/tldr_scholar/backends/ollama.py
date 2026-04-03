"""Ollama backend — /api/generate endpoint."""
from __future__ import annotations

from typing import Any, Optional

import httpx
from loguru import logger

from tldr_scholar.backends.base import BackendBase

_PROMPT_TEMPLATE = (
    "Summarize the following document in approximately {max_chars} characters.\n"
    "Focus on: {focus}.\n"
    "Be concise, precise, and factual. Do not add information not in the source.\n"
    "{hashtag_instruction}\n\n"
    "<document>\n{text}\n</document>"
)


class OllamaBackend(BackendBase):
    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._model = cfg.get("model", "gemma3:9b")
        self._host = cfg.get("host", "http://localhost:11434")
        self._timeout = cfg.get("timeout", 30)

    def summarize(self, text: str, max_chars: int, focus: str,
                  hashtag_instruction: str) -> Optional[str]:
        prompt = _PROMPT_TEMPLATE.format(
            max_chars=max_chars, focus=focus,
            hashtag_instruction=hashtag_instruction, text=text,
        )
        try:
            response = httpx.post(
                f"{self._host}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
                timeout=self._timeout,
            )
            return response.json()["response"].strip() or None
        except Exception:
            return None
