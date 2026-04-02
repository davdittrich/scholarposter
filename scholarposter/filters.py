"""Content filtering and text transformation for scholarposter."""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from scholarposter.config import FilterConfig, HashtagRule
from scholarposter.models import UnifiedPost


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""


def evaluate_filters(post: UnifiedPost, cfg: FilterConfig) -> FilterResult:
    """Evaluate filter rules against a post. Returns FilterResult."""
    post_tags_lower = {t.lower() for t in post.hashtags}

    # skip_hashtags check (case-insensitive)
    if cfg.skip_hashtags:
        skip_lower = {t.lower() for t in cfg.skip_hashtags}
        logger.debug(f"Evaluating skip_hashtags: post_tags={post_tags_lower} vs skip={skip_lower}")
        matched = post_tags_lower & skip_lower
        if matched:
            tag = next(iter(matched))
            return FilterResult(passed=False, reason=f"skip_hashtag: {tag}")

    # skip_content_types check
    if cfg.skip_content_types:
        logger.debug(f"Evaluating skip_content_types: {cfg.skip_content_types}")
        if "sensitive" in cfg.skip_content_types and post.is_sensitive:
            return FilterResult(passed=False, reason="skip_content_type: sensitive")
        if "poll" in cfg.skip_content_types and post.has_poll:
            return FilterResult(passed=False, reason="skip_content_type: poll")
        if "media_only" in cfg.skip_content_types and post.is_media_only:
            return FilterResult(passed=False, reason="skip_content_type: media_only")
        if "reblog" in cfg.skip_content_types and post.is_reblog:
            return FilterResult(passed=False, reason="skip_content_type: reblog")

    # require_hashtags check (at least one must match; empty = post all)
    if cfg.require_hashtags:
        required_lower = {t.lower() for t in cfg.require_hashtags}
        logger.debug(f"Evaluating require_hashtags: post_tags={post_tags_lower} vs required={required_lower}")
        if not (post_tags_lower & required_lower):
            return FilterResult(
                passed=False,
                reason=f"require_hashtags: none of {cfg.require_hashtags} found",
            )

    logger.debug(f"Toot {post.source_id} passed all filters")
    return FilterResult(passed=True, reason="")


def apply_hashtag_rules(text: str, hashtags: list[str], rules: list[HashtagRule]) -> str:
    """Prepend hashtags to text based on trigger rules.

    For each rule, if any of its if_any_hashtag values match a hashtag in the
    post (case-insensitive), prepend #add_hashtag to the post text.
    Multiple matching rules each contribute their hashtag, space-separated on
    a single prefix line.
    """
    if not rules:
        return text
    post_tags_lower = {t.lower() for t in hashtags}
    to_prepend: list[str] = []
    for rule in rules:
        trigger_lower = {t.lower() for t in rule.if_any_hashtag}
        if trigger_lower and (post_tags_lower & trigger_lower):
            to_prepend.append(f"#{rule.add_hashtag}")
    if not to_prepend:
        return text
    return " ".join(to_prepend) + "\n" + text
