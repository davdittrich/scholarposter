"""Bluesky engagement sync for audit log (FR-93)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from urllib.parse import urlparse


def parse_at_uri(post_url: Optional[str]) -> Optional[str]:
    """Construct an AT URI from a bsky.app profile URL.

    Only works when the URL contains a DID (``did:plc:…`` or ``did:web:…``).
    Returns None for handle-based URLs or malformed input — caller should
    count those as skipped, not as errors.
    """
    if not post_url:
        return None
    try:
        parts = [p for p in urlparse(post_url).path.split("/") if p]
        # Expected structure: ["profile", "{did_or_handle}", "post", "{rkey}"]
        if len(parts) < 4 or parts[0] != "profile" or parts[2] != "post":
            return None
        did = parts[1]
        rkey = parts[3]
        if not did.startswith("did:") or not rkey:
            return None
        return f"at://{did}/app.bsky.feed.post/{rkey}"
    except Exception:
        return None


def _resolve_handle_to_at_uri(handle: str, rkey: str, client: Any) -> Optional[str]:
    """Resolve a Bluesky handle to a DID and construct an AT URI.

    Returns None when resolution fails — caller treats this as skipped.
    """
    try:
        result = client.com.atproto.identity.resolve_handle(params={"handle": handle})
        did = getattr(result, "did", None)
        if not isinstance(did, str) or not did.startswith("did:"):
            return None
        return f"at://{did}/app.bsky.feed.post/{rkey}"
    except Exception:
        return None


def _fetch_likes(at_uri: str, client: Any) -> int:
    """Cursor-paginate app.bsky.feed.getLikes and return total count."""
    total = 0
    cursor: Optional[str] = None
    while True:
        params: dict[str, Any] = {"uri": at_uri, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = client.app.bsky.feed.get_likes(params=params)
        total += len(resp.likes)
        cursor = resp.cursor
        if not cursor:
            break
    return total


def _fetch_reposts(at_uri: str, client: Any) -> int:
    """Cursor-paginate app.bsky.feed.getRepostedBy and return total count."""
    total = 0
    cursor: Optional[str] = None
    while True:
        params: dict[str, Any] = {"uri": at_uri, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = client.app.bsky.feed.get_reposted_by(params=params)
        total += len(resp.reposted_by)
        cursor = resp.cursor
        if not cursor:
            break
    return total


def _write_records_atomically(audit_path: Path, records: list[dict]) -> None:
    """Write records to a temp file then rename over audit_path.

    Verifies record count before rename.  Raises RuntimeError and deletes
    the temp file if the written count does not match.
    """
    tmp_path = audit_path.with_suffix(".jsonl.tmp")
    content = "".join(json.dumps(r) + "\n" for r in records).encode("utf-8")
    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)

    # Verify count
    with open(tmp_path) as f:
        written_count = sum(1 for line in f if line.strip())

    if written_count != len(records):
        try:
            os.unlink(str(tmp_path))
        except OSError:
            pass
        raise RuntimeError(
            f"record count mismatch: expected {len(records)}, got {written_count}"
        )

    os.replace(str(tmp_path), str(audit_path))


def sync_engagement(
    audit_path: Path,
    client: Any,
    dry_run: bool,
    force: bool,
) -> tuple[int, int, int]:
    """Sync Bluesky engagement counts into audit.jsonl.

    Reads every record from audit_path, fetches likes/reposts for
    eligible Bluesky records via the AT Protocol, and atomically
    rewrites the file.

    Returns:
        (synced, skipped, errors) — counts for this run.
        In dry_run mode the file is not written; synced counts
        planned updates.
    """
    if not audit_path.exists():
        return (0, 0, 0)

    with open(audit_path) as f:
        records = [json.loads(line) for line in f if line.strip()]

    synced = skipped = errors = 0
    updated_records: list[dict] = []

    for rec in records:
        outcome, updated_rec = _process_record(rec, client, force)
        updated_records.append(updated_rec)
        if outcome == "synced":
            synced += 1
        elif outcome == "skipped":
            skipped += 1
        else:  # "error"
            errors += 1

    if not dry_run:
        _write_records_atomically(audit_path, updated_records)

    return (synced, skipped, errors)


def _process_record(
    rec: dict, client: Any, force: bool
) -> tuple[str, dict]:
    """Process one audit record.

    Returns (outcome, record) where outcome is "synced" | "skipped" | "error".
    The returned record is the updated one on success, original otherwise.
    """
    if rec.get("platform") != "bluesky":
        return ("skipped", rec)

    post_url = rec.get("post_url")
    if not post_url:
        return ("skipped", rec)

    # Without --force, skip already-synced records
    if not force and rec.get("engagement_synced_at") is not None:
        return ("skipped", rec)

    at_uri = parse_at_uri(post_url)
    if at_uri is None:
        # Handle-based URL: attempt DID resolution before skipping
        parts = [p for p in urlparse(post_url).path.split("/") if p]
        if (
            len(parts) >= 4
            and parts[0] == "profile"
            and parts[2] == "post"
            and not parts[1].startswith("did:")
        ):
            at_uri = _resolve_handle_to_at_uri(parts[1], parts[3], client)
        if at_uri is None:
            return ("skipped", rec)

    try:
        likes = _fetch_likes(at_uri, client)
        reposts = _fetch_reposts(at_uri, client)
    except Exception:
        return ("error", rec)

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = {
        **rec,
        "bluesky_likes": likes,
        "bluesky_reposts": reposts,
        "engagement_synced_at": synced_at,
    }
    return ("synced", updated)
