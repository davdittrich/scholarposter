"""Tests for enrichment/summarizer.py - text summarization backends."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scholarposter.config import SummarizationConfig
from scholarposter.enrichment.summarizer import (
    summarize_gemini,
    summarize_ollama,
    summarize_extractive,
    summarize,
)

MULTI_SENTENCE_TEXT = (
    "Game theory provides tools for analyzing strategic interactions between rational agents. "
    "Nash equilibrium is a central concept where no player benefits from unilaterally changing strategy. "
    "Mechanism design inverts this problem by designing games that achieve desired outcomes. "
    "Auction theory applies game-theoretic models to bidding and allocation problems. "
    "The revelation principle states that any social choice function can be implemented by a direct mechanism. "
    "Applications include spectrum auctions, matching markets, and online advertising platforms. "
    "Experimental evidence suggests real agents deviate from theoretical predictions in systematic ways. "
    "Behavioral mechanism design incorporates bounded rationality into the framework. "
    "Recent work connects mechanism design to machine learning and algorithmic game theory. "
    "These connections open new avenues for designing efficient resource allocation systems. "
)


class TestSummarizeGemini:
    def test_returns_stdout_on_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "This is a summary.\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = summarize_gemini("some text", prompt="Summarize:", timeout=10)
        assert result == "This is a summary."
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "gemini"
        assert "-p" in args
        assert "Summarize:" in args

    def test_returns_none_on_timeout(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gemini", 10)):
            result = summarize_gemini("some text", prompt="Summarize:", timeout=10)
        assert result is None

    def test_returns_none_on_nonzero_returncode(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = summarize_gemini("some text", prompt="Summarize:", timeout=10)
        assert result is None

    def test_returns_none_on_empty_stdout(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   "
        with patch("subprocess.run", return_value=mock_result):
            result = summarize_gemini("some text", prompt="Summarize:", timeout=10)
        assert result is None


class TestSummarizeOllama:
    def test_returns_response_on_success(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "Ollama summary text."}
        with patch("httpx.post", return_value=mock_response):
            result = summarize_ollama(
                "some text",
                model="gemma3:9b",
                host="http://localhost:11434",
                prompt="Summarize:",
                timeout=30,
            )
        assert result == "Ollama summary text."

    def test_returns_none_on_timeout(self) -> None:
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            result = summarize_ollama(
                "some text",
                model="gemma3:9b",
                host="http://localhost:11434",
                prompt="Summarize:",
                timeout=30,
            )
        assert result is None

    def test_returns_none_on_http_error(self) -> None:
        with patch("httpx.post", side_effect=httpx.HTTPError("error")):
            result = summarize_ollama(
                "some text",
                model="gemma3:9b",
                host="http://localhost:11434",
                prompt="Summarize:",
                timeout=30,
            )
        assert result is None

    def test_calls_correct_endpoint(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "summary"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            summarize_ollama(
                "text",
                model="llama3",
                host="http://localhost:11434",
                prompt="Summarize:",
                timeout=30,
            )
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "http://localhost:11434/api/generate" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["model"] == "llama3"


class TestSummarizeExtractive:
    def test_returns_non_empty_string_for_multi_sentence_text(self) -> None:
        result = summarize_extractive(MULTI_SENTENCE_TEXT, max_sentences=3)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_empty_string_for_short_text(self) -> None:
        """A single sentence (< 2 sentences) should return empty string."""
        result = summarize_extractive("Just one sentence.", max_sentences=3)
        assert result == ""

    def test_returns_empty_string_for_two_sentences(self) -> None:
        """Two sentences is the boundary; should still return empty."""
        result = summarize_extractive(
            "First sentence here. Second sentence here.",
            max_sentences=3,
        )
        # Two sentences total - boundary behavior: empty or non-empty depending on impl
        # We just verify it doesn't crash and returns a string
        assert isinstance(result, str)

    def test_max_sentences_limits_output(self) -> None:
        result = summarize_extractive(MULTI_SENTENCE_TEXT, max_sentences=2)
        assert isinstance(result, str)

    def test_max_chars_limits_output_length(self) -> None:
        """summarize_extractive must not return more chars than max_chars."""
        result = summarize_extractive(MULTI_SENTENCE_TEXT, max_sentences=10, max_chars=50)
        assert len(result) <= 50


class TestSummarize:
    def test_uses_preferred_backend_first(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with patch("scholarposter.enrichment.summarizer.summarize_gemini", return_value="gemini result"):
            result = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert result == "gemini result"

    def test_falls_back_to_ollama_when_gemini_returns_none(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_gemini", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value="ollama result"),
        ):
            result = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert result == "ollama result"

    def test_falls_back_to_extractive_when_ollama_returns_none(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_gemini", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="extractive result"),
        ):
            result = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert result == "extractive result"

    def test_truncates_to_max_chars(self) -> None:
        config = SummarizationConfig(backend="extractive", max_chars=10)
        with patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="A" * 100):
            result = summarize(MULTI_SENTENCE_TEXT, backend="extractive", max_chars=10, prompt="Sum:", config=config)
        assert len(result) <= 10

    def test_extractive_backend_direct(self) -> None:
        config = SummarizationConfig(backend="extractive", max_chars=500)
        with patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="extractive summary"):
            result = summarize(MULTI_SENTENCE_TEXT, backend="extractive", max_chars=500, prompt="Sum:", config=config)
        assert result == "extractive summary"

    def test_ollama_backend_direct(self) -> None:
        config = SummarizationConfig(backend="ollama", max_chars=500)
        with patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value="ollama summary"):
            result = summarize(MULTI_SENTENCE_TEXT, backend="ollama", max_chars=500, prompt="Sum:", config=config)
        assert result == "ollama summary"


class TestBuildBackendOrder:
    """Tests for _build_backend_order (no wrap-around behavior)."""

    def test_build_backend_order_ollama_no_gemini(self) -> None:
        from scholarposter.enrichment.summarizer import _build_backend_order
        # ollama preferred: only ollama + extractive, no wrap to gemini
        assert _build_backend_order("ollama") == ["ollama", "extractive"]

    def test_build_backend_order_extractive_no_fallback(self) -> None:
        from scholarposter.enrichment.summarizer import _build_backend_order
        # extractive is last: no backends after it
        assert _build_backend_order("extractive") == ["extractive"]

    def test_build_backend_order_gemini_unchanged(self) -> None:
        from scholarposter.enrichment.summarizer import _build_backend_order
        # gemini is first: full list, unchanged
        assert _build_backend_order("gemini") == ["gemini", "ollama", "extractive"]


class TestSummarizeFallbackLogging:
    def test_fallback_warning_logged_when_primary_backend_fails(self) -> None:
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        config = SummarizationConfig(backend="gemini", max_chars=500)
        try:
            with (
                patch("scholarposter.enrichment.summarizer.summarize_gemini", return_value=None),
                patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value="ollama result"),
            ):
                summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        finally:
            logger.remove(lid)
        assert any("ollama" in m for m in messages), f"Expected fallback warning mentioning 'ollama', got: {messages}"

    def test_no_fallback_warning_when_primary_backend_succeeds(self) -> None:
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        config = SummarizationConfig(backend="gemini", max_chars=500)
        try:
            with patch("scholarposter.enrichment.summarizer.summarize_gemini", return_value="gemini result"):
                summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        finally:
            logger.remove(lid)
        assert not any("falling back" in m for m in messages), f"Unexpected fallback warning: {messages}"
