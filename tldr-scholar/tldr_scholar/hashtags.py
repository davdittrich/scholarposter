"""Hashtag generation: LLM response parsing + TF-IDF fallback."""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from loguru import logger

_HASHTAG_RE = re.compile(r"#\w+")

# Common English stopwords (subset — no NLTK dependency)
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those",
    "it", "its", "we", "our", "they", "their", "not", "no", "as", "if",
    "so", "than", "more", "also", "very", "just", "about", "into", "over",
    "such", "only", "other", "new", "some", "each", "which", "when",
    "where", "how", "all", "both", "through", "between", "after", "before",
    "during", "up", "out", "then", "here", "there", "what", "who", "whom",
})


def build_hashtag_instruction(n: int) -> str:
    """Build the prompt instruction for LLM hashtag generation.

    Returns empty string when n == 0.
    """
    if n <= 0:
        return ""
    return (
        f"After the summary, on a new line, generate exactly {n} hashtags. "
        "Each hashtag should be 1-2 words, lowercase, commonly used on academic "
        "social media, and descriptive of the text's key topics. "
        "Format: #hashtag1 #hashtag2 ..."
    )


def parse_hashtags_from_response(response: str) -> tuple[str, list[str]]:
    """Split LLM response into (summary_text, hashtags_list).

    Finds the last line containing hashtags (matching #word pattern).
    Returns (full_response, []) if no hashtag line is found.
    """
    if not response:
        return "", []

    lines = response.strip().split("\n")

    # Search from the end for a line that looks like hashtags
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        tags = _HASHTAG_RE.findall(line)
        if tags and len(tags) >= 2:  # at least 2 hashtags on one line
            summary = "\n".join(lines[:i]).strip()
            hashtags = [t.lower()[:30] for t in tags]  # lowercase, max 30 chars
            return summary, hashtags

    # No hashtag line found
    return response.strip(), []


def generate_hashtags_tfidf(text: str, n: int) -> list[str]:
    """Generate hashtags from text using TF-IDF heuristic.

    No NLTK dependency — uses a built-in stopword list and simple tokenization.
    Returns hashtags as lowercase #-prefixed strings, max 30 chars each.
    """
    if n <= 0 or not text:
        return []

    # Tokenize: split on non-alphanumeric, lowercase, filter stopwords and short words
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    words = [w for w in words if w not in _STOPWORDS and len(w) >= 3]

    if not words:
        return []

    # TF (term frequency)
    tf = Counter(words)
    total = len(words)

    # Simple TF-IDF: tf * log(1/df) approximated as tf for single-document use
    # Boost multi-word terms by checking for capitalized words in original text
    # (heuristic for proper nouns / key terms)
    capitalized = set(re.findall(r"\b[A-Z][a-z]{2,}", text))
    cap_lower = {w.lower() for w in capitalized}

    scored: list[tuple[str, float]] = []
    for word, count in tf.items():
        score = count / total
        if word in cap_lower:
            score *= 2.0  # boost capitalized terms (likely key concepts)
        scored.append((word, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_terms = [w for w, _ in scored[:n]]
    return [f"#{t}"[:30] for t in top_terms]
