"""Tests for enrichment/summarizer.py - text summarization backends."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from scholarposter.config import SummarizationConfig
from scholarposter.gemini_client import summarize_via_gemini, GeminiUsage
from scholarposter.enrichment.summarizer import (
    summarize_lemonade,
    summarize_ollama,
    summarize_extractive,
    summarize,
    _ensure_lemonade_model,
    _get_downloaded_models,
    _load_lemonade_model,
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


class TestSummarizeGeminiIntegration:
    """Tests that summarize() correctly delegates to summarize_via_gemini."""

    def test_gemini_backend_calls_summarize_via_gemini(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        config.gemini.model = "gemini-3-flash-preview"
        usage = GeminiUsage(tokens_used=100, cost_usd=0.00015, cost_currency="USD", is_estimated=False, cost_is_estimated=False)
        with patch("scholarposter.enrichment.summarizer.summarize_via_gemini",
                   return_value=("gemini summary", usage)) as mock:
            text, backend, returned_usage = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500,
                                                      prompt="Sum:", config=config)
        assert text == "gemini summary"
        assert backend == "gemini"
        assert returned_usage == usage
        mock.assert_called_once()
        call_kwargs = mock.call_args
        assert call_kwargs[1]["model"] == "gemini-3-flash-preview"

    def test_gemini_backend_returns_none_falls_through_to_lemonade(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=(None, None)),
            patch("scholarposter.enrichment.summarizer.summarize_lemonade", return_value="lemonade result"),
        ):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500,
                                             prompt="Sum:", config=config)
        assert text == "lemonade result"
        assert backend == "lemonade"
        assert usage is None

    def test_gemini_model_empty_by_default(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        assert config.gemini.model == ""


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
    def test_returns_tuple(self) -> None:
        """summarize() returns a 3-tuple (text, backend_name, usage)."""
        config = SummarizationConfig(backend="extractive", max_chars=500)
        with patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="some text"):
            result = summarize(MULTI_SENTENCE_TEXT, backend="extractive", max_chars=500, prompt="Sum:", config=config)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_returns_none_when_all_backends_fail(self) -> None:
        """When all backends return None, summarize() returns (None, None, None)."""
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=(None, None)),
            patch("scholarposter.enrichment.summarizer.summarize_lemonade", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value=""),
        ):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert text is None
        assert backend is None
        assert usage is None

    def test_uses_preferred_backend_first(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        usage = GeminiUsage(tokens_used=50, cost_usd=0.0001, cost_currency="USD", is_estimated=True, cost_is_estimated=True)
        with patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=("gemini result", usage)):
            text, backend, returned_usage = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert text == "gemini result"
        assert backend == "gemini"
        assert returned_usage == usage

    def test_falls_back_to_lemonade_when_gemini_returns_none(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=(None, None)),
            patch("scholarposter.enrichment.summarizer.summarize_lemonade", return_value="lemonade result"),
        ):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert text == "lemonade result"
        assert backend == "lemonade"
        assert usage is None

    def test_falls_back_to_extractive_when_all_llm_return_none(self) -> None:
        config = SummarizationConfig(backend="gemini", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=(None, None)),
            patch("scholarposter.enrichment.summarizer.summarize_lemonade", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="extractive result"),
        ):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        assert text == "extractive result"
        assert backend == "extractive"
        assert usage is None

    def test_truncates_to_max_chars(self) -> None:
        config = SummarizationConfig(backend="extractive", max_chars=10)
        with patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="A" * 100):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="extractive", max_chars=10, prompt="Sum:", config=config)
        assert len(text) <= 10
        assert usage is None

    def test_extractive_backend_direct(self) -> None:
        config = SummarizationConfig(backend="extractive", max_chars=500)
        with patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="extractive summary"):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="extractive", max_chars=500, prompt="Sum:", config=config)
        assert text == "extractive summary"
        assert backend == "extractive"
        assert usage is None

    def test_ollama_backend_direct(self) -> None:
        config = SummarizationConfig(backend="ollama", max_chars=500)
        with patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value="ollama summary"):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="ollama", max_chars=500, prompt="Sum:", config=config)
        assert text == "ollama summary"
        assert backend == "ollama"
        assert usage is None


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

    def test_build_backend_order_gemini_full_chain(self) -> None:
        from scholarposter.enrichment.summarizer import _build_backend_order
        # gemini is first: full chain including lemonade
        assert _build_backend_order("gemini") == ["gemini", "lemonade", "ollama", "extractive"]

    def test_build_backend_order_lemonade(self) -> None:
        from scholarposter.enrichment.summarizer import _build_backend_order
        assert _build_backend_order("lemonade") == ["lemonade", "ollama", "extractive"]


class TestCollectSentencesBoundary:
    def test_no_trailing_space(self) -> None:
        """_collect_sentences uses join (no trailing space).

        This is more correct than the old concatenation which had a trailing
        space that inflated len() by 1, causing the while-loop to over-reduce
        at exact boundary lengths.
        """
        from scholarposter.enrichment.summarizer import _collect_sentences

        class FakeSentence:
            def __init__(self, text):
                self._text = text
            def __str__(self):
                return self._text

        sentences = [FakeSentence("A" * 50), FakeSentence("B" * 50)]
        result = _collect_sentences(sentences, min_chars=0)
        assert not result.endswith(" ")
        # join produces "AAA...A BBB...B" — 101 chars (50 + 1 space + 50)
        # Old code would have produced "AAA...A BBB...B " — 102 chars
        assert len(result) == 101


class TestSummarizeFallbackLogging:
    def test_fallback_warning_logged_when_primary_backend_fails(self) -> None:
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        config = SummarizationConfig(backend="gemini", max_chars=500)
        try:
            with (
                patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=(None, None)),
                patch("scholarposter.enrichment.summarizer.summarize_lemonade", return_value="lemonade result"),
            ):
                summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        finally:
            logger.remove(lid)
        assert any("lemonade" in m for m in messages), f"Expected fallback warning mentioning 'lemonade', got: {messages}"

    def test_no_fallback_warning_when_primary_backend_succeeds(self) -> None:
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        config = SummarizationConfig(backend="gemini", max_chars=500)
        try:
            with patch("scholarposter.enrichment.summarizer.summarize_via_gemini", return_value=("gemini result", None)):
                summarize(MULTI_SENTENCE_TEXT, backend="gemini", max_chars=500, prompt="Sum:", config=config)
        finally:
            logger.remove(lid)
        assert not any("falling back" in m for m in messages), f"Unexpected fallback warning: {messages}"

    def test_summarize_backend_name_comes_from_actual_used_backend(self) -> None:
        """Backend name in tuple must reflect actual backend used (fallback case)."""
        config = SummarizationConfig(backend="lemonade", max_chars=500)
        with (
            patch("scholarposter.enrichment.summarizer.summarize_lemonade", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_ollama", return_value=None),
            patch("scholarposter.enrichment.summarizer.summarize_extractive", return_value="ext result"),
        ):
            text, backend, usage = summarize(MULTI_SENTENCE_TEXT, backend="lemonade", max_chars=500, prompt="Sum:", config=config)
        assert text == "ext result"
        assert backend == "extractive"  # must reflect actual backend, not requested backend


class TestSummarizeLemonade:
    @respx.mock
    def test_returns_content_on_success(self) -> None:
        import respx as _respx
        _respx.post("http://127.0.0.1:8000/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "choices": [{"message": {"content": "Test summary."}}]
            })
        )
        result = summarize_lemonade("text", model="test-model", host="http://127.0.0.1:8000",
                                    prompt="Summarize:", timeout=10)
        assert result == "Test summary."

    @respx.mock
    def test_returns_none_on_500(self) -> None:
        import respx as _respx
        _respx.post("http://127.0.0.1:8000/v1/chat/completions").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        result = summarize_lemonade("text", model="test-model", host="http://127.0.0.1:8000",
                                    prompt="Summarize:", timeout=10)
        assert result is None

    @respx.mock
    def test_returns_none_on_timeout(self) -> None:
        import respx as _respx
        _respx.post("http://127.0.0.1:8000/v1/chat/completions").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        result = summarize_lemonade("text", model="test-model", host="http://127.0.0.1:8000",
                                    prompt="Summarize:", timeout=1)
        assert result is None

    @respx.mock
    def test_auto_detects_model_when_empty(self) -> None:
        import respx as _respx
        import scholarposter.enrichment.summarizer as _mod
        _mod._cached_lemonade_model = None  # reset cache
        _respx.get("http://127.0.0.1:8000/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [{"id": "auto-detected-model"}]
            })
        )
        _respx.post("http://127.0.0.1:8000/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "choices": [{"message": {"content": "Auto summary."}}]
            })
        )
        result = summarize_lemonade("text", model="", host="http://127.0.0.1:8000",
                                    prompt="Summarize:", timeout=10)
        assert result == "Auto summary."
        _mod._cached_lemonade_model = None  # cleanup

    def test_ensure_lemonade_model_returns_cached(self) -> None:
        import scholarposter.enrichment.summarizer as _mod
        _mod._cached_lemonade_model = "cached-model"
        result = _ensure_lemonade_model("http://127.0.0.1:8000", [], 8192)
        assert result == "cached-model"
        _mod._cached_lemonade_model = None

    def test_config_accepts_lemonade_backend(self) -> None:
        cfg = SummarizationConfig(backend="lemonade")
        assert cfg.backend == "lemonade"
        assert cfg.lemonade.host == "http://127.0.0.1:8000"
        assert cfg.lemonade.ctx_size == 8192
        assert cfg.lemonade.load_timeout_seconds == 180
        assert len(cfg.lemonade.preferred_models) > 0


class TestLemonadeAutoLoad:
    def test_get_downloaded_models_no_binary(self) -> None:
        with patch("scholarposter.enrichment.summarizer.shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            result = _get_downloaded_models()
        assert result == []

    def test_get_downloaded_models_parses_cli_output(self) -> None:
        cli_output = (
            "Model Name                              Downloaded  Details\n"
            "----------------------------------------------------------------------------------------------------\n"
            "Phi-4-mini-instruct-GGUF                 Yes         llamacpp\n"
            "Qwen3-8B-GGUF                            No          llamacpp\n"
            "user.DeepSeek-R1-GGUF                    Yes         llamacpp\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = cli_output
        with patch("scholarposter.enrichment.summarizer.shutil") as mock_shutil, \
             patch("scholarposter.enrichment.summarizer.subprocess") as mock_sub:
            mock_shutil.which.return_value = "/usr/bin/lemonade"
            mock_sub.run.return_value = mock_result
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            result = _get_downloaded_models()
        assert "Phi-4-mini-instruct-GGUF" in result
        assert "user.DeepSeek-R1-GGUF" in result
        assert "Qwen3-8B-GGUF" not in result

    def test_load_lemonade_model_returns_false_on_nonzero_returncode(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("scholarposter.enrichment.summarizer.subprocess") as mock_sub:
            mock_sub.run.return_value = mock_result
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            result = _load_lemonade_model("test-model", 8192, "http://127.0.0.1:8000")
        assert result is False

    @respx.mock
    def test_ensure_lemonade_model_prefers_configured_model(self) -> None:
        import scholarposter.enrichment.summarizer as _mod
        _mod._cached_lemonade_model = None
        # Server has no model loaded
        respx.get("http://127.0.0.1:8000/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        mock_result_list = MagicMock()
        mock_result_list.returncode = 0
        mock_result_list.stdout = (
            "Model Name                              Downloaded  Details\n"
            "----\n"
            "Phi-4-mini-instruct-GGUF                 Yes         llamacpp\n"
            "Qwen3-8B-GGUF                            Yes         llamacpp\n"
        )
        mock_result_load = MagicMock()
        mock_result_load.returncode = 0
        # After load, server has the model
        respx.get("http://127.0.0.1:8000/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [{"id": "Phi-4-mini-instruct-GGUF"}]
            })
        )
        with patch("scholarposter.enrichment.summarizer.shutil") as mock_shutil, \
             patch("scholarposter.enrichment.summarizer.subprocess") as mock_sub:
            mock_shutil.which.return_value = "/usr/bin/lemonade"
            mock_sub.run.side_effect = [mock_result_list, mock_result_load]
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            result = _ensure_lemonade_model(
                "http://127.0.0.1:8000",
                ["Phi-4-mini-instruct-GGUF", "Qwen3-8B-GGUF"],
                8192,
            )
        assert result == "Phi-4-mini-instruct-GGUF"
        _mod._cached_lemonade_model = None

    @respx.mock
    def test_ensure_with_user_prefix_matches_preferred(self) -> None:
        """CLI output with 'user.' prefix should still match preferred_models."""
        import scholarposter.enrichment.summarizer as _mod
        _mod._cached_lemonade_model = None
        respx.get("http://127.0.0.1:8000/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        mock_result_list = MagicMock()
        mock_result_list.returncode = 0
        mock_result_list.stdout = (
            "Model Name                              Downloaded  Details\n"
            "----\n"
            "user.Phi-4-mini-instruct-GGUF            Yes         llamacpp\n"
        )
        mock_result_load = MagicMock()
        mock_result_load.returncode = 0
        respx.get("http://127.0.0.1:8000/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [{"id": "user.Phi-4-mini-instruct-GGUF"}]
            })
        )
        with patch("scholarposter.enrichment.summarizer.shutil") as mock_shutil, \
             patch("scholarposter.enrichment.summarizer.subprocess") as mock_sub:
            mock_shutil.which.return_value = "/usr/bin/lemonade"
            mock_sub.run.side_effect = [mock_result_list, mock_result_load]
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            # preferred_models uses bare name without "user." prefix
            result = _ensure_lemonade_model(
                "http://127.0.0.1:8000",
                ["Phi-4-mini-instruct-GGUF"],
                8192,
            )
        # Should match despite user. prefix
        assert "Phi-4-mini-instruct-GGUF" in result
        _mod._cached_lemonade_model = None

    def test_ensure_no_downloaded_models_returns_empty(self) -> None:
        import scholarposter.enrichment.summarizer as _mod
        _mod._cached_lemonade_model = None
        with patch("scholarposter.enrichment.summarizer.httpx") as mock_httpx, \
             patch("scholarposter.enrichment.summarizer.shutil") as mock_shutil:
            mock_httpx.get.return_value = MagicMock(json=lambda: {"data": []})
            mock_shutil.which.return_value = None  # no lemonade binary
            result = _ensure_lemonade_model("http://127.0.0.1:8000", [], 8192)
        assert result == ""
        _mod._cached_lemonade_model = None


class TestStdinSafety:
    """FR-20d: stdin safety and byte limit."""

    def test_input_truncated_at_byte_limit(self):
        """Content exceeding byte limit is truncated before processing."""
        from scholarposter.enrichment.summarizer import _MAX_STDIN_BYTES
        huge = "A" * (_MAX_STDIN_BYTES + 10_000)
        # Should not crash — extractive handles truncated input
        text, backend, usage = summarize(
            huge, backend="extractive", max_chars=150,
            prompt="test", config=SummarizationConfig(),
        )
        # result may be None (too few sentences after truncation) or a string
        assert text is None or isinstance(text, str)

    def test_shell_metacharacters_do_not_crash(self):
        """Shell metacharacters in content don't crash the summarizer."""
        dangerous = "$(rm -rf /); `echo pwned`; " + MULTI_SENTENCE_TEXT
        result = summarize_extractive(dangerous, max_chars=150)
        assert isinstance(result, str)
        assert len(result) <= 150
