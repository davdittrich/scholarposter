"""Configuration loading for tldr-scholar."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel


class GeminiConfig(BaseModel):
    model: str = ""
    timeout: int = 30


class LemonadeConfig(BaseModel):
    model: str = ""
    host: str = "http://127.0.0.1:8000"
    timeout: int = 60
    ctx_size: int = 8192
    load_timeout: int = 180
    preferred_models: list[str] = [
        "Phi-4-mini-instruct-GGUF",
        "Qwen3-4B-Instruct-2507-GGUF",
        "Qwen3-8B-GGUF",
        "DeepSeek-Qwen3-8B-GGUF",
        "Llama-3.2-3B-Instruct-GGUF",
        "Gemma-3-4b-it-GGUF",
        "Qwen3-1.7B-GGUF",
        "Llama-3.2-1B-Instruct-GGUF",
    ]


class OllamaConfig(BaseModel):
    model: str = "gemma3:9b"
    host: str = "http://localhost:11434"
    timeout: int = 30


class TldrScholarConfig(BaseModel):
    gemini: GeminiConfig = GeminiConfig()
    lemonade: LemonadeConfig = LemonadeConfig()
    ollama: OllamaConfig = OllamaConfig()


def load_config(path: Path) -> TldrScholarConfig:
    """Load config from TOML file. Returns defaults if file doesn't exist."""
    if not path.exists():
        return TldrScholarConfig()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return TldrScholarConfig.model_validate(data)
