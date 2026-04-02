"""Configuration loading and validation for scholarposter."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel


class MastodonConfig(BaseModel):
    instance: str
    credentials_file: str


class FilterConfig(BaseModel):
    skip_hashtags: list[str] = []
    skip_content_types: list[str] = []
    require_hashtags: list[str] = []


class MediaConfig(BaseModel):
    enabled: bool = True
    max_image_size_kb: int = 950


class HashtagRule(BaseModel):
    """Rule: prepend add_hashtag to a post if any of if_any_hashtag are present."""
    add_hashtag: str
    if_any_hashtag: list[str] = []


class PlatformConfig(BaseModel):
    enabled: bool = True
    filters: FilterConfig = FilterConfig()
    media: MediaConfig = MediaConfig()
    hashtag_rules: list[HashtagRule] = []


class GeminiSummarizationConfig(BaseModel):
    timeout_seconds: int = 30


class OllamaSummarizationConfig(BaseModel):
    model: str = "gemma3:9b"
    host: str = "http://localhost:11434"
    timeout_seconds: int = 30


class ExtractiveSummarizationConfig(BaseModel):
    max_sentences: int = 5
    timeout_seconds: int = 10  # Deprecated: kept for config compat; not used at runtime


class SummarizationConfig(BaseModel):
    enabled: bool = True
    backend: Literal["gemini", "ollama", "extractive"] = "extractive"
    max_chars: int = 500
    prompt: str = (
        "Summarize this academic paper/article in 2-3 sentences for a social media post. "
        "Focus on the key finding and methodology. Be concise and precise."
    )
    gemini: GeminiSummarizationConfig = GeminiSummarizationConfig()
    ollama: OllamaSummarizationConfig = OllamaSummarizationConfig()
    extractive: ExtractiveSummarizationConfig = ExtractiveSummarizationConfig()


class CrossrefConfig(BaseModel):
    enabled: bool = True
    etiquette_email: str = ""
    cache_ttl_days: int = 7
    timeout_seconds: int = 5


class UrlUnshortenConfig(BaseModel):
    enabled: bool = True
    timeout_seconds: int = 10
    max_redirects: int = 5


class EnrichmentConfig(BaseModel):
    crossref: CrossrefConfig = CrossrefConfig()
    summarization: SummarizationConfig = SummarizationConfig()
    url_unshorten: UrlUnshortenConfig = UrlUnshortenConfig()


class NotificationBackendConfig(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    topic: Optional[str] = None
    server: Optional[str] = None
    # signal-cli fields
    api_url: Optional[str] = None
    phone_number: Optional[str] = None
    recipients: list[str] = []
    # email fields
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    from_addr: Optional[str] = None
    to_addr: Optional[str] = None


class NotificationsConfig(BaseModel):
    backends: list[NotificationBackendConfig] = []


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "scholarposter.log"
    rotation: str = "10 MB"
    retention: str = "30 days"


class StateConfig(BaseModel):
    state_file: str = "state.json"
    cache_file: str = "cache.json"
    lock_file: str = "scholarposter.lock"


class ScholarposterConfig(BaseModel):
    mastodon: MastodonConfig
    platforms: dict[str, PlatformConfig] = {}
    enrichment: EnrichmentConfig = EnrichmentConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    logging: LoggingConfig = LoggingConfig()
    state: StateConfig = StateConfig()

def load_config(path: Path) -> ScholarposterConfig:
    """Load and validate configuration from a TOML file."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ScholarposterConfig.model_validate(data)
