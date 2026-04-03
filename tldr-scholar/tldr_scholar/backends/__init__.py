"""Backend dispatch for tldr-scholar."""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from tldr_scholar.backends.base import BackendBase
from tldr_scholar.backends.extractive import ExtractiveBackend
from tldr_scholar.backends.gemini import GeminiBackend
from tldr_scholar.backends.lemonade import LemonadeBackend
from tldr_scholar.backends.ollama import OllamaBackend

_BACKEND_MAP: dict[str, type[BackendBase]] = {
    "gemini": GeminiBackend,
    "lemonade": LemonadeBackend,
    "ollama": OllamaBackend,
    "extractive": ExtractiveBackend,
}

_AUTO_ORDER = ["gemini", "lemonade", "ollama", "extractive"]


def get_backend(name: str, config: dict[str, Any] | None = None) -> BackendBase:
    """Create a backend instance by name. Raises ValueError for unknown names.

    Config can be:
    - dict-of-dicts keyed by backend name (CLI path): {"lemonade": {...}, "ollama": {...}}
      → extracts the sub-dict for this backend
    - flat dict (library API path): {"host": "...", "model": "..."}
      → passed through to the backend directly
    """
    cls = _BACKEND_MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown backend: {name}. Choose from: {', '.join(_BACKEND_MAP)}")
    if config and name in config and isinstance(config[name], dict):
        return cls(config[name])
    return cls(config)


def run_with_fallback(
    text: str,
    max_chars: int,
    focus: str,
    hashtag_instruction: str,
    backend: str,
    config: dict[str, Any] | None = None,
) -> tuple[Optional[str], str]:
    """Run summarization with optional fallback chain.

    Returns (response_text, backend_used) or (None, "") on complete failure.

    backend="auto": tries gemini → lemonade → ollama → extractive.
    backend=explicit: single attempt, no fallback.
    """
    if backend == "auto":
        for i, name in enumerate(_AUTO_ORDER):
            if i > 0:
                logger.warning(f"Summarizer: falling back to {name}")
            b = get_backend(name, config)
            result = b.summarize(text, max_chars, focus, hashtag_instruction)
            if result:
                return result, name
        return None, ""
    else:
        b = get_backend(backend, config)
        result = b.summarize(text, max_chars, focus, hashtag_instruction)
        if result:
            return result, backend
        return None, ""
