"""Tests for tldr_scholar.backends."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from tldr_scholar.backends import get_backend, run_with_fallback
from tldr_scholar.backends.extractive import ExtractiveBackend
from tldr_scholar.backends.lemonade import LemonadeBackend, _MODEL_NAME_RE


SAMPLE_TEXT = (
    "Machine learning has transformed natural language processing. "
    "Transformer architectures enable contextual understanding of text. "
    "Recent advances in large language models demonstrate emergent capabilities. "
    "These models can summarize, translate, and generate human-like text."
)


class TestGetBackend:
    def test_valid_backends(self):
        for name in ["gemini", "lemonade", "ollama", "extractive"]:
            b = get_backend(name)
            assert b is not None

    def test_invalid_backend(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("invalid")


class TestExtractiveBackend:
    def test_produces_summary(self):
        b = ExtractiveBackend()
        result = b.summarize(SAMPLE_TEXT, max_chars=200, focus="", hashtag_instruction="")
        assert result is not None
        assert len(result) > 0

    def test_focus_keyword_biasing(self):
        b = ExtractiveBackend()
        focused = b.summarize(SAMPLE_TEXT, max_chars=500, focus="transformer", hashtag_instruction="")
        unfocused = b.summarize(SAMPLE_TEXT, max_chars=500, focus="", hashtag_instruction="")
        # Both produce output; focus may reorder sentences
        assert focused is not None
        assert unfocused is not None

    def test_ignores_hashtag_instruction(self):
        b = ExtractiveBackend()
        result = b.summarize(SAMPLE_TEXT, max_chars=200, focus="", hashtag_instruction="Generate 5 hashtags")
        assert result is not None
        assert "#" not in result  # extractive doesn't generate hashtags


class TestLemonadeBackend:
    def test_model_name_validation(self):
        assert _MODEL_NAME_RE.match("Phi-4-mini-instruct-GGUF")
        assert _MODEL_NAME_RE.match("user.DeepSeek-R1-GGUF")
        assert not _MODEL_NAME_RE.match("model; rm -rf /")
        assert not _MODEL_NAME_RE.match("")

    @respx.mock
    def test_returns_content_on_success(self):
        respx.post("http://127.0.0.1:8000/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "choices": [{"message": {"content": "A summary.\n\n#ai #ml"}}]
            })
        )
        b = LemonadeBackend({"model": "test-model", "host": "http://127.0.0.1:8000"})
        result = b.summarize("text", 500, "insights", "")
        assert "summary" in result

    @respx.mock
    def test_returns_none_on_failure(self):
        respx.post("http://127.0.0.1:8000/v1/chat/completions").mock(
            return_value=httpx.Response(500, text="error")
        )
        b = LemonadeBackend({"model": "test-model", "host": "http://127.0.0.1:8000"})
        result = b.summarize("text", 500, "insights", "")
        assert result is None


class TestRunWithFallback:
    def test_auto_first_success_wins(self):
        with patch("tldr_scholar.backends.get_backend") as mock_get:
            mock_b = MagicMock()
            mock_b.summarize.return_value = "gemini result"
            mock_get.return_value = mock_b
            result, backend_used = run_with_fallback("text", 500, "", "", "auto")
        assert result == "gemini result"
        assert backend_used == "gemini"

    def test_auto_fallback_on_failure(self):
        call_count = [0]
        def _get_backend(name, config=None):
            call_count[0] += 1
            mock_b = MagicMock()
            if name == "gemini":
                mock_b.summarize.return_value = None
            elif name == "lemonade":
                mock_b.summarize.return_value = "lemonade result"
            else:
                mock_b.summarize.return_value = None
            return mock_b

        with patch("tldr_scholar.backends.get_backend", side_effect=_get_backend):
            result, backend_used = run_with_fallback("text", 500, "", "", "auto")
        assert result == "lemonade result"
        assert backend_used == "lemonade"

    def test_explicit_backend_no_fallback(self):
        with patch("tldr_scholar.backends.get_backend") as mock_get:
            mock_b = MagicMock()
            mock_b.summarize.return_value = None
            mock_get.return_value = mock_b
            result, _ = run_with_fallback("text", 500, "", "", "gemini")
        assert result is None

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError):
            run_with_fallback("text", 500, "", "", "nonexistent")

    def test_document_delimiters_in_prompt(self):
        """FR-16: shared prompt template contains <document> delimiters."""
        from tldr_scholar.backends.base import SUMMARY_PROMPT_TEMPLATE
        assert "<document>" in SUMMARY_PROMPT_TEMPLATE
        assert "</document>" in SUMMARY_PROMPT_TEMPLATE
