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
    ProgressiveEnrichmentConfig,
    AuditConfig,
    DiscoveryConfig,
    DiscoveryRankingConfig,
    ConfigError,
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

[platforms.linkedin]
enabled = true

[platforms.linkedin.filters]
skip_hashtags = ["nobridge", "shitpost"]
skip_content_types = ["sensitive", "poll"]
require_hashtags = []

[platforms.linkedin.media]
enabled = true
max_image_size_kb = 5000

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
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
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


# ─── WU-1: New Config Models ─────────────────────────────────────────────────

PROGRESSIVE_TOML = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[enrichment.progressive]
enabled = false
"""

AUDIT_TOML = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[audit]
enabled = true
file = "my_audit.jsonl"
min_report_sample = 5
rotation_max_mb = 100
retention_days = 180
"""

DISCOVERY_TOML = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[discovery]
enabled = true
sources = ["openalex"]
modes = ["cited-by"]
limit = 50
digest_email = "user@example.com"
digest_auto = true
cache_ttl_hours = 48

[discovery.ranking]
oa_weight = 2.0
recency_half_life_years = 3.0
"""

EXTRA_KEYS_TOML = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[enrichment.progressive]
enabled = true
unknown_future_key = "should be ignored"

[audit]
enabled = false
unknown_field = 42

[discovery]
enabled = false
future_feature = "ignored"
"""


