"""Tests for scholarposter.gemini_client (agy subprocess backend)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import subprocess


class TestPublicApi:
    def test_summarize_via_gemini_importable(self):
        from scholarposter.gemini_client import summarize_via_gemini
        assert callable(summarize_via_gemini)

    def test_agy_available_importable(self):
        from scholarposter.gemini_client import AGY_AVAILABLE
        assert isinstance(AGY_AVAILABLE, bool)

    def test_acp_available_is_alias(self):
        from scholarposter.gemini_client import ACP_AVAILABLE, AGY_AVAILABLE
        assert ACP_AVAILABLE is AGY_AVAILABLE

    def test_gemini_usage_importable(self):
        from scholarposter.gemini_client import GeminiUsage
        u = GeminiUsage(tokens_used=10, cost_usd=None, cost_currency=None)
        assert u.tokens_used == 10
        assert u.is_estimated is True


class TestSummarizeViaGemini:
    def test_returns_text_and_usage_on_success(self):
        from scholarposter.gemini_client import summarize_via_gemini, GeminiUsage
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "summary text"
        mock_result.stderr = ""
        with patch("scholarposter.gemini_client.AGY_AVAILABLE", True), \
             patch("subprocess.run", return_value=mock_result):
            text, usage = summarize_via_gemini("input", "summarize this")
        assert text == "summary text"
        assert isinstance(usage, GeminiUsage)
        assert usage.tokens_used > 0
        assert usage.is_estimated is True

    def test_returns_none_when_agy_unavailable(self):
        from scholarposter.gemini_client import summarize_via_gemini
        with patch("scholarposter.gemini_client.AGY_AVAILABLE", False):
            text, usage = summarize_via_gemini("input", "prompt")
        assert text is None
        assert usage is None

    def test_returns_none_on_nonzero_exit(self):
        from scholarposter.gemini_client import summarize_via_gemini
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"
        with patch("scholarposter.gemini_client.AGY_AVAILABLE", True), \
             patch("subprocess.run", return_value=mock_result):
            text, usage = summarize_via_gemini("input", "prompt")
        assert text is None
        assert usage is None

    def test_returns_none_on_timeout(self):
        from scholarposter.gemini_client import summarize_via_gemini
        with patch("scholarposter.gemini_client.AGY_AVAILABLE", True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("agy", 30)):
            text, usage = summarize_via_gemini("input", "prompt", timeout=30)
        assert text is None
        assert usage is None

    def test_passes_model_flag_when_set(self):
        from scholarposter.gemini_client import summarize_via_gemini
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "result"
        mock_result.stderr = ""
        with patch("scholarposter.gemini_client.AGY_AVAILABLE", True), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            summarize_via_gemini("input", "prompt", model="gemini-2.5-flash")
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "gemini-2.5-flash" in cmd
