"""Tests for config-update CLI command (US-017) — T-31 through T-44."""
from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from scholarposter.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Controlled example TOML fixture (isolated from real shipped file)
# ---------------------------------------------------------------------------

_EXAMPLE_RAW = """\
[mastodon]
instance = "https://mastodon.social"
credentials_file = ""

[logging]
level = "INFO"
rotation = "10 MB"

[audit]
enabled = false
file = "audit.jsonl"

[discovery]
enabled = false
limit = 20
"""


def _patch_example(raw=_EXAMPLE_RAW):
    parsed = tomllib.loads(raw)
    return patch("scholarposter.cli._load_example_config", return_value=(raw, parsed))


def _cfg(tmp_path: Path, content: str, name: str = "config.toml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# T-31: Missing section appended at EOF
# ---------------------------------------------------------------------------

class TestT31MissingSection:
    def test_missing_audit_appended(self, tmp_path):
        """T-31: [audit] absent → appended at EOF with sentinel + # [audit] + key lines."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
            '\n'
            '[discovery]\n'
            'enabled = false\n'
            'limit = 20\n'
        ))
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        # Original untouched
        assert '[mastodon]\ninstance' in content
        # Audit block appended
        assert "# --- config-update: audit ---" in content
        assert "# Added by scholarposter config-update" in content
        assert "# [audit]" in content
        assert "# enabled = false" in content
        assert "# file = " in content


# ---------------------------------------------------------------------------
# T-32: Missing leaf key → key lines only, no # [section] header
# ---------------------------------------------------------------------------

class TestT32MissingLeafKey:
    def test_missing_leaf_no_section_header(self, tmp_path):
        """T-32: logging.rotation absent → appended without # [logging] header."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            # rotation missing
            '\n'
            '[audit]\n'
            'enabled = false\n'
            'file = "audit.jsonl"\n'
            '\n'
            '[discovery]\n'
            'enabled = false\n'
            'limit = 20\n'
        ))
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert "# --- config-update: logging ---" in content
        assert '# rotation = "10 MB"' in content
        # [logging] must appear exactly once (user's real header; no duplicate)
        assert content.count("[logging]") == 1
        assert "# [logging]" not in content


# ---------------------------------------------------------------------------
# T-33: Fully up-to-date
# ---------------------------------------------------------------------------

class TestT33UpToDate:
    def test_no_changes_when_up_to_date(self, tmp_path):
        """T-33: All keys present → 'up to date' message, file byte-identical."""
        cfg = _cfg(tmp_path, _EXAMPLE_RAW)
        original = cfg.read_bytes()
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "up to date" in result.output
        assert cfg.read_bytes() == original


# ---------------------------------------------------------------------------
# T-34: --dry-run does not write
# ---------------------------------------------------------------------------

class TestT34DryRun:
    def test_dry_run_prints_and_does_not_write(self, tmp_path):
        """T-34: --dry-run prints additions without writing."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
        ))
        original = cfg.read_bytes()
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg), "--dry-run"])
        assert result.exit_code == 0
        assert "# --- config-update:" in result.output
        assert cfg.read_bytes() == original


# ---------------------------------------------------------------------------
# T-35: --diff shows unified diff; --dry-run --diff ≡ --diff
# ---------------------------------------------------------------------------

class TestT35Diff:
    def test_diff_shows_markers_no_write(self, tmp_path):
        """T-35: --diff shows unified diff without writing."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
        ))
        original = cfg.read_bytes()
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg), "--diff"])
        assert result.exit_code == 0
        assert "+++" in result.output or "---" in result.output
        assert cfg.read_bytes() == original

    def test_dry_run_diff_equivalent(self, tmp_path):
        """T-35: --dry-run --diff behaves like --diff (no write, same markers)."""
        base = (
            '[mastodon]\n'
            'instance = "x"\n'
            'credentials_file = ""\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
        )
        cfg1 = _cfg(tmp_path, base, "a.toml")
        cfg2 = _cfg(tmp_path, base, "b.toml")
        orig1, orig2 = cfg1.read_bytes(), cfg2.read_bytes()
        with _patch_example():
            r1 = runner.invoke(app, ["config-update", "--config", str(cfg1), "--diff"])
            r2 = runner.invoke(app, ["config-update", "--config", str(cfg2), "--dry-run", "--diff"])
        assert r1.exit_code == r2.exit_code == 0
        assert cfg1.read_bytes() == orig1
        assert cfg2.read_bytes() == orig2
        assert "+++" in r1.output
        assert "+++" in r2.output
        assert r1.output == r2.output


