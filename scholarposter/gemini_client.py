"""Agy (Antigravity CLI) client for text summarization.

Replaces the gemini-acp ACP/JSON-RPC backend with a simple subprocess call
to `agy --print`. Public API is unchanged so callers need no modification.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from loguru import logger

AGY_AVAILABLE: bool = shutil.which("agy") is not None

# Backward-compat alias so any code using ACP_AVAILABLE still works.
ACP_AVAILABLE = AGY_AVAILABLE


@dataclass
class GeminiUsage:
    tokens_used: int
    cost_usd: float | None
    cost_currency: str | None
    is_estimated: bool = True
    cost_is_estimated: bool = False


def summarize_via_gemini(
    text: str,
    prompt: str,
    model: str = "",
    timeout: int = 30,
) -> tuple[str | None, GeminiUsage | None]:
    """Summarize text using agy CLI (--print mode).

    Returns (response_text, usage) where usage is a GeminiUsage or None.
    Falls back to (None, None) if agy is not installed or any error occurs.
    The summarizer fallback chain handles the None text case.
    """
    if not AGY_AVAILABLE:
        logger.warning("agy CLI not found on PATH")
        return (None, None)

    full_prompt = f"{prompt}\n\n{text}"

    # --add-dir with a fresh tmpdir isolates this invocation from any
    # concurrent interactive agy sessions, preventing conversation bleed.
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = ["agy", "--add-dir", tmpdir, "--print", full_prompt]
            if model:
                cmd.extend(["--model", model])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        logger.warning(f"agy timed out after {timeout}s")
        return (None, None)
    except FileNotFoundError:
        logger.warning("agy CLI not found on PATH")
        return (None, None)
    except Exception as e:
        logger.warning(f"agy error: {e}")
        return (None, None)

    if result.returncode != 0:
        logger.warning(f"agy exited {result.returncode}: {result.stderr.strip()[:200]}")
        return (None, None)

    response = result.stdout.strip()
    if not response:
        return (None, None)

    estimated_tokens = (len(full_prompt) + len(response)) // 4
    usage = GeminiUsage(
        tokens_used=estimated_tokens,
        cost_usd=None,
        cost_currency=None,
        is_estimated=True,
        cost_is_estimated=False,
    )
    return (response, usage)


__all__ = ["summarize_via_gemini", "AGY_AVAILABLE", "ACP_AVAILABLE", "GeminiUsage"]
