"""Tests for scholarposter.audit (WU-3): audit log, build_audit_record, CLI subcommand."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from scholarposter.audit.log import build_audit_record
from scholarposter.cli import app
from scholarposter.models import (
    LinkEnrichment,
    LinkType,
    PostResult,
    PostStatus,
    UnifiedPost,
)
from scholarposter.state import StateManager

runner = CliRunner()


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_post(
    source_id: str = "123",
    hashtags: list[str] | None = None,
    links: list[LinkEnrichment] | None = None,
) -> UnifiedPost:
    return UnifiedPost(
        source_id=source_id,
        text="Test toot",
        source_url=f"https://fediscience.org/@user/{source_id}",
        created_at=datetime(2026, 4, 7, 14, 23, 11, tzinfo=timezone.utc),
        hashtags=hashtags or [],
        urls=[],
        links=links or [],
    )


def _make_link(
    doi: str | None = None,
    crossref_abstract: str | None = None,
    summary: str | None = None,
    enrichment_path: list[str] | None = None,
    llm_backend_used: str | None = None,
    link_type: LinkType = LinkType.WEBPAGE,
) -> LinkEnrichment:
    return LinkEnrichment(
        original_url="https://example.com/paper",
        doi=doi,
        crossref_abstract=crossref_abstract,
        summary=summary,
        enrichment_path=enrichment_path or [],
        llm_backend_used=llm_backend_used,
        link_type=link_type,
    )


_BASE_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
)

_AUDIT_TOML = (
    "[mastodon]\n"
    'instance = "https://fediscience.org"\n'
    'credentials_file = "test.secret"\n'
    "\n"
    "[audit]\n"
    "enabled = true\n"
    'file = "audit.jsonl"\n'
)


# ─── build_audit_record ───────────────────────────────────────────────────────

class TestBuildAuditRecord:
    def test_all_required_fields_present(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED,
                            post_url="https://bsky.app/profile/did/post/abc")
        record = build_audit_record(
            toot_id="123", platform="bluesky", post=post, result=result,
            dry_run=False,
        )
        required = {
            "timestamp", "toot_id", "platform", "status", "enrichment_path",
            "pdf_stage_skipped", "llm_backend_used", "abstract_chars", "summary_chars",
            "doi", "link_type", "post_url", "bluesky_likes", "bluesky_reposts",
            "engagement_synced_at", "hashtags", "chunk_count",
        }
        assert required.issubset(set(record.keys()))

    def test_status_posted(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["status"] == "posted"

    def test_status_failed(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.FAILED, error="connection refused")
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["status"] == "failed"

    def test_status_dry_run_overrides_posted(self):
        """dry_run=True must always produce status='dry_run', even if result.status=POSTED."""
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=True)
        assert record["status"] == "dry_run"

    def test_pdf_stage_skipped_true_when_in_enrichment_path(self):
        link = _make_link(enrichment_path=["stage_2.5_crossref", "stage_2.5_skip"])
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["pdf_stage_skipped"] is True

    def test_pdf_stage_skipped_false_when_not_in_path(self):
        link = _make_link(enrichment_path=["stage_3_pdf"])
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["pdf_stage_skipped"] is False

    def test_pdf_stage_skipped_false_when_no_links(self):
        post = _make_post(links=[])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["pdf_stage_skipped"] is False

    def test_engagement_fields_null_at_write_time(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED,
                            post_url="https://bsky.app/profile/did/post/abc")
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["bluesky_likes"] is None
        assert record["bluesky_reposts"] is None
        assert record["engagement_synced_at"] is None

    def test_doi_from_first_link(self):
        link = _make_link(doi="10.1234/test")
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["doi"] == "10.1234/test"

    def test_doi_none_when_no_links(self):
        post = _make_post(links=[])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["doi"] is None

    def test_abstract_chars_from_crossref_abstract_length(self):
        link = _make_link(crossref_abstract="A" * 50)
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["abstract_chars"] == 50

    def test_abstract_chars_none_when_no_abstract(self):
        link = _make_link(doi="10.1/x")
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["abstract_chars"] is None

    def test_summary_chars_from_summary_length(self):
        link = _make_link(summary="Short summary.")
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["summary_chars"] == len("Short summary.")

    def test_llm_backend_used_from_link(self):
        link = _make_link(llm_backend_used="extractive")
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["llm_backend_used"] == "extractive"

    def test_link_type_from_first_link(self):
        link = _make_link(link_type=LinkType.FILE)
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["link_type"] == "file"

    def test_link_type_none_when_no_links(self):
        post = _make_post(links=[])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["link_type"] is None

    def test_hashtags_from_post(self):
        post = _make_post(hashtags=["science", "ml"])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["hashtags"] == ["science", "ml"]

    def test_enrichment_path_from_first_link(self):
        link = _make_link(enrichment_path=["stage_4_crossref", "stage_5_summarize"])
        post = _make_post(links=[link])
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["enrichment_path"] == ["stage_4_crossref", "stage_5_summarize"]

    def test_chunk_count_default_one(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["chunk_count"] == 1

    def test_timestamp_is_iso_utc(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED)
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        # Must be parseable as ISO 8601 UTC
        ts = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        assert ts.tzinfo is not None

    def test_post_url_from_result(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.POSTED,
                            post_url="https://bsky.app/profile/did/post/rkey")
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["post_url"] == "https://bsky.app/profile/did/post/rkey"

    def test_post_url_none_for_failed(self):
        post = _make_post()
        result = PostResult(platform="bluesky", status=PostStatus.FAILED, error="boom")
        record = build_audit_record("1", "bluesky", post, result, dry_run=False)
        assert record["post_url"] is None


# ─── StateManager.append_audit ────────────────────────────────────────────────

class TestStateManagerAppendAudit:
    def test_creates_file_with_0o600_permissions(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        sm = StateManager(state_dir=tmp_path, audit_file=audit_file)
        with sm.lock():
            sm.append_audit({"toot_id": "1", "status": "posted"})
        assert audit_file.exists()
        mode = oct(audit_file.stat().st_mode & 0o777)
        assert mode == oct(0o600)

    def test_writes_valid_json_line(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        sm = StateManager(state_dir=tmp_path, audit_file=audit_file)
        record = {"toot_id": "42", "platform": "bluesky", "status": "posted"}
        with sm.lock():
            sm.append_audit(record)
        lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["toot_id"] == "42"

    def test_appends_multiple_records(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        sm = StateManager(state_dir=tmp_path, audit_file=audit_file)
        with sm.lock():
            sm.append_audit({"toot_id": "1"})
            sm.append_audit({"toot_id": "2"})
        lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        ids = [json.loads(l)["toot_id"] for l in lines]
        assert ids == ["1", "2"]

    def test_noop_when_audit_file_is_none(self, tmp_path):
        """append_audit does nothing when no audit_file is configured."""
        sm = StateManager(state_dir=tmp_path, audit_file=None)
        sm.append_audit({"toot_id": "1"})  # Should not raise
        assert not (tmp_path / "audit.jsonl").exists()

    def test_warns_when_called_without_lock(self, tmp_path):
        from loguru import logger
        audit_file = tmp_path / "audit.jsonl"
        sm = StateManager(state_dir=tmp_path, audit_file=audit_file)
        messages = []
        lid = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            sm.append_audit({"toot_id": "1"})  # No lock held
        finally:
            logger.remove(lid)
        assert any("lock" in m.lower() for m in messages)

    def test_write_failure_is_non_blocking(self, tmp_path):
        """Audit write failure must not propagate — post outcome unaffected."""
        audit_file = tmp_path / "audit.jsonl"
        sm = StateManager(state_dir=tmp_path, audit_file=audit_file)
        # Simulate a write error; lock not held (warn suppressed in test).
        # The critical property: OSError from the write must not propagate.
        with patch("scholarposter.state.os.open", side_effect=OSError("no space")):
            sm.append_audit({"toot_id": "1"})  # Must not raise


# ─── scholarposter audit subcommand ──────────────────────────────────────────

class TestAuditSubcommand:
    def _write_audit_records(self, path: Path, records: list[dict]) -> None:
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_no_records_prints_message(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text("")  # empty file
        result = runner.invoke(app, ["audit", "--config", str(p)])
        assert result.exit_code == 0
        assert "No audit records" in result.output

    def test_tabular_output_default(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-07T14:00:00Z", "toot_id": "123",
             "platform": "bluesky", "status": "posted", "doi": "10.1/x",
             "llm_backend_used": "extractive", "summary_chars": 120,
             "engagement_synced_at": None, "bluesky_likes": None, "bluesky_reposts": None,
             "pdf_stage_skipped": False, "enrichment_path": [], "abstract_chars": None,
             "link_type": "webpage", "post_url": "https://bsky.app/p/1",
             "hashtags": [], "chunk_count": 1},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p)])
        assert result.exit_code == 0
        assert "123" in result.output
        assert "bluesky" in result.output

    def test_json_flag_emits_raw_lines(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        record = {"timestamp": "2026-04-07T14:00:00Z", "toot_id": "99",
                  "platform": "bluesky", "status": "posted"}
        self._write_audit_records(tmp_path / "audit.jsonl", [record])
        result = runner.invoke(app, ["audit", "--config", str(p), "--json"])
        assert result.exit_code == 0
        # Output should be parseable JSON
        parsed = json.loads(result.output.strip())
        assert parsed["toot_id"] == "99"

    def test_filter_by_platform(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-07T14:00:00Z", "toot_id": "1",
             "platform": "bluesky", "status": "posted"},
            {"timestamp": "2026-04-07T14:01:00Z", "toot_id": "2",
             "platform": "linkedin", "status": "posted"},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p),
                                     "--json", "--platform", "linkedin"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["platform"] == "linkedin"
        assert parsed["toot_id"] == "2"

    def test_filter_by_status(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-07T14:00:00Z", "toot_id": "1",
             "platform": "bluesky", "status": "posted"},
            {"timestamp": "2026-04-07T14:01:00Z", "toot_id": "2",
             "platform": "bluesky", "status": "failed"},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p),
                                     "--json", "--status", "failed"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["status"] == "failed"

    def test_filter_by_since(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-05T12:00:00Z", "toot_id": "1",
             "platform": "bluesky", "status": "posted"},
            {"timestamp": "2026-04-07T12:00:00Z", "toot_id": "2",
             "platform": "bluesky", "status": "posted"},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p),
                                     "--json", "--since", "2026-04-06"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["toot_id"] == "2"

    def test_filter_by_until(self, tmp_path):
        """--until excludes records after end-of-day on the given date."""
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-05T12:00:00Z", "toot_id": "1",
             "platform": "bluesky", "status": "posted"},
            {"timestamp": "2026-04-07T12:00:00Z", "toot_id": "2",
             "platform": "bluesky", "status": "posted"},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p),
                                     "--json", "--until", "2026-04-06"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["toot_id"] == "1"  # Apr-05 inside; Apr-07 outside

    def test_since_and_until_combined(self, tmp_path):
        """--since + --until both applied; only records in range returned."""
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-01T00:00:00Z", "toot_id": "1", "platform": "bluesky", "status": "posted"},
            {"timestamp": "2026-04-05T12:00:00Z", "toot_id": "2", "platform": "bluesky", "status": "posted"},
            {"timestamp": "2026-04-10T00:00:00Z", "toot_id": "3", "platform": "bluesky", "status": "posted"},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p),
                                     "--json", "--since", "2026-04-03", "--until", "2026-04-07"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["toot_id"] == "2"

    def test_limit_flag(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": f"2026-04-0{i}T12:00:00Z", "toot_id": str(i),
             "platform": "bluesky", "status": "posted"}
            for i in range(1, 6)  # 5 records
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p), "--json", "--limit", "2"])
        assert result.exit_code == 0
        # --json with multiple records: each line is a JSON object
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_csv_flag_emits_csv(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(_AUDIT_TOML)
        records = [
            {"timestamp": "2026-04-07T14:00:00Z", "toot_id": "1",
             "platform": "bluesky", "status": "posted",
             "doi": None, "llm_backend_used": None, "summary_chars": None,
             "engagement_synced_at": None, "bluesky_likes": None, "bluesky_reposts": None},
        ]
        self._write_audit_records(tmp_path / "audit.jsonl", records)
        result = runner.invoke(app, ["audit", "--config", str(p), "--csv"])
        assert result.exit_code == 0
        # First line should be a CSV header
        lines = result.output.strip().splitlines()
        assert len(lines) >= 2  # header + data
        assert "," in lines[0]  # header has commas