class TestProgressiveEnrichmentConfig:
    def test_defaults(self):
        cfg = ProgressiveEnrichmentConfig()
        assert cfg.enabled is True

    def test_toml_with_progressive_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(PROGRESSIVE_TOML)
        cfg = load_config(p)
        assert cfg.enrichment.progressive.enabled is False

    def test_toml_without_progressive_section_uses_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert cfg.enrichment.progressive.enabled is True

    def test_extra_keys_silently_ignored(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(EXTRA_KEYS_TOML)
        cfg = load_config(p)
        assert cfg.enrichment.progressive.enabled is True  # default since not set in EXTRA_KEYS_TOML


class TestAuditConfig:
    def test_defaults(self):
        cfg = AuditConfig()
        assert cfg.enabled is False
        assert cfg.file == "audit.jsonl"
        assert cfg.min_report_sample == 3
        assert cfg.rotation_max_mb == 50
        assert cfg.retention_days == 365

    def test_toml_with_audit_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(AUDIT_TOML)
        cfg = load_config(p)
        assert cfg.audit.enabled is True
        assert cfg.audit.min_report_sample == 5
        assert cfg.audit.rotation_max_mb == 100
        assert cfg.audit.retention_days == 180

    def test_toml_without_audit_section_uses_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert cfg.audit.enabled is False
        assert cfg.audit.file == "audit.jsonl"

    def test_extra_keys_silently_ignored(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(EXTRA_KEYS_TOML)
        cfg = load_config(p)
        assert cfg.audit.enabled is False  # default

    def test_audit_file_resolves_relative_to_config_parent(self, tmp_path):
        """Relative audit.file resolves to config parent directory."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        p = subdir / "config.toml"
        p.write_text(AUDIT_TOML)
        cfg = load_config(p)
        expected = subdir.resolve() / "my_audit.jsonl"
        assert cfg.audit.resolved_file == expected

    def test_audit_file_default_resolves_to_config_parent(self, tmp_path):
        """Default audit.file='audit.jsonl' resolves relative to config parent."""
        p = tmp_path / "config.toml"
        p.write_text(MINIMAL_TOML)
        cfg = load_config(p)
        expected = tmp_path.resolve() / "audit.jsonl"
        assert cfg.audit.resolved_file == expected

    def test_audit_absolute_file_path_unchanged(self, tmp_path):
        """Absolute audit.file path is kept as-is."""
        toml = f"""
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[audit]
file = "/var/log/audit.jsonl"
"""
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(p)
        assert cfg.audit.resolved_file == Path("/var/log/audit.jsonl")


class TestDiscoveryConfig:
    def test_defaults(self):
        cfg = DiscoveryConfig()
        assert cfg.enabled is False
        assert cfg.sources == ["openalex"]
        assert cfg.modes == ["cited-by", "cites"]
        assert cfg.limit == 20
        assert cfg.digest_email is None
        assert cfg.digest_auto is False
        assert cfg.cache_ttl_hours == 24
        assert cfg.ranking.oa_weight == 1.2
        assert cfg.ranking.recency_half_life_years == 2.0

    def test_toml_with_discovery_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(DISCOVERY_TOML)
        cfg = load_config(p)
        assert cfg.discovery.enabled is True
        assert cfg.discovery.modes == ["cited-by"]
        assert cfg.discovery.limit == 50
        assert cfg.discovery.digest_email == "user@example.com"
        assert cfg.discovery.digest_auto is True
        assert cfg.discovery.cache_ttl_hours == 48
        assert cfg.discovery.ranking.oa_weight == 2.0
        assert cfg.discovery.ranking.recency_half_life_years == 3.0

    def test_toml_without_discovery_section_uses_defaults(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert cfg.discovery.enabled is False
        assert cfg.discovery.sources == ["openalex"]

    def test_extra_keys_silently_ignored(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(EXTRA_KEYS_TOML)
        cfg = load_config(p)
        assert cfg.discovery.enabled is False  # default


class TestDiscoveryRankingConfig:
    def test_defaults(self):
        cfg = DiscoveryRankingConfig()
        assert cfg.oa_weight == 1.2
        assert cfg.recency_half_life_years == 2.0

    def test_custom_values(self):
        cfg = DiscoveryRankingConfig(oa_weight=3.0, recency_half_life_years=5.0)
        assert cfg.oa_weight == 3.0
        assert cfg.recency_half_life_years == 5.0


class TestDigestEmailValidation:
    def test_valid_email_accepted(self, tmp_path):
        toml = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "test.secret"

[discovery]
digest_email = "user@example.com"
"""
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(p)
        assert cfg.discovery.digest_email == "user@example.com"

    def test_digest_email_with_crlf_raises_config_error(self, tmp_path):
        """digest_email containing \\r\\n triggers ConfigError (SMTP injection prevention)."""
        toml = 'mastodon = {instance = "https://fediscience.org", credentials_file = "x.secret"}\n'
        p = tmp_path / "config.toml"
        p.write_text(toml)
        # Simulate loading a config with an injected digest_email by patching after load
        import tomllib as _tomllib
        data = {
            "mastodon": {"instance": "https://fediscience.org", "credentials_file": "x.secret"},
            "discovery": {"digest_email": "bad\r\nemail@example.com"},
        }
        with pytest.raises(ConfigError, match="newline"):
            ScholarposterConfig.model_validate(data)

    def test_digest_email_with_lf_raises_config_error(self):
        """digest_email containing bare \\n triggers ConfigError."""
        data = {
            "mastodon": {"instance": "https://fediscience.org", "credentials_file": "x.secret"},
            "discovery": {"digest_email": "bad\nemail@example.com"},
        }
        with pytest.raises(ConfigError, match="newline"):
            ScholarposterConfig.model_validate(data)

    def test_none_digest_email_accepted(self, minimal_config_file):
        """No digest_email configured is valid (None)."""
        cfg = load_config(minimal_config_file)
        assert cfg.discovery.digest_email is None


class TestEnrichmentConfigExtended:
    def test_enrichment_has_progressive_field(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert hasattr(cfg.enrichment, "progressive")
        assert isinstance(cfg.enrichment.progressive, ProgressiveEnrichmentConfig)

    def test_full_toml_with_all_new_sections(self, tmp_path):
        toml = """
[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[enrichment.progressive]
enabled = true

[audit]
enabled = true
file = "audit.jsonl"
min_report_sample = 3

[discovery]
enabled = true
limit = 10

[discovery.ranking]
oa_weight = 1.5
"""
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(p)
        assert cfg.enrichment.progressive.enabled is True
        assert cfg.audit.enabled is True
        assert cfg.audit.min_report_sample == 3
        assert cfg.discovery.enabled is True
        assert cfg.discovery.limit == 10
        assert cfg.discovery.ranking.oa_weight == 1.5

    def test_scholarly_config_has_audit_and_discovery(self, minimal_config_file):
        cfg = load_config(minimal_config_file)
        assert hasattr(cfg, "audit")
        assert hasattr(cfg, "discovery")
        assert isinstance(cfg.audit, AuditConfig)
        assert isinstance(cfg.discovery, DiscoveryConfig)
