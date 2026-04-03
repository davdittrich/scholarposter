"""Lemonade backend — OpenAI-compatible /v1/chat/completions API."""
from __future__ import annotations

import re
import shutil
import subprocess
import time as _time
from typing import Any, Optional

import httpx
from loguru import logger

from tldr_scholar.backends.base import BackendBase, SUMMARY_PROMPT_TEMPLATE

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_cached_model: Optional[str] = None


class LemonadeBackend(BackendBase):
    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._model = cfg.get("model", "")
        self._host = cfg.get("host", "http://127.0.0.1:8000")
        self._timeout = cfg.get("timeout", 60)
        self._ctx_size = cfg.get("ctx_size", 8192)
        self._load_timeout = cfg.get("load_timeout", 180)
        self._preferred_models = cfg.get("preferred_models", [
            "Phi-4-mini-instruct-GGUF",
            "Qwen3-4B-Instruct-2507-GGUF",
            "Qwen3-8B-GGUF",
            "DeepSeek-Qwen3-8B-GGUF",
            "Llama-3.2-3B-Instruct-GGUF",
            "Gemma-3-4b-it-GGUF",
            "Qwen3-1.7B-GGUF",
            "Llama-3.2-1B-Instruct-GGUF",
        ])

    def summarize(self, text: str, max_chars: int, focus: str,
                  hashtag_instruction: str) -> Optional[str]:
        model = self._model
        if not model:
            model = _ensure_model(
                self._host, self._preferred_models,
                self._ctx_size, self._load_timeout,
            )
            if not model:
                logger.debug("No Lemonade model available")
                return None

        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            max_chars=max_chars, focus=focus,
            hashtag_instruction=hashtag_instruction, text=text,
        )
        try:
            response = httpx.post(
                f"{self._host}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                    ],
                    "stream": False,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return content.strip() or None
        except Exception:
            return None


def _get_downloaded_models() -> list[str]:
    if not shutil.which("lemonade"):
        return []
    try:
        result = subprocess.run(
            ["lemonade", "list", "--downloaded"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        models = []
        for line in result.stdout.strip().split("\n")[2:]:
            line = line.strip()
            if not line or line.startswith("-"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "Yes":
                models.append(parts[0])
        return models
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return []


def _load_model(model: str, ctx_size: int, host: str, load_timeout: int) -> bool:
    if not _MODEL_NAME_RE.match(model):
        raise ValueError(f"Invalid model name: {model}")
    try:
        result = subprocess.run(
            ["lemonade", "load", model, "--ctx-size", str(ctx_size)],
            capture_output=True, text=True, timeout=load_timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False
    if result.returncode != 0:
        logger.warning(f"lemonade load exited with code {result.returncode}")
        return False
    deadline = _time.monotonic() + min(30, load_timeout)
    while _time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{host}/v1/models", timeout=5)
            if resp.json().get("data"):
                return True
        except Exception:
            pass
        _time.sleep(2)
    return False


def _ensure_model(host: str, preferred: list[str], ctx_size: int,
                  load_timeout: int) -> str:
    global _cached_model
    if _cached_model is not None:
        return _cached_model
    try:
        resp = httpx.get(f"{host}/v1/models", timeout=5)
        models = resp.json().get("data", [])
        if models:
            _cached_model = models[0]["id"]
            return _cached_model
    except Exception:
        pass
    downloaded = _get_downloaded_models()
    if not downloaded:
        return ""
    downloaded_normalized = {d.removeprefix("user."): d for d in downloaded}
    chosen = ""
    for pref in preferred:
        if pref in downloaded_normalized:
            chosen = downloaded_normalized[pref]
            break
    if not chosen:
        chosen = downloaded[0]
    logger.info(f"Loading Lemonade model '{chosen}' with ctx_size={ctx_size}")
    if _load_model(chosen, ctx_size, host, load_timeout):
        try:
            resp = httpx.get(f"{host}/v1/models", timeout=10)
            models = resp.json().get("data", [])
            if models:
                _cached_model = models[0]["id"]
                return _cached_model
        except Exception:
            pass
        _cached_model = chosen
        return chosen
    logger.warning(f"Failed to load Lemonade model '{chosen}'")
    return ""
