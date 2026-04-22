"""Configuration loading and validation for scholarposter."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator


class ConfigError(Exception):
    """Configuration validation error (non-recoverable)."""


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
    model: str = ""  # empty = CLI default; e.g. "gemini-3-flash-preview"
    timeout_seconds: int = 30


class LemonadeSummarizationConfig(BaseModel):
    model: str = ""  # empty = auto-detect/auto-load best downloaded model
    host: str = "http://127.0.0.1:8000"
    timeout_seconds: int = 60  # inference timeout
    ctx_size: int = 8192  # context window for model loading (tokens)
    load_timeout_seconds: int = 180  # max time for model load (includes possible download)
    preferred_models: list[str] = [
        # Tier 1: Best quality/size for CPU (3-4B, instruction-tuned)
        "Phi-4-mini-instruct-GGUF",         # 3.8B — beats 6-9B on accuracy benchmarks
        "Qwen3-4B-Instruct-2507-GGUF",      # 4B — #1 in fine-tuned benchmarks
        # Tier 2: Higher quality if GPU available (8B)
        "Qwen3-8B-GGUF",                    # 8B — strongest instruction-following
        "DeepSeek-Qwen3-8B-GGUF",           # 8B — DeepSeek distillation quality
        # Tier 3: Lightweight fallbacks
        "Llama-3.2-3B-Instruct-GGUF",       # 3B — 128K context, good instruction following
        "Gemma-3-4b-it-GGUF",               # 4B — solid all-rounder
        # Tier 4: Ultra-light (minimal hardware)
        "Qwen3-1.7B-GGUF",                  # 1.7B — rivals 7B vintage models
        "Llama-3.2-1B-Instruct-GGUF",       # 1B — last resort, still usable
    ]


class OllamaSummarizationConfig(BaseModel):
    model: str = "gemma3:9b"
    host: str = "http://localhost:11434"
    timeout_seconds: int = 30


class ExtractiveSummarizationConfig(BaseModel):
    max_sentences: int = 5


class SummarizationConfig(BaseModel):
    enabled: bool = True
    backend: Literal["gemini", "lemonade", "ollama", "extractive"] = "extractive"
    max_chars: int = 150
    prompt: str = (
        "Summarize the key finding of this academic paper/article "
        "in one sentence (~150 characters) for a social media link card. "
        "Be precise and specific."
    )
    gemini: GeminiSummarizationConfig = GeminiSummarizationConfig()
    lemonade: LemonadeSummarizationConfig = LemonadeSummarizationConfig()
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


class ProgressiveEnrichmentConfig(BaseModel):
    """Stage 2.5 pre-check: skip PDF download when Crossref abstract is sufficient."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True


class ThumbnailFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    width: int = 1200
    height: int = 627
    background_color: str = "#1c1c2e"
    text_color: str = "#f0f0f0"


class EnrichmentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    crossref: CrossrefConfig = CrossrefConfig()
    summarization: SummarizationConfig = SummarizationConfig()
    url_unshorten: UrlUnshortenConfig = UrlUnshortenConfig()
    progressive: ProgressiveEnrichmentConfig = ProgressiveEnrichmentConfig()
    thumbnail_fallback: ThumbnailFallbackConfig = ThumbnailFallbackConfig()


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
    bibliography_file: str = "bibliography.json"


class AuditConfig(BaseModel):
    """Audit log configuration (FR-90). file is resolved by load_config()."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    file: str = "audit.jsonl"
    min_report_sample: int = 3
    # Phase 6 placeholders — not enforced until rotation/retention is implemented
    rotation_max_mb: int = 50
    retention_days: int = 365

    _resolved_file: Optional[Path] = PrivateAttr(default=None)

    @property
    def resolved_file(self) -> Optional[Path]:
        """Absolute path to the audit log file (set by load_config())."""
        return self._resolved_file


class DiscoveryRankingConfig(BaseModel):
    """Composite score weights for citation graph ranking."""
    model_config = ConfigDict(extra="ignore")

    oa_weight: float = 1.2
    recency_half_life_years: float = 2.0


class DiscoveryConfig(BaseModel):
    """Citation graph discovery configuration (US-014)."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    sources: list[str] = ["openalex"]
    modes: list[str] = ["cited-by", "cites"]
    limit: int = 20
    digest_email: Optional[str] = None
    digest_auto: bool = False
    cache_ttl_hours: int = 24
    ranking: DiscoveryRankingConfig = DiscoveryRankingConfig()

    @model_validator(mode="after")
    def _validate_digest_email(self) -> "DiscoveryConfig":
        if self.digest_email is not None and ("\r" in self.digest_email or "\n" in self.digest_email):
            raise ConfigError(
                f"discovery.digest_email contains newline characters (SMTP injection risk): "
                f"{self.digest_email!r}"
            )
        return self


class ScholarposterConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mastodon: MastodonConfig
    platforms: dict[str, PlatformConfig] = {}
    enrichment: EnrichmentConfig = EnrichmentConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    logging: LoggingConfig = LoggingConfig()
    state: StateConfig = StateConfig()
    audit: AuditConfig = AuditConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()


def load_config(path: Path) -> ScholarposterConfig:
    """Load and validate configuration from a TOML file.

    Post-processing: resolves audit.file relative to the config parent directory.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    cfg = ScholarposterConfig.model_validate(data)

    # Resolve audit.file relative to config parent directory
    audit_file = Path(cfg.audit.file)
    if audit_file.is_absolute():
        cfg.audit._resolved_file = audit_file
    else:
        cfg.audit._resolved_file = path.parent.resolve() / audit_file

    return cfg
