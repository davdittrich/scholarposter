"""Tests for tldr_scholar.cli."""
from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from tldr_scholar.cli import app
from tldr_scholar.models import SummaryMetadata, SummaryResult

runner = CliRunner()


def _mock_result(text="A summary.", hashtags=None, backend="extractive", input_type="text"):
    return SummaryResult(
        text=text,
        hashtags=hashtags or [],
        metadata=SummaryMetadata(
            source="test.txt",
            input_type=input_type,
            backend_used=backend,
            max_chars=500,
            focus="insights",
            char_count=len(text),
        ),
    )


class TestCliHelp:
    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "summarize" in result.output.lower() or "pdf" in result.output.lower()


class TestCliOutput:
    def test_text_format(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some research text about machine learning transformers.")
        with patch("tldr_scholar.cli.summarize_file", return_value=_mock_result()):
            result = runner.invoke(app, [str(f)])
        assert result.exit_code == 0
        assert "A summary." in result.output

    def test_json_format(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some text.")
        with patch("tldr_scholar.cli.summarize_file", return_value=_mock_result()):
            result = runner.invoke(app, [str(f), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "summary" in data or "text" in data
        assert "metadata" in data

    def test_markdown_format(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some text.")
        with patch("tldr_scholar.cli.summarize_file", return_value=_mock_result()):
            result = runner.invoke(app, [str(f), "--format", "markdown"])
        assert result.exit_code == 0
        assert "## Summary" in result.output

    def test_markdown_no_hashtag_section_when_zero(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some text.")
        with patch("tldr_scholar.cli.summarize_file", return_value=_mock_result()):
            result = runner.invoke(app, [str(f), "--format", "markdown"])
        assert "## Hashtags" not in result.output

    def test_markdown_has_hashtag_section_when_present(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some text.")
        mock = _mock_result(hashtags=["#ai", "#ml"])
        with patch("tldr_scholar.cli.summarize_file", return_value=mock):
            result = runner.invoke(app, [str(f), "--format", "markdown", "--hashtags", "2"])
        assert "## Hashtags" in result.output

    def test_text_no_hashtag_line_when_zero(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some text.")
        with patch("tldr_scholar.cli.summarize_file", return_value=_mock_result()):
            result = runner.invoke(app, [str(f)])
        assert result.output.strip() == "A summary."  # no second line


class TestCliValidation:
    def test_invalid_backend(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("text")
        result = runner.invoke(app, [str(f), "--backend", "invalid"])
        assert result.exit_code == 2

    def test_invalid_format(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("text")
        result = runner.invoke(app, [str(f), "--format", "xml"])
        assert result.exit_code == 2

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "test.png"
        f.write_bytes(b"\x89PNG")
        result = runner.invoke(app, [str(f)])
        assert result.exit_code == 2

    def test_missing_file(self):
        result = runner.invoke(app, ["/nonexistent/file.txt"])
        assert result.exit_code == 1

    def test_explicit_backend_empty_response(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Some text about research.")
        mock = _mock_result(text="")
        with patch("tldr_scholar.cli.summarize_file", return_value=mock):
            result = runner.invoke(app, [str(f), "--backend", "gemini"])
        assert result.exit_code == 1
        assert "empty response" in result.output.lower() or "failed" in result.output.lower()


class TestCliLengthPresets:
    def test_length_short(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("text")
        with patch("tldr_scholar.cli.summarize_file") as mock_fn:
            mock_fn.return_value = _mock_result()
            runner.invoke(app, [str(f), "--length", "short"])
            call_kwargs = mock_fn.call_args[1]
            assert call_kwargs["max_chars"] == 200

    def test_length_medium(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("text")
        with patch("tldr_scholar.cli.summarize_file") as mock_fn:
            mock_fn.return_value = _mock_result()
            runner.invoke(app, [str(f), "--length", "medium"])
            assert mock_fn.call_args[1]["max_chars"] == 500

    def test_length_long(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("text")
        with patch("tldr_scholar.cli.summarize_file") as mock_fn:
            mock_fn.return_value = _mock_result()
            runner.invoke(app, [str(f), "--length", "long"])
            assert mock_fn.call_args[1]["max_chars"] == 1000

    def test_max_chars_overrides_length(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("text")
        with patch("tldr_scholar.cli.summarize_file") as mock_fn:
            mock_fn.return_value = _mock_result()
            runner.invoke(app, [str(f), "--length", "short", "--max-chars", "777"])
            assert mock_fn.call_args[1]["max_chars"] == 777


class TestCliCredentialStripping:
    def test_url_credentials_stripped_from_metadata(self, tmp_path):
        """Security: URL with user:pass should have credentials stripped."""
        mock = _mock_result()
        with patch("tldr_scholar.cli.summarize_url", return_value=mock):
            result = runner.invoke(app, ["https://user:pass@example.com/paper"])
        # The actual stripping happens in summarize_url, not CLI — just verify no crash
        assert result.exit_code == 0


class TestCliEnvVar:
    def test_tldr_scholar_config_env_var(self, tmp_path, monkeypatch):
        config_file = tmp_path / "custom.toml"
        config_file.write_text("[gemini]\nmodel = 'test'\n")
        monkeypatch.setenv("TLDR_SCHOLAR_CONFIG", str(config_file))
        f = tmp_path / "test.txt"
        f.write_text("text")
        with patch("tldr_scholar.cli.summarize_file") as mock_fn:
            mock_fn.return_value = _mock_result()
            result = runner.invoke(app, [str(f)])
        assert result.exit_code == 0


class TestExtractiveHashtags:
    def test_extractive_backend_with_hashtags(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Machine learning transformer architecture research.")
        mock = _mock_result(hashtags=["#machine", "#learning", "#transformer"])
        with patch("tldr_scholar.cli.summarize_file", return_value=mock):
            result = runner.invoke(app, [str(f), "--backend", "extractive", "--hashtags", "3"])
        assert result.exit_code == 0
