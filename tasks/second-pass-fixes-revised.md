# Second-Pass Bug Fixes — Revised Plan

## Context

The first-pass implementation fixed 20 bugs from the PRD audit. This plan addresses 8
additional issues found by the critical-code-reviewer skill and a full PRD compliance
re-read. A previous version of this plan failed the plan-review-gate with 3 blocking
issues; those are corrected here.

---

## Changes

### A1 — FR-8: Filter before enrich in `run()`

**File**: `scholarposter/cli.py`

FR-8 requires: "Evaluate filter rules BEFORE enrichment (fail fast)."

Current order (lines 146–148) enriches first, then filters. This wastes DOI lookups,
summarization, and link-card fetches on toots that will be skipped anyway.

**Change**: Swap the order inside the `for plat` loop:

```python
# 1. Filter first
filter_result = evaluate_filters(post, plat_cfg.filters)
if not filter_result.passed:
    logger.info(f"[{plat}] Toot {post.source_id} filtered: {filter_result.reason}")
    state_mgr.update_platform_state(plat, PlatformState(
        last_toot_id=int(post.source_id),
        last_status="skipped",
    ))
    continue

# 2. Enrich only after passing filter
post = pipeline.enrich(post)
```

**Test update**: In `TestEnrichmentWired.test_pipeline_enrich_called_before_dispatch`,
add an assertion that `evaluate_filters` is called BEFORE `pipeline.enrich`:
```python
call_order = []
mock_filter.side_effect = lambda p, f: (call_order.append("filter"),
                                         MagicMock(passed=True))[1]
mock_pipeline.enrich.side_effect = lambda p: (call_order.append("enrich"), p)[1]
# after invoke:
assert call_order.index("filter") < call_order.index("enrich")
```

---

### A2 — US-010: Add `last_error` to `status` output

**File**: `scholarposter/cli.py`

US-010 specifies status shows "last toot ID, pending count, last error".

Current line 220:
```python
typer.echo(f"{plat}: last_toot_id={data.get('last_toot_id')}, status={data.get('last_status')}")
```

**Change**:
```python
typer.echo(
    f"{plat}: last_toot_id={data.get('last_toot_id')}, "
    f"status={data.get('last_status')}, "
    f"last_error={data.get('last_error')}"
)
```

**Test**: Add `test_status_shows_last_error` in `tests/test_cli.py` that writes state
with a `last_error` field and verifies it appears in output.

---

### A3 — Fix `_print_masked_config` list-of-dicts display

**File**: `scholarposter/cli.py`

Current code (lines 318–323) recurses into dict items in a list at the same `indent`
level with no bullet marker, making list items visually indistinguishable from nested
dicts.

**Change**: Replace the `elif isinstance(data, list):` branch:

```python
elif isinstance(data, list):
    for item in data:
        if isinstance(item, dict):
            keys = list(item.items())
            for i, (key, value) in enumerate(keys):
                bullet = "- " if i == 0 else "  "
                if key in _SENSITIVE_FIELDS:
                    typer.echo(f"{prefix}{bullet}{key}: [REDACTED]")
                elif isinstance(value, (dict, list)):
                    typer.echo(f"{prefix}{bullet}{key}:")
                    _print_masked_config(value, indent + 1)
                else:
                    typer.echo(f"{prefix}{bullet}{key}: {value}")
        elif isinstance(item, list):
            _print_masked_config(item, indent + 1)
        else:
            typer.echo(f"{prefix}- {item}")
```

The first key of each dict item in a list gets the `"- "` bullet; remaining keys of the
same dict get `"  "` alignment so the block reads as one item.

**Test**: Add `test_print_masked_config_list_of_dicts` that calls `_print_masked_config`
with `[{"type": "ntfy", "topic": "alerts"}]` at indent=0 and asserts the output contains
`"- type: ntfy"` (bullet on first key).

---

### B1 — Move `mastodon.me()` outside platform loop

**File**: `scholarposter/cli.py`

`mastodon.me()` (lines 138–139) is called on every loop iteration, making one redundant
API round-trip per additional platform.

**Change**: Move `user_info = mastodon.me()` and `user_id = user_info["id"]` to BEFORE
the `for plat in platforms_to_run:` loop (after `notified_platforms: set[str] = set()`).

**No new test needed**: `TestEnrichmentWired` already mocks `Mastodon` and will
implicitly cover this via the mock call count if desired.

---

### B2 — Move platform config check before API calls in `retry` (CORRECTED)

**File**: `scholarposter/cli.py`

**Problem in original plan**: The prior plan proposed placing `if platform not in
cfg.platforms:` BEFORE the `try:` block. This is a lock-leak bug: if that guard fires
after `acquire_lock()` but before `try:`, the `finally:` clause never executes and the
lock file is left behind, blocking all future runs.

