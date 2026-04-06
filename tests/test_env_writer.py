"""Tests for scholarposter.env_writer"""
import os
import stat
from unittest.mock import patch

import pytest

from scholarposter.env_writer import read_env, write_env


class TestReadEnv:
    def test_reads_key_value_pairs(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('FOO="bar"\nBAZ=qux\n')
        result = read_env(env)
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_quotes(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('KEY="value"\nKEY2=\'value2\'\n')
        result = read_env(env)
        assert result["KEY"] == "value"
        assert result["KEY2"] == "value2"

    def test_skips_comments_and_blanks(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# comment\n\nKEY=val\n")
        result = read_env(env)
        assert result == {"KEY": "val"}

    def test_returns_empty_for_missing_file(self, tmp_path):
        assert read_env(tmp_path / ".env") == {}


class TestWriteEnv:
    def test_writes_and_reads_roundtrip(self, tmp_path):
        env = tmp_path / ".env"
        write_env(env, {"A": "1", "B": "2"})
        result = read_env(env)
        assert result == {"A": "1", "B": "2"}

    def test_preserves_existing_keys(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('EXISTING="keep"\n')
        write_env(env, {"NEW": "added"})
        result = read_env(env)
        assert result["EXISTING"] == "keep"
        assert result["NEW"] == "added"

    def test_updates_existing_keys(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('KEY="old"\n')
        write_env(env, {"KEY": "new"})
        result = read_env(env)
        assert result["KEY"] == "new"

    def test_file_permissions_are_0600(self, tmp_path):
        env = tmp_path / ".env"
        write_env(env, {"KEY": "val"})
        mode = stat.S_IMODE(os.stat(env).st_mode)
        assert mode == 0o600

    def test_atomic_crash_leaves_original_intact(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('ORIGINAL="safe"\n')
        os.chmod(env, 0o600)
        with patch("scholarposter.env_writer.os.rename", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                write_env(env, {"NEW": "val"})
        assert read_env(env) == {"ORIGINAL": "safe"}
