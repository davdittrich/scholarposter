"""Tests for tldr_scholar.models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tldr_scholar.models import SummaryRequest, SummaryResult, SummaryMetadata


class TestSummaryRequest:
    def test_defaults(self):
        req = SummaryRequest(text="hello")
        assert req.max_chars == 500
        assert req.focus == "main findings and novel insights"
        assert req.hashtags == 0
        assert req.backend == "auto"
        assert req.backend_config == {}

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValidationError):
            SummaryRequest(text="hello", backend="invalid")

    def test_valid_backends(self):
        for b in ["auto", "gemini", "lemonade", "ollama", "extractive"]:
            req = SummaryRequest(text="x", backend=b)
            assert req.backend == b

    def test_custom_config(self):
        req = SummaryRequest(
            text="x",
            max_chars=200,
            focus="methodology",
            hashtags=5,
            backend="lemonade",
            backend_config={"host": "http://localhost:9000"},
        )
        assert req.max_chars == 200
        assert req.backend_config["host"] == "http://localhost:9000"


class TestSummaryResult:
    def test_round_trip(self):
        result = SummaryResult(
            text="A summary.",
            hashtags=["#ai", "#research"],
            metadata=SummaryMetadata(
                source="paper.pdf",
                input_type="pdf",
                backend_used="extractive",
                max_chars=500,
                focus="insights",
                char_count=10,
            ),
        )
        dumped = result.model_dump(mode="json")
        restored = SummaryResult.model_validate(dumped)
        assert restored.text == "A summary."
        assert restored.hashtags == ["#ai", "#research"]
        assert restored.metadata.backend_used == "extractive"
        assert restored.metadata.char_count == 10

    def test_metadata_defaults(self):
        result = SummaryResult(text="x")
        assert result.metadata.source == ""
        assert result.metadata.input_type == ""
        assert result.metadata.char_count == 0


class TestSummaryMetadata:
    def test_all_fields_present(self):
        """FR-33: JSON metadata must include these fields."""
        meta = SummaryMetadata()
        fields = meta.model_fields
        for required in ["source", "input_type", "backend_used", "max_chars", "focus", "char_count"]:
            assert required in fields, f"Missing metadata field: {required}"
