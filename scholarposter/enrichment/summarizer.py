"""Text summarization backends: Gemini CLI, Ollama, and extractive (sumy)."""
from __future__ import annotations

import re
from math import sqrt
from typing import Optional

import httpx
from loguru import logger
from sumy.nlp.tokenizers import Tokenizer
from sumy.parsers.plaintext import PlaintextParser
from sumy.summarizers.kl import KLSummarizer
from sumy.summarizers.lsa import LsaSummarizer

from scholarposter.config import SummarizationConfig
from scholarposter.gemini_client import summarize_via_gemini

_LANGUAGE = "english"
# Minimum sentence count before extractive summarization is attempted
_MIN_SENTENCES = 3
_MIN_SENTENCE_CHARS = 40  # Minimum chars to include a sentence in summary


def _collect_sentences(sentences, min_chars: int = _MIN_SENTENCE_CHARS) -> str:
    """Join sentences exceeding min_chars, stripping parenthesized text."""
    parts = [str(s) for s in sentences if len(str(s)) > min_chars]
    text = " ".join(parts)
    return re.sub(r"\([^()]*\)", "", text)


# summarize_gemini replaced by summarize_via_gemini from gemini_client.py (ACP-based)


_cached_lemonade_model: Optional[str] = None


def _detect_lemonade_model(host: str, timeout: int = 5) -> str:
    """Query Lemonade for the first available model. Cached after first success."""
    global _cached_lemonade_model
    if _cached_lemonade_model is not None:
        return _cached_lemonade_model
    try:
        resp = httpx.get(f"{host}/v1/models", timeout=timeout)
        models = resp.json().get("data", [])
        if models:
            _cached_lemonade_model = models[0]["id"]
            return _cached_lemonade_model
    except Exception:
        pass
    return ""


def summarize_lemonade(
    text: str,
    model: str,
    host: str,
    prompt: str,
    timeout: int,
) -> Optional[str]:
    """Summarize text via Lemonade's OpenAI-compatible API.

    POSTs to {host}/v1/chat/completions with system/user messages.
    Returns the assistant's response text, or None on error.
    """
    if not model:
        model = _detect_lemonade_model(host)
        if not model:
            logger.debug("No Lemonade models available")
            return None
    try:
        response = httpx.post(
            f"{host}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                "stream": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return content.strip() or None
    except (httpx.TimeoutException, httpx.HTTPError, KeyError, IndexError, Exception):
        return None


def summarize_ollama(
    text: str,
    model: str,
    host: str,
    prompt: str,
    timeout: int,
) -> Optional[str]:
    """Summarize text via the Ollama REST API.

    POSTs to {host}/api/generate with stream=False.
    Returns the response text on success, None on any HTTP/timeout error.
    """
    try:
        response = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": f"{prompt}\n\n{text}", "stream": False},
            timeout=timeout,
        )
        return response.json()["response"].strip() or None
    except (httpx.TimeoutException, httpx.HTTPError, Exception):
        return None


def summarize_extractive(
    text: str,
    max_sentences: int = 5,
    max_chars: int = 1000,
) -> str:
    """Extractive summarization using sumy's KL summarizer.

    Ported from m2blusky.py getSummarytext(), with a two-pass reduction for
    very long documents (> 150 sentences) and a minimum sentence threshold.

    Returns empty string for text with fewer than _MIN_SENTENCES sentences.
    """
    parser = PlaintextParser.from_string(text, Tokenizer(_LANGUAGE))
    sc = len(parser.document.sentences)

    if sc < _MIN_SENTENCES:
        return ""

    kl_summ = KLSummarizer()
    lsa_summ = LsaSummarizer()

    full_text = ""

    # Stage 1: reduce very long documents
    if sc > 150:
        reduced_count = max(150, int(150 + sqrt(sc - 150)))
        full_text = _collect_sentences(kl_summ(parser.document, reduced_count))
        parser = PlaintextParser.from_string(full_text, Tokenizer(_LANGUAGE))
        sc = len(parser.document.sentences)
        full_text = ""

    pc = len(parser.document.paragraphs)
    nos = min(max(3, int(0.01 * sc), int(0.05 * pc)), max_sentences)

    full_text = _collect_sentences(lsa_summ(parser.document, nos))

    # Reduce further if still too long
    while len(full_text) > max_chars:
        nos -= 1
        if nos == 0:
            break
        full_text = _collect_sentences(lsa_summ(parser.document, nos), min_chars=0)

    if len(full_text) > max_chars:
        full_text = full_text[:max_chars - 1] + "\u2026"

    return full_text.strip()


def summarize(
    text: str,
    backend: str,
    max_chars: int,
    prompt: str,
    config: SummarizationConfig,
) -> str:
    """Summarize text using the specified backend, with fallback chain.

    Fallback order: gemini -> lemonade -> ollama -> extractive.
    Result is truncated to max_chars.
    Returns empty string if all backends fail.
    """
    result: Optional[str] = None

    # Try backends in order starting from the preferred one
    backend_order = _build_backend_order(backend)

    for i, b in enumerate(backend_order):
        if i > 0:
            logger.warning(f"Summarizer: falling back to {b}")
        if b == "gemini":
            result = summarize_via_gemini(
                text,
                prompt=prompt,
                model=config.gemini.model,
                timeout=config.gemini.timeout_seconds,
            )
        elif b == "lemonade":
            result = summarize_lemonade(
                text,
                model=config.lemonade.model,
                host=config.lemonade.host,
                prompt=prompt,
                timeout=config.lemonade.timeout_seconds,
            )
        elif b == "ollama":
            result = summarize_ollama(
                text,
                model=config.ollama.model,
                host=config.ollama.host,
                prompt=prompt,
                timeout=config.ollama.timeout_seconds,
            )
        elif b == "extractive":
            result = summarize_extractive(
                text,
                max_sentences=config.extractive.max_sentences,
                max_chars=max_chars,
            )

        if result:
            logger.debug(f"Summarizer: {b} produced {len(result)} chars")
            break

    if not result:
        return ""

    return result[:max_chars]


def _build_backend_order(preferred: str) -> list[str]:
    """Return backend names starting with preferred, then the fallback sequence."""
    all_backends = ["gemini", "lemonade", "ollama", "extractive"]
    if preferred in all_backends:
        idx = all_backends.index(preferred)
        return all_backends[idx:]  # no wrap-around — only cheaper/simpler backends
    return all_backends
