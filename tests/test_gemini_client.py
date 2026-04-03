"""Tests for scholarposter.gemini_client re-export.

The actual ACP client tests live in gemini-acp/tests/test_gemini_acp.py.
This file verifies the re-export works correctly.
"""
from __future__ import annotations


class TestReExport:
    def test_summarize_via_gemini_importable(self):
        from scholarposter.gemini_client import summarize_via_gemini
        assert callable(summarize_via_gemini)

    def test_acp_available_importable(self):
        from scholarposter.gemini_client import ACP_AVAILABLE
        assert isinstance(ACP_AVAILABLE, bool)

    def test_re_export_matches_source(self):
        from scholarposter.gemini_client import summarize_via_gemini as sp_fn
        from gemini_acp import summarize_via_gemini as ga_fn
        assert sp_fn is ga_fn  # same object, not a copy
