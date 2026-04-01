"""Text summarization backends: Gemini CLI, Ollama, and extractive (sumy)."""
from __future__ import annotations

import re
import subprocess
from math import sqrt
from typing import Optional

import httpx
from sumy.nlp.tokenizers import Tokenizer
from sumy.parsers.plaintext import PlaintextParser
from sumy.summarizers.kl import KLSummarizer
from sumy.summarizers.lsa import LsaSummarizer

from scholarposter.config import SummarizationConfig

_LANGUAGE = "english"
# Minimum sentence count before extractive summarization is attempted
_MIN_SENTENCES = 3


def summarize_gemini(text: str, prompt: str, timeout: int) -> Optional[str]:
    """Summarize text using the Gemini CLI subprocess.

    Calls: gemini -p <prompt>  (text passed via stdin)
    Returns stripped stdout on success, None on timeout or nonzero exit code.
    """
    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            input=text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None

    output = result.stdout.strip()
    return output if output else None


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
        for sentence in kl_summ(parser.document, reduced_count):
            if len(str(sentence)) > 40:
                full_text += str(sentence) + " "
        full_text = re.sub(r"\([^()]*\)", "", full_text)
        parser = PlaintextParser.from_string(full_text, Tokenizer(_LANGUAGE))
        sc = len(parser.document.sentences)
        full_text = ""

    pc = len(parser.document.paragraphs)
    nos = min(max(3, int(0.01 * sc), int(0.05 * pc)), max_sentences)

    for sentence in lsa_summ(parser.document, nos):
        if len(str(sentence)) > 40:
            full_text += str(sentence) + " "
    full_text = re.sub(r"\([^()]*\)", "", full_text)

    # Reduce further if still too long
    while len(full_text) > max_chars:
        nos -= 1
        if nos == 0:
            break
        full_text = ""
        for sentence in lsa_summ(parser.document, nos):
            full_text += str(sentence) + " "
        full_text = re.sub(r"\([^()]*\)", "", full_text)

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

    Fallback order: gemini -> ollama -> extractive.
    Result is truncated to max_chars.
    Returns empty string if all backends fail.
    """
    result: Optional[str] = None

    # Try backends in order starting from the preferred one
    backend_order = _build_backend_order(backend)

    for b in backend_order:
        if b == "gemini":
            result = summarize_gemini(
                text,
                prompt=prompt,
                timeout=config.gemini.timeout_seconds,
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
            break

    if not result:
        return ""

    return result[:max_chars]


def _build_backend_order(preferred: str) -> list[str]:
    """Return backend names starting with preferred, then the fallback sequence."""
    all_backends = ["gemini", "ollama", "extractive"]
    if preferred in all_backends:
        idx = all_backends.index(preferred)
        return all_backends[idx:] + all_backends[:idx]
    return all_backends
