# Third-Pass Bug Fixes — PRD Compliance Audit + Code Review Issues

## Context

Full audit of the codebase against `tasks/prd-mastodon-crosspost-rewrite.md`. Two passes of
implementation have run; this plan addresses the remaining confirmed gaps plus the behavioral
regression identified by the critical-code-reviewer in the second-pass review.

---

## Confirmed Gaps Found

### Compliance gaps (from PRD audit)

| ID | Requirement | Current State |
|----|-------------|---------------|
| FR-40 | "exit 0 if lock already held" | Both `run` and `retry` exit code=1 on lock conflict — cron treats this as a job failure |
| FR-37 | `state.json` entry includes `last_posted_at` | `PlatformState.last_posted_at` field exists in the model; `state.py` writes it when non-None; but `cli.py` **never passes it** — the field is always None |

### Code review regression (from second-pass review)

| ID | Issue | Source |
|----|-------|--------|
| B1 | `mastodon.me()` called unconditionally before platform loop — extra API call when all platforms disabled or when the requested platform is not configured | code-reviewer |

### Cleanup (from code-reviewer)

| ID | Issue |
|----|-------|
| C1 | `from io import StringIO` and `import typer` unused in `test_cli.py:TestPrintMaskedConfig.test_list_of_dicts_has_bullet_prefix` |

### Deferred (out of scope)

| ID | Reason |
|----|--------|
| US-010 pending count | Requires authenticated Mastodon API call from the `status` subcommand — significant scope change |
| FR-10 `media_only` filter | `UnifiedPost` has no `is_media_only` flag; deriving it requires checking if post has only media and no text — separate enhancement |

---

## Changes

### Fix 1 — FR-40: exit 0 when lock already held

**File**: `scholarposter/cli.py`

FR-40: "File lock acquired at startup; exit 0 if lock already held."  
US-010: "Exit codes: 0 = success or **nothing to do**, 1 = posting error, 2 = configuration error."

A cron job whose lock is already held did "nothing wrong" — another instance is running. Exit 0 is correct.

**In `run()` (current line 109-111)**:
```python
if not state_mgr.acquire_lock():
    logger.info("Another instance is already running. Exiting cleanly.")
    raise typer.Exit(code=0)  # FR-40: lock held = nothing to do, not an error
```

**In `retry()` (current line 249-251)**:  
Same change — consistent with the general lock semantics. A retry blocked by the lock means
the run is in progress; the user can retry again after it completes.
```python
if not state_mgr.acquire_lock():
    logger.info("Another instance is already running. Retry after current run completes.")
    raise typer.Exit(code=0)  # FR-40
```

**Tests**:  
Add `test_run_exits_zero_when_lock_held` in `TestCliRun`:
```python
def test_run_exits_zero_when_lock_held(self, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(_BASE_TOML)
    with (
        patch("scholarposter.cli.StateManager") as mock_state_cls,
        patch("scholarposter.cli.find_dotenv", return_value=""),
    ):
        mock_state = MagicMock()
        mock_state.acquire_lock.return_value = False  # lock held
        mock_state_cls.return_value = mock_state
        result = runner.invoke(app, ["run", "--config", str(config_file)])
    assert result.exit_code == 0
```

Add `test_retry_exits_zero_when_lock_held` in `TestRetryLockRelease`.

---

### Fix 2 — FR-37: Set `last_posted_at` on successful post

**File**: `scholarposter/cli.py`

`PlatformState.last_posted_at: Optional[datetime] = None` exists in `models.py:97` and is
written by `state.py:49-50` when non-None. But `cli.py` never passes it.

**Add to imports**:
```python
from datetime import datetime, timezone
```

**In `run()`, split the single `update_platform_state` call into two branches**:
```python
if result.status == PostStatus.POSTED:
    state_mgr.update_platform_state(plat, PlatformState(
        last_toot_id=int(post.source_id),
        last_status=result.status.value,
        last_posted_at=datetime.now(timezone.utc),
    ))
else:
    state_mgr.update_platform_state(plat, PlatformState(
        last_toot_id=int(post.source_id),
        last_status=result.status.value,
        last_error=result.error,
    ))
```