# ---------------------------------------------------------------------------
# T-36: Idempotency — second run is a no-op
# ---------------------------------------------------------------------------

class TestT36Idempotency:
    def test_second_run_noop(self, tmp_path):
        """T-36: Second run detects prior appended lines and reports up-to-date."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
        ))
        with _patch_example():
            runner.invoke(app, ["config-update", "--config", str(cfg)])
            after_first = cfg.read_bytes()
            result2 = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result2.exit_code == 0
        assert "up to date" in result2.output
        assert cfg.read_bytes() == after_first


# ---------------------------------------------------------------------------
# T-37: Commented-out section header treated as absent
# ---------------------------------------------------------------------------

class TestT37CommentedSection:
    def test_commented_section_treated_as_absent(self, tmp_path):
        """T-37: # [discovery] in file → discovery treated as absent → block appended."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
            '\n'
            '[audit]\n'
            'enabled = false\n'
            'file = "audit.jsonl"\n'
            '\n'
            '# [discovery]\n'
            '# enabled = false\n'
            '# limit = 20\n'
        ))
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert "# --- config-update: discovery ---" in content
        assert "# [discovery]" in content


# ---------------------------------------------------------------------------
# T-38: User value differs from example default → preserved
# ---------------------------------------------------------------------------

class TestT38UserValuePreserved:
    def test_user_value_not_overwritten(self, tmp_path):
        """T-38: User's level = "DEBUG" differs from example "INFO" → preserved."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "DEBUG"\n'
            'rotation = "10 MB"\n'
            '\n'
            '[audit]\n'
            'enabled = false\n'
            'file = "audit.jsonl"\n'
            '\n'
            '[discovery]\n'
            'enabled = false\n'
            'limit = 20\n'
        ))
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        assert 'level = "DEBUG"' in cfg.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T-39: Missing config.example.toml → exit 1
# ---------------------------------------------------------------------------

class TestT39MissingExample:
    def test_missing_example_exits_1(self, tmp_path):
        """T-39: config.example.toml not found → exits 1 with 'reinstall' message."""
        cfg = _cfg(tmp_path, '[mastodon]\ninstance = "x"\ncredentials_file = ""\n')
        with patch("scholarposter.cli._load_example_config", side_effect=FileNotFoundError):
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "reinstall" in result.output


# ---------------------------------------------------------------------------
# T-40: Inline comments not corrupted
# ---------------------------------------------------------------------------

class TestT40InlineComments:
    def test_inline_comments_preserved_valid_toml(self, tmp_path):
        """T-40: Keys with inline comments → append leaves file valid and comments intact."""
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"  # primary instance\n'
            'credentials_file = "cred.secret"\n'
            '\n'
            '[logging]\n'
            'level = "INFO"  # custom log level\n'
            'rotation = "10 MB"\n'
        ))
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert "# primary instance" in content
        assert "# custom log level" in content
        tomllib.loads(content)  # must remain valid TOML


# ---------------------------------------------------------------------------
# T-41: CI round-trip against real shipped config.example.toml
# ---------------------------------------------------------------------------

class TestT41RoundTrip:
    def test_real_example_passes_validation(self):
        """T-41: Real shipped config.example.toml passes ScholarposterConfig round-trip."""
        import importlib.resources
        from scholarposter.config import ScholarposterConfig
        data = (
            importlib.resources.files("scholarposter.data")
            .joinpath("config.example.toml")
            .read_bytes()
        )
        parsed = tomllib.loads(data.decode())
        ScholarposterConfig.model_validate(parsed)  # must not raise


# ---------------------------------------------------------------------------
# T-42: Cross-version idempotency — previously appended key not duplicated
# ---------------------------------------------------------------------------

class TestT42CrossVersionIdempotency:
    def test_previously_appended_key_not_duplicated(self, tmp_path):
        """T-42: key_a appended by v1.2.0; v1.3.0 no new keys → no-op."""
        example_raw = (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = ""\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
        )
        # logging.level was appended as a comment by v1.2.0 — not yet uncommented
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = ""\n'
            '\n'
            '# --- config-update: logging ---\n'
            '# Added by scholarposter config-update 1.2.0 — logging\n'
            '# [logging]\n'
            '# level = "INFO"\n'
        ))
        with patch("scholarposter.cli._load_example_config",
                   return_value=(example_raw, tomllib.loads(example_raw))):
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "up to date" in result.output


# ---------------------------------------------------------------------------
# T-43: Cross-version new key — only new key appended
# ---------------------------------------------------------------------------

class TestT43CrossVersionNewKey:
    def test_new_key_appended_old_key_not_duplicated(self, tmp_path):
        """T-43: key_a from v1.2.0 stays; v1.3.0 new key_b appended once."""
        example_raw = (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = ""\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'  # new in v1.3.0
        )
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = ""\n'
            '\n'
            '# --- config-update: logging ---\n'
            '# Added by scholarposter config-update 1.2.0 — logging\n'
            '# [logging]\n'
            '# level = "INFO"\n'
        ))
        with patch("scholarposter.cli._load_example_config",
                   return_value=(example_raw, tomllib.loads(example_raw))):
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert '# rotation = "10 MB"' in content
        assert content.count('# level = "INFO"') == 1


# ---------------------------------------------------------------------------
# T-44: Sensitive value redaction in --dry-run
# ---------------------------------------------------------------------------

class TestT44SensitiveRedaction:
    def test_smtp_password_redacted_in_dry_run(self, tmp_path):
        """T-44: smtp_password in example → --dry-run shows # smtp_password = \"<redacted>\"."""
        example_raw = (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = ""\n'
            '\n'
            '[mail]\n'
            'smtp_password = ""\n'
        )
        cfg = _cfg(tmp_path, '[mastodon]\ninstance = "https://mastodon.social"\ncredentials_file = ""\n')
        with patch("scholarposter.cli._load_example_config",
                   return_value=(example_raw, tomllib.loads(example_raw))):
            result = runner.invoke(app, ["config-update", "--config", str(cfg), "--dry-run"])
        assert result.exit_code == 0
        assert '# smtp_password = "<redacted>"' in result.output


# ---------------------------------------------------------------------------
# Regression: cross-section false positive on shared key names
# ---------------------------------------------------------------------------

class TestCrossSectionFalsePositive:
    def test_shared_key_name_not_suppressed_across_sections(self, tmp_path):
        """Regression: 'enabled' commented in section A must not suppress section B's 'enabled'."""
        # audit was appended with # enabled = false
        # discovery is entirely absent and also needs 'enabled'
        cfg = _cfg(tmp_path, (
            '[mastodon]\n'
            'instance = "https://mastodon.social"\n'
            'credentials_file = ""\n'
            '\n'
            '[logging]\n'
            'level = "INFO"\n'
            'rotation = "10 MB"\n'
            '\n'
            '# --- config-update: audit ---\n'
            '# Added by scholarposter config-update 1.0.0 — audit\n'
            '# [audit]\n'
            '# enabled = false\n'
            '# file = "audit.jsonl"\n'
        ))
        with _patch_example():
            result = runner.invoke(app, ["config-update", "--config", str(cfg)])
        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        # discovery must still be appended (its 'enabled' is separate from audit's)
        assert "# --- config-update: discovery ---" in content
