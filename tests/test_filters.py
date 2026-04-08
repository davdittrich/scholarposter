"""Tests for scholarposter.filters"""
import pytest
from datetime import datetime, timezone
from scholarposter.filters import evaluate_filters, FilterResult
from scholarposter.config import FilterConfig
from scholarposter.models import UnifiedPost


def make_post(**kwargs) -> UnifiedPost:
    defaults = dict(
        source_id="1",
        text="Test post",
        source_url="https://fediscience.org/@user/1",
        created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        hashtags=[],
        is_sensitive=False,
        has_poll=False,
    )
    defaults.update(kwargs)
    return UnifiedPost(**defaults)


class TestEvaluateFilters:
    def test_no_filters_passes(self):
        post = make_post(hashtags=["Science"])
        result = evaluate_filters(post, FilterConfig())
        assert result.passed is True

    def test_skip_hashtag_exact_match(self):
        post = make_post(hashtags=["nobridge", "Science"])
        cfg = FilterConfig(skip_hashtags=["nobridge"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert "nobridge" in result.reason.lower()

    def test_skip_hashtag_case_insensitive(self):
        post = make_post(hashtags=["NoBridge"])
        cfg = FilterConfig(skip_hashtags=["nobridge"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    def test_skip_hashtag_no_match_passes(self):
        post = make_post(hashtags=["Science", "Research"])
        cfg = FilterConfig(skip_hashtags=["nobridge", "private"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_skip_sensitive_content_type(self):
        post = make_post(is_sensitive=True)
        cfg = FilterConfig(skip_content_types=["sensitive"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    def test_skip_poll_content_type(self):
        post = make_post(has_poll=True)
        cfg = FilterConfig(skip_content_types=["poll"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    def test_sensitive_not_skipped_when_not_configured(self):
        post = make_post(is_sensitive=True)
        cfg = FilterConfig(skip_content_types=[])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_require_hashtags_present_passes(self):
        post = make_post(hashtags=["Research", "Science"])
        cfg = FilterConfig(require_hashtags=["Science", "Economics"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_require_hashtags_all_missing_fails(self):
        post = make_post(hashtags=["PersonalPost"])
        cfg = FilterConfig(require_hashtags=["Science", "Economics"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    def test_require_hashtags_empty_posts_all(self):
        post = make_post(hashtags=[])
        cfg = FilterConfig(require_hashtags=[])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_media_only_filter_skips_media_without_text(self):
        from scholarposter.models import MediaAttachment
        post = make_post(
            text="https://example.com #photo",
            media=[MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")],
        )
        cfg = FilterConfig(skip_content_types=["media_only"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert "media_only" in result.reason

    def test_media_only_filter_passes_when_text_present(self):
        from scholarposter.models import MediaAttachment
        post = make_post(
            text="Check out this paper https://example.com",
            media=[MediaAttachment(url="https://example.com/img.jpg", mime_type="image/jpeg")],
        )
        cfg = FilterConfig(skip_content_types=["media_only"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_media_only_filter_passes_when_no_media(self):
        post = make_post(text="", media=[])
        cfg = FilterConfig(skip_content_types=["media_only"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_combined_filters_first_fails(self):
        post = make_post(hashtags=["nobridge", "Science"])
        cfg = FilterConfig(
            skip_hashtags=["nobridge"],
            require_hashtags=["Science"],
        )
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    def test_result_has_reason(self):
        post = make_post(is_sensitive=True)
        cfg = FilterConfig(skip_content_types=["sensitive"])
        result = evaluate_filters(post, cfg)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


class TestFilterDebugLogging:
    def test_skip_hashtags_check_logged_at_debug(self):
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
        try:
            post = make_post(hashtags=["science"])
            evaluate_filters(post, FilterConfig(skip_hashtags=["nobridge"]))
        finally:
            logger.remove(lid)
        assert any("skip_hashtags" in m for m in messages)

    def test_pass_logged_at_debug(self):
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
        try:
            post = make_post(source_id="42", hashtags=["science"])
            evaluate_filters(post, FilterConfig())
        finally:
            logger.remove(lid)
        assert any("passed all filters" in m for m in messages)

    def test_require_hashtags_check_logged_at_debug(self):
        from loguru import logger
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
        try:
            post = make_post(hashtags=["science"])
            evaluate_filters(post, FilterConfig(require_hashtags=["Research"]))
        finally:
            logger.remove(lid)
        assert any("require_hashtags" in m for m in messages)


class TestApplyHashtagRules:
    def test_no_rules_returns_text_unchanged(self):
        from scholarposter.filters import apply_hashtag_rules
        assert apply_hashtag_rules("hello", ["Science"], []) == "hello"

    def test_matching_rule_prepends_hashtag(self):
        from scholarposter.filters import apply_hashtag_rules
        from scholarposter.config import HashtagRule
        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics", "GameTheory"])]
        result = apply_hashtag_rules("Some text", ["Economics", "Research"], rules)
        assert result.startswith("#EconSky\n")
        assert "Some text" in result

    def test_non_matching_rule_leaves_text_unchanged(self):
        from scholarposter.filters import apply_hashtag_rules
        from scholarposter.config import HashtagRule
        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics"])]
        result = apply_hashtag_rules("Some text", ["Science", "Research"], rules)
        assert result == "Some text"

    def test_case_insensitive_matching(self):
        from scholarposter.filters import apply_hashtag_rules
        from scholarposter.config import HashtagRule
        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=["economics"])]
        result = apply_hashtag_rules("text", ["Economics"], rules)
        assert result.startswith("#EconSky")

    def test_multiple_rules_multiple_matches(self):
        from scholarposter.filters import apply_hashtag_rules
        from scholarposter.config import HashtagRule
        rules = [
            HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics"]),
            HashtagRule(add_hashtag="AcademicSky", if_any_hashtag=["Research"]),
        ]
        result = apply_hashtag_rules("text", ["Economics", "Research"], rules)
        assert "#EconSky" in result
        assert "#AcademicSky" in result
        # Both on the same prefix line
        first_line = result.split("\n")[0]
        assert "#EconSky" in first_line
        assert "#AcademicSky" in first_line

    def test_multiple_rules_partial_match(self):
        from scholarposter.filters import apply_hashtag_rules
        from scholarposter.config import HashtagRule
        rules = [
            HashtagRule(add_hashtag="EconSky", if_any_hashtag=["Economics"]),
            HashtagRule(add_hashtag="AcademicSky", if_any_hashtag=["Research"]),
        ]
        result = apply_hashtag_rules("text", ["Economics"], rules)
        assert "#EconSky" in result
        assert "#AcademicSky" not in result

    def test_empty_trigger_list_never_matches(self):
        from scholarposter.filters import apply_hashtag_rules
        from scholarposter.config import HashtagRule
        rules = [HashtagRule(add_hashtag="EconSky", if_any_hashtag=[])]
        result = apply_hashtag_rules("text", ["Economics"], rules)
        assert result == "text"


class TestUnifiedPostNewFields:
    """WU-1: Verify the 5 new UnifiedPost fields exist with correct defaults."""

    def test_is_reply_defaults_false(self):
        post = make_post()
        assert post.is_reply is False

    def test_is_self_thread_reply_defaults_false(self):
        post = make_post()
        assert post.is_self_thread_reply is False

    def test_visibility_defaults_public(self):
        post = make_post()
        assert post.visibility == "public"

    def test_has_content_warning_defaults_false(self):
        post = make_post()
        assert post.has_content_warning is False

    def test_has_mention_defaults_false(self):
        post = make_post()
        assert post.has_mention is False

    def test_new_fields_can_be_set(self):
        post = make_post(
            is_reply=True,
            is_self_thread_reply=False,
            visibility="private",
            has_content_warning=True,
            has_mention=True,
        )
        assert post.is_reply is True
        assert post.visibility == "private"
        assert post.has_content_warning is True
        assert post.has_mention is True


class TestExtendedContentTypeFilters:
    """WU-3: 7 new skip_content_types branches."""

    # --- reply ---
    def test_skip_reply(self):
        post = make_post(is_reply=True)
        cfg = FilterConfig(skip_content_types=["reply"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: reply"

    def test_skip_reply_passes_self_thread(self):
        # The collector enforces mutual exclusion: is_self_thread_reply=True implies
        # is_reply=False. This test uses the correct real-world state and guards against
        # the "reply" filter being accidentally widened to also catch self-thread posts.
        post = make_post(is_self_thread_reply=True)  # is_reply=False (collector invariant)
        cfg = FilterConfig(skip_content_types=["reply"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_skip_reply_not_configured(self):
        post = make_post(is_reply=True)
        cfg = FilterConfig(skip_content_types=[])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    # --- self_thread_reply ---
    def test_skip_self_thread_reply(self):
        post = make_post(is_self_thread_reply=True)
        cfg = FilterConfig(skip_content_types=["self_thread_reply"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: self_thread_reply"

    def test_skip_self_thread_passes_other_reply(self):
        post = make_post(is_reply=True)
        cfg = FilterConfig(skip_content_types=["self_thread_reply"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    # --- both reply types ---
    def test_skip_all_replies_catches_other(self):
        post = make_post(is_reply=True)
        cfg = FilterConfig(skip_content_types=["reply", "self_thread_reply"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    def test_skip_all_replies_catches_self(self):
        post = make_post(is_self_thread_reply=True)
        cfg = FilterConfig(skip_content_types=["reply", "self_thread_reply"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False

    # --- visibility ---
    def test_skip_private(self):
        post = make_post(visibility="private")
        cfg = FilterConfig(skip_content_types=["private"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: private"

    def test_skip_private_passes_public(self):
        post = make_post(visibility="public")
        cfg = FilterConfig(skip_content_types=["private"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_skip_direct(self):
        post = make_post(visibility="direct")
        cfg = FilterConfig(skip_content_types=["direct"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: direct"

    def test_skip_unlisted(self):
        post = make_post(visibility="unlisted")
        cfg = FilterConfig(skip_content_types=["unlisted"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: unlisted"

    # --- content_warning ---
    def test_skip_content_warning(self):
        post = make_post(has_content_warning=True)
        cfg = FilterConfig(skip_content_types=["content_warning"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: content_warning"

    def test_skip_content_warning_passes_no_cw(self):
        post = make_post(has_content_warning=False)
        cfg = FilterConfig(skip_content_types=["content_warning"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    # --- mention ---
    def test_skip_mention(self):
        post = make_post(has_mention=True)
        cfg = FilterConfig(skip_content_types=["mention"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert result.reason == "skip_content_type: mention"

    def test_skip_mention_passes_no_mention(self):
        post = make_post(has_mention=False)
        cfg = FilterConfig(skip_content_types=["mention"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True


class TestReblogFilter:
    def test_reblog_filtered_when_configured(self):
        post = make_post(is_reblog=True)
        cfg = FilterConfig(skip_content_types=["reblog"])
        result = evaluate_filters(post, cfg)
        assert result.passed is False
        assert "reblog" in result.reason

    def test_non_reblog_passes_when_reblog_configured(self):
        post = make_post(is_reblog=False)
        cfg = FilterConfig(skip_content_types=["reblog"])
        result = evaluate_filters(post, cfg)
        assert result.passed is True

    def test_reblog_not_filtered_when_not_configured(self):
        post = make_post(is_reblog=True)
        cfg = FilterConfig(skip_content_types=[])
        result = evaluate_filters(post, cfg)
        assert result.passed is True
