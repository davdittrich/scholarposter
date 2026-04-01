"""Tests for scholarposter.config"""
import pytest
from pathlib import Path
import tomllib
from scholarposter.config import (
    load_config,
    ScholarposterConfig,
    MastodonConfig,
    FilterConfig,
    MediaConfig,
    PlatformConfig,
    SummarizationConfig,
    EnrichmentConfig,
    NotificationsConfig,
    LoggingConfig,
    StateConfig,
)

MINIMAL_TOML = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"
"""

FULL_TOML = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[platforms.bluesky]
enabled = true

[platforms.bluesky.filters]
skip_hashtags = ["nobridge", "private"]
skip_content_types = ["sensitive"]
require_hashtags = []

[platforms.bluesky.media]
enabled = true
max_image_size_kb = 950
max_video_size_mb = 50
supported_types = ["image/jpeg", "image/png", "image/gif", "image/webp", "video/mp4"]

[platforms.linkedin]
enabled = true

[platforms.linkedin.filters]
skip_hashtags = ["nobridge", "shitpost"]
skip_content_types = ["sensitive", "poll"]
require_hashtags = []

[platforms.linkedin.media]
enabled = true
max_image_size_kb = 5000
max_video_size_mb = 200
supported_types = ["image/jpeg", "image/png", "image/gif", "video/mp4"]

[enrichment.crossref]
enabled = true
etiquette_email = "test@example.com"
cache_ttl_days = 7
timeout_seconds = 5

[enrichment.summarization]
enabled = true
backend = "gemini"
max_chars = 500
prompt = "Summarize this academic paper."

[enrichment.summarization.gemini]
timeout_seconds = 30

[enrichment.summarization.ollama]
model = "gemma3:9b"
host = "http://localhost:11434"
timeout_seconds = 30

[enrichment.summarization.extractive]
max_sentences = 5
timeout_seconds = 10

[enrichment.url_unshorten]
enabled = true
timeout_seconds = 10
max_redirects = 5

[[notifications.backends]]
type = "ntfy"
topic = "scholarposter"
server = "https://ntfy.sh"

[logging]
level = "INFO"
file = "scholarposter.log"
rotation = "10 MB"
retention = "30 days"

[state]
state_file = "state.json"
cache_file = "cache.json"
lock_file = "scholarposter.lock"
"""


@pytest.fixture
def minimal_config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(MINIMAL_TOML)
    return p


@pytest.fixture
def full_config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(FULL_TOML)
    return p


class TestLoadConfig:
    def test_minimal_loads(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert isinstance(cfg, ScholarposterConfig)
        assert cfg.mastodon.instance == "https://fediscience.org"

    def test_full_loads(self, full_config_file):
        cfg = load_config(full_config_file)
        assert cfg.mastodon.credentials_file == "pytooter_usercred.secret"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config(Path("/nonexistent/config.toml"))

    def test_missing_mastodon_section_raises(self, tmp_path):
        p = tmp_path / "bad.toml"
        p.write_text("[logging]\nlevel = 'INFO'\n")
        with pytest.raises((ValueError, KeyError)):
            load_config(p)


class TestDefaults:
    def test_filter_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        bluesky = cfg.platforms.get("bluesky")
        if bluesky:
            assert isinstance(bluesky.filters, FilterConfig)
            assert bluesky.filters.skip_hashtags == []
            assert bluesky.filters.require_hashtags == []
        # Defaults should be present even if not configured

    def test_logging_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert cfg.logging.level == "INFO"

    def test_state_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert cfg.state.state_file == "state.json"

    def test_enrichment_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert isinstance(cfg.enrichment, EnrichmentConfig)


class TestFullConfig:
    def test_bluesky_platform(self, full_config_file):
        cfg = load_config(full_config_file)
        assert "bluesky" in cfg.platforms
        bluesky = cfg.platforms["bluesky"]
        assert bluesky.enabled is True
        assert "nobridge" in bluesky.filters.skip_hashtags
        assert bluesky.media.max_image_size_kb == 950

    def test_linkedin_platform(self, full_config_file):
        cfg = load_config(full_config_file)
        assert "linkedin" in cfg.platforms
        li = cfg.platforms["linkedin"]
        assert "shitpost" in li.filters.skip_hashtags
        assert "poll" in li.filters.skip_content_types

    def test_summarization_config(self, full_config_file):
        cfg = load_config(full_config_file)
        s = cfg.enrichment.summarization
        assert s.enabled is True
        assert s.backend == "gemini"
        assert s.gemini.timeout_seconds == 30
        assert s.ollama.model == "gemma3:9b"

    def test_notifications_config(self, full_config_file):
        cfg = load_config(full_config_file)
        assert len(cfg.notifications.backends) == 1
        assert cfg.notifications.backends[0].type == "ntfy"
        assert cfg.notifications.backends[0].topic == "scholarposter"

    def test_crossref_config(self, full_config_file):
        cfg = load_config(full_config_file)
        assert cfg.enrichment.crossref.etiquette_email == "test@example.com"
        assert cfg.enrichment.crossref.cache_ttl_days == 7


class TestHashtagRulesConfig:
    def test_hashtag_rules_parsed(self, tmp_path):
        toml = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "test.secret"

[platforms.bluesky]
enabled = true

[[platforms.bluesky.hashtag_rules]]
add_hashtag = "EconSky"
if_any_hashtag = ["Economics", "GameTheory", "Labor", "Market"]

[[platforms.bluesky.hashtag_rules]]
add_hashtag = "AcademicSky"
if_any_hashtag = ["Research", "Science", "Academia"]
"""
        p = tmp_path / "config.toml"
        p.write_text(toml)
        from scholarposter.config import load_config
        cfg = load_config(p)
        rules = cfg.platforms["bluesky"].hashtag_rules
        assert len(rules) == 2
        assert rules[0].add_hashtag == "EconSky"
        assert "Economics" in rules[0].if_any_hashtag
        assert "GameTheory" in rules[0].if_any_hashtag
        assert rules[1].add_hashtag == "AcademicSky"

    def test_no_hashtag_rules_defaults_to_empty(self, minimal_config_file):
        from scholarposter.config import load_config
        cfg = load_config(minimal_config_file)
        # Even without bluesky platform, default PlatformConfig has empty rules
        for plat_cfg in cfg.platforms.values():
            assert plat_cfg.hashtag_rules == []


class TestPlatformConfigValidation:
    def test_empty_dict_succeeds(self):
        pc = PlatformConfig.model_validate({})
        assert pc.enabled is True
        assert isinstance(pc.filters, FilterConfig)

    def test_enabled_only_succeeds_without_filters_key(self):
        pc = PlatformConfig.model_validate({"enabled": True})
        assert pc.enabled is True
        assert isinstance(pc.filters, FilterConfig)
        assert pc.filters.skip_hashtags == []


class TestSummarizationConfigValidation:
    def test_invalid_backend_raises_validation_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SummarizationConfig(backend="typo")

    def test_valid_backend_gemini(self):
        s = SummarizationConfig(backend="gemini")
        assert s.backend == "gemini"

    def test_valid_backend_ollama(self):
        s = SummarizationConfig(backend="ollama")
        assert s.backend == "ollama"

    def test_valid_backend_extractive(self):
        s = SummarizationConfig(backend="extractive")
        assert s.backend == "extractive"