Note: by splitting the branches, a FAILED state does NOT include `last_posted_at` (correct:
the post didn't happen), and a POSTED state does NOT include `last_error` (correct: no error
to report). The `state[platform] = entry` replacement in `state.py` clears old fields, so
previous errors are cleared on success and previous `last_posted_at` is preserved across
failures (since failure entries don't overwrite it — wait, state.py line 53 does
`state[platform] = entry` which replaces the whole entry. So on failure, `last_posted_at`
from a previous success IS lost. To preserve it, we need to read-modify-write).

**Revised approach** — merge with existing entry rather than replace:
Do NOT split into branches. Instead, always pass `last_posted_at` conditionally, and
explicitly pass `last_error=None` on success to clear previous errors. The state.py
`update_platform_state` writes each field only if non-None — but it replaces the entire
platform entry (`state[platform] = entry`), so fields not in `entry` are dropped.

The cleanest fix that preserves `last_posted_at` across failures is to change state.py to
MERGE rather than replace. But that is a larger change. For this pass, the scope is to set
`last_posted_at` on success, which already works via entry replacement (we just need to pass it):

```python
# In run() — single call, set last_posted_at only when POSTED
posted_at = datetime.now(timezone.utc) if result.status == PostStatus.POSTED else None
state_mgr.update_platform_state(plat, PlatformState(
    last_toot_id=int(post.source_id),
    last_status=result.status.value,
    last_posted_at=posted_at,
    last_error=result.error,
))
```

`state.py` only writes `last_posted_at` if non-None (line 49: `if ps.last_posted_at is not None`),
and `state[platform] = entry` replaces the whole platform dict. So:
- POSTED: entry has `last_toot_id`, `last_status`, `last_posted_at`; no `last_error` (result.error is None)
- FAILED: entry has `last_toot_id`, `last_status`, `last_error`; no `last_posted_at`

This matches FR-37's documented structure exactly.

Apply the same change in `retry()`.

**Tests (run command)**:

```python
def test_run_sets_last_posted_at_on_success(self, tmp_path):
    # Verify update_platform_state receives a non-None last_posted_at on POSTED
    ...mock setup...
    mock_dispatch.return_value = PostResult(platform="bluesky", status=PostStatus.POSTED)
    runner.invoke(app, ["run", "--config", str(config_file)])
    call_kwargs = mock_state.update_platform_state.call_args
    ps_arg = call_kwargs[0][1]  # PlatformState argument
    assert ps_arg.last_posted_at is not None

def test_run_no_last_posted_at_on_failure(self, tmp_path):
    # Verify last_posted_at is None on FAILED (not set)
    ...mock setup...
    mock_dispatch.return_value = PostResult(platform="bluesky", status=PostStatus.FAILED, error="boom")
    runner.invoke(app, ["run", "--config", str(config_file)])
    call_kwargs = mock_state.update_platform_state.call_args
    ps_arg = call_kwargs[0][1]
    assert ps_arg.last_posted_at is None
```

**Tests (retry command — blocking gap addressed here)**:  
Fix 2 changes BOTH `run()` and `retry()`. The retry command's
`update_platform_state` call at lines 273-277 also needs the same `last_posted_at` treatment.

```python
def test_retry_sets_last_posted_at_on_success(self, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(_BSKY_TOML)
    mock_post = MagicMock(); mock_post.source_id = "999"
    with (
        patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
        patch("scholarposter.cli.MastodonCollector") as mock_col_cls,
        patch("scholarposter.cli.StateManager") as mock_state_cls,
        patch("scholarposter.cli.EnrichmentPipeline") as mock_pipe_cls,
        patch("scholarposter.cli._dispatch_post") as mock_dispatch,
        patch("scholarposter.cli.find_dotenv", return_value=""),
    ):
        mock_mastodon_cls.return_value.status.return_value = {}
        mock_col_cls.return_value._toot_to_unified_post.return_value = mock_post
        mock_state = MagicMock(); mock_state.acquire_lock.return_value = True
        mock_state_cls.return_value = mock_state
        mock_pipe_cls.return_value.enrich.return_value = mock_post
        mock_dispatch.return_value = PostResult(platform="bluesky", status=PostStatus.POSTED, post_url="https://bsky.app/p/1")
        runner.invoke(app, ["retry", "--config", str(config_file), "--platform", "bluesky", "--toot-id", "999"])
    ps_arg = mock_state.update_platform_state.call_args[0][1]
    assert ps_arg.last_posted_at is not None
```

