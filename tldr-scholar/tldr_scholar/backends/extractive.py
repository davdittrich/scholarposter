"""Extractive summarization backend — sumy KL + LSA two-pass algorithm.

Copied from scholarposter's summarize_extractive with attribution.
Focus keyword biasing: re-ranks sumy output by boosting sentences containing focus terms.
"""
from __future__ import annotations

import re
from math import sqrt
from typing import Optional

from loguru import logger
from sumy.nlp.tokenizers import Tokenizer
from sumy.parsers.plaintext import PlaintextParser
from sumy.summarizers.kl import KLSummarizer
from sumy.summarizers.lsa import LsaSummarizer

from tldr_scholar.backends.base import BackendBase

_LANGUAGE = "english"
_MIN_SENTENCES = 3
_MIN_SENTENCE_CHARS = 40


def _collect_sentences(sentences, min_chars: int = _MIN_SENTENCE_CHARS) -> str:
    parts = [str(s) for s in sentences if len(str(s)) > min_chars]
    text = " ".join(parts)
    return re.sub(r"\([^()]*\)", "", text)


class ExtractiveBackend(BackendBase):
    def __init__(self, config: dict = None):
        cfg = config or {}
        self._max_sentences = cfg.get("max_sentences", 5)

    def summarize(self, text: str, max_chars: int, focus: str,
                  hashtag_instruction: str) -> Optional[str]:
        """Extractive summarization. hashtag_instruction is ignored."""
        parser = PlaintextParser.from_string(text, Tokenizer(_LANGUAGE))
        sc = len(parser.document.sentences)
        if sc < _MIN_SENTENCES:
            return ""

        kl_summ = KLSummarizer()
        lsa_summ = LsaSummarizer()
        full_text = ""

        # Stage 1: reduce very long documents
        if sc > 150:
            reduced_count = max(150, int(150 + sqrt(sc - 150)))
            full_text = _collect_sentences(kl_summ(parser.document, reduced_count))
            parser = PlaintextParser.from_string(full_text, Tokenizer(_LANGUAGE))
            sc = len(parser.document.sentences)
            full_text = ""

        pc = len(parser.document.paragraphs)
        nos = min(max(3, int(0.01 * sc), int(0.05 * pc)), self._max_sentences)

        # Stage 2: LSA extraction
        sentences = list(lsa_summ(parser.document, nos))

        # Focus keyword biasing: re-rank sentences containing focus terms first
        if focus:
            focus_words = set(focus.lower().split())
            sentences.sort(
                key=lambda s: (
                    0 if any(w in str(s).lower() for w in focus_words) else 1,
                ),
            )

        full_text = _collect_sentences(sentences)

        # Reduce if too long
        while len(full_text) > max_chars:
            nos -= 1
            if nos == 0:
                break
            full_text = _collect_sentences(
                lsa_summ(parser.document, nos), min_chars=0
            )

        if len(full_text) > max_chars:
            full_text = full_text[:max_chars - 1] + "\u2026"

        return full_text.strip()
