"""Tests for tldr_scholar.hashtags."""
from __future__ import annotations

from tldr_scholar.hashtags import (
    build_hashtag_instruction,
    parse_hashtags_from_response,
    generate_hashtags_tfidf,
)


class TestBuildHashtagInstruction:
    def test_zero_returns_empty(self):
        assert build_hashtag_instruction(0) == ""

    def test_negative_returns_empty(self):
        assert build_hashtag_instruction(-1) == ""

    def test_positive_returns_instruction(self):
        result = build_hashtag_instruction(5)
        assert "5 hashtags" in result
        assert len(result) > 0


class TestParseHashtagsFromResponse:
    def test_response_with_hashtags(self):
        response = "This is a summary of the paper.\n\n#ai #machinelearning #nlp"
        summary, tags = parse_hashtags_from_response(response)
        assert summary == "This is a summary of the paper."
        assert "#ai" in tags
        assert "#machinelearning" in tags
        assert "#nlp" in tags

    def test_response_without_hashtags(self):
        response = "Just a plain summary with no tags."
        summary, tags = parse_hashtags_from_response(response)
        assert summary == response
        assert tags == []

    def test_empty_response(self):
        summary, tags = parse_hashtags_from_response("")
        assert summary == ""
        assert tags == []

    def test_hashtags_in_markdown_block(self):
        response = "Summary text here.\n\nKeywords: #deep #learning #transformer"
        summary, tags = parse_hashtags_from_response(response)
        assert "Summary text here" in summary
        assert len(tags) >= 2

    def test_hashtags_are_lowercase(self):
        response = "Summary.\n\n#AI #MachineLearning"
        _, tags = parse_hashtags_from_response(response)
        for tag in tags:
            assert tag == tag.lower()

    def test_hashtags_max_30_chars(self):
        long_tag = "#" + "a" * 40
        response = f"Summary.\n\n{long_tag} #short"
        _, tags = parse_hashtags_from_response(response)
        for tag in tags:
            assert len(tag) <= 30

    def test_single_hashtag_not_treated_as_line(self):
        """Need at least 2 hashtags on a line to detect as hashtag line."""
        response = "Summary with #one hashtag inline."
        summary, tags = parse_hashtags_from_response(response)
        assert tags == []
        assert summary == response


class TestGenerateHashtagsTfidf:
    def test_produces_hashtags(self):
        text = (
            "Machine learning transforms natural language processing. "
            "Transformer architectures enable contextual understanding. "
            "Large language models demonstrate emergent capabilities."
        )
        tags = generate_hashtags_tfidf(text, 3)
        assert len(tags) == 3
        assert all(t.startswith("#") for t in tags)

    def test_zero_returns_empty(self):
        assert generate_hashtags_tfidf("some text", 0) == []

    def test_empty_text_returns_empty(self):
        assert generate_hashtags_tfidf("", 5) == []

    def test_hashtags_are_lowercase(self):
        text = "Machine Learning and Natural Language Processing are important."
        tags = generate_hashtags_tfidf(text, 3)
        for tag in tags:
            assert tag == tag.lower()

    def test_hashtags_max_30_chars(self):
        text = "Supercalifragilisticexpialidocious research methodology analysis."
        tags = generate_hashtags_tfidf(text, 3)
        for tag in tags:
            assert len(tag) <= 30

    def test_boosts_capitalized_terms(self):
        text = (
            "The Transformer architecture was introduced. "
            "simple analysis of data shows interesting patterns. "
            "Transformer models are widely used."
        )
        tags = generate_hashtags_tfidf(text, 1)
        # "transformer" should be boosted because it's capitalized in source
        assert tags[0] == "#transformer"