**Corrected change**: The guard must remain INSIDE `try:` (so `finally:` always runs),
but it must move to BEFORE the first API call (`mastodon.status(toot_id)` at line 259).
Currently the check is at line 263, three lines AFTER the API calls. Move it to
immediately after pipeline setup:

```python
try:
    # Validate config before any API calls
    if platform not in cfg.platforms:
        typer.echo(f"Platform '{platform}' not configured.", err=True)
        raise typer.Exit(code=1)
    plat_cfg = cfg.platforms[platform]

    mastodon = Mastodon(
        access_token=cfg.mastodon.credentials_file,
        api_base_url=cfg.mastodon.instance,
    )
    collector = MastodonCollector(mastodon)
    pipeline = EnrichmentPipeline(config=cfg.enrichment, cache=state_mgr)

    raw_toot = mastodon.status(toot_id)   # ← API call only after config validated
    post = collector._toot_to_unified_post(raw_toot)
    post = pipeline.enrich(post)
    result = _dispatch_post(platform, post, plat_cfg, dry_run)
    ...
finally:
    state_mgr.release_lock()
```

**Test**: Add `test_retry_unconfigured_platform_releases_lock` that passes a config with
no platforms, invokes retry, and asserts `mock_state.release_lock` was called (lock
released even though platform was invalid).

---

### B3 — Move `time.sleep(0.2)` to `finally:` in `_build_facets`

**File**: `scholarposter/adapters/bluesky.py`

Current code (line 133) places `time.sleep(0.2)` inside `try:`, so failed handle
resolutions skip the delay and can hammer the AT Protocol identity endpoint.

**Change**: Move sleep to `finally:`:

```python
for m in mentions[:10]:
    try:
        resp = client.com.atproto.identity.resolve_handle(params={"handle": m["handle"]})
        did = resp.did
        facets.append({
            "index": {"byteStart": m["start"], "byteEnd": m["end"]},
            "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}],
        })
    except Exception:
        pass  # Unresolvable mentions render as plain text
    finally:
        time.sleep(0.2)  # Always rate-limit, even on exception
```

**Test**: Add `test_mention_resolution_sleeps_on_exception` that patches `resolve_handle`
to raise and verifies `time.sleep` is still called once.

---

### B4 — Remove signal/email dead path in `_build_notifiers`

**File**: `scholarposter/cli.py`

Lines 78–79 have a special `elif cfg.type in ("signal", "email"):` branch that only logs
a warning and skips — identical behavior to the `else:` clause. This creates a false
implication that signal/email are recognized but pending, when they should be treated
identically to any unrecognized type.

**Change**: Remove the `elif cfg.type in ("signal", "email"):` branch entirely. Let
signal/email fall through to `else:` which emits
`logger.warning(f"Unknown notification backend type: {cfg.type}")`. Note: this changes
the log message for signal/email from `"Notification backend X is not yet implemented,
skipping."` to `"Unknown notification backend type: X"`. The behavior change is
intentional — signal/email are not recognized by this version of the code.

**Test**: Update `test_unknown_type_skipped` to also cover `"signal"` and `"email"` as
types, asserting zero notifiers returned. Add a warning-text assertion that verifies the
message contains `"Unknown notification backend type"` (prevents silent reversion to
old message text).

---

### B5 — DEFERRED: `config validate` sub-Typer refactor

**Not in scope for this bug-fix pass.**

The existing `@app.command(name="config")` + `typer.Argument("validate")` pattern
works correctly and all tests pass. Converting to a sub-Typer would change the CLI
argument order from `config --config PATH validate` → `config validate --config PATH`,
which is a breaking interface change. This belongs in a separate improvement PR, not
a bug-fix pass.

---

## Files Changed

| File | Changes |
|------|---------|
| `scholarposter/cli.py` | A1 filter order, A2 status output, A3 list display, B1 mastodon.me(), B2 retry guard, B4 dead path |
| `scholarposter/adapters/bluesky.py` | B3 sleep in finally |
| `tests/test_cli.py` | A1 order assertion, A2 last_error, A3 list-of-dicts, B2 lock test, B4 signal/email warning text |
| `tests/test_adapter_bluesky.py` | B3 sleep-on-exception test |

---

## Invariants

- Lock is always released: every `acquire_lock()` is immediately followed by `try: ... finally: release_lock()`, with no guards between them that could `raise` before the `try:`
- Filter runs before enrich: `evaluate_filters()` call precedes `pipeline.enrich()` call
- API calls in `retry` happen only after config validity is confirmed
- `time.sleep(0.2)` in `_build_facets` executes for every mention regardless of resolve outcome
- All tests pass, coverage ≥ 89%