---

### Fix 3 — B1: Lazy `mastodon.me()` inside platform loop

**File**: `scholarposter/cli.py`

`mastodon.me()` was hoisted before the loop in the second pass. This is a regression: the
call now fires even when all platforms are disabled or `platforms_to_run` is empty, making
an unnecessary network round-trip and surfacing auth errors in the "nothing to do" case.

**Change** (inside `try:`, in `run()`):

Remove the unconditional lines:
```python
user_info = mastodon.me()
user_id = user_info["id"]
```

Replace with lazy initialization inside the loop, after both guards:
```python
user_id: Optional[str] = None

for plat in platforms_to_run:
    if plat not in cfg.platforms:
        ...continue
    plat_cfg = cfg.platforms[plat]
    if not plat_cfg.enabled:
        continue

    if user_id is None:
        user_id = mastodon.me()["id"]

    since_id = state_mgr.get_since_id(plat)
    post = collector.fetch_oldest_unprocessed(user_id=user_id, since_id=since_id)
```

**Invariants preserved**:
- `mastodon.me()` is called at most once per run (first enabled platform sets `user_id`)
- No call if all platforms are disabled or not configured
- Type: after the `if user_id is None:` branch, `user_id` is always a `str`; add
  `assert user_id is not None` before `fetch_oldest_unprocessed` call if type-checker complains

**Test**:  
Add `test_mastodon_me_not_called_when_no_enabled_platforms` to `TestCliRun`:
```python
def test_mastodon_me_not_called_when_no_enabled_platforms(self, tmp_path):
    config_file = tmp_path / "config.toml"
    # Config with bluesky disabled
    config_file.write_text(
        '[mastodon]\ninstance="https://fediscience.org"\ncredentials_file="t.secret"\n'
        '\n[platforms.bluesky]\nenabled=false\n'
    )
    with (
        patch("scholarposter.cli.Mastodon") as mock_mastodon_cls,
        patch("scholarposter.cli.StateManager") as mock_state_cls,
        patch("scholarposter.cli.EnrichmentPipeline"),
        patch("scholarposter.cli.find_dotenv", return_value=""),
    ):
        mock_mastodon = MagicMock()
        mock_mastodon_cls.return_value = mock_mastodon
        mock_state = MagicMock()
        mock_state.acquire_lock.return_value = True
        mock_state_cls.return_value = mock_state
        runner.invoke(app, ["run", "--config", str(config_file)])
    mock_mastodon.me.assert_not_called()
```

---

### Fix 4 — C1: Remove unused imports in TestPrintMaskedConfig

**File**: `tests/test_cli.py`

In `test_list_of_dicts_has_bullet_prefix` (line 576-577), remove:
```python
from io import StringIO   # ← delete
import typer              # ← delete
```

---

## Files Changed

| File | Changes |
|------|---------|
| `scholarposter/cli.py` | Fix 1 (exit codes), Fix 2 (last_posted_at + datetime import), Fix 3 (lazy user_id) |
| `tests/test_cli.py` | Fix 1 test, Fix 2 test, Fix 3 test, Fix 4 cleanup |

---

## Invariants

- Exit code 0 when lock held for both `run` and `retry` (FR-40)
- `last_posted_at` is written to state.json on every successful post (FR-37)
- `mastodon.me()` is never called when all platforms are disabled/unconfigured (B1 regression)
- All existing tests continue to pass (277 → ≥ 280 tests)
