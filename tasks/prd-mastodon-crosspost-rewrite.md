# PRD: Academic Mastodon Cross-Poster Rewrite

## 1. Executive Summary

### Problem

The current Mastodon cross-posting system consists of three independent Python scripts (`m2blusky.py`, `m2li.py`, `m2tw.py`) that cross-post toots from fediscience.org to Bluesky and LinkedIn. A security and code quality audit revealed **3 critical security vulnerabilities**, **6 high-severity bugs**, and **~180 lines of duplicated code** across scripts. The system has no error recovery, no retry logic, no proper logging, silent failure modes that cause duplicate or lost posts, and hardcoded credentials throughout. It processes only one toot per run with no filtering, and crashes on empty timelines.

### Solution

A complete rewrite as a single, modular Python application (`scholarposter`) with a shared enrichment core, platform-specific adapters, SQLite state tracking, configurable content filtering, academic content enrichment (DOI/Crossref, text summarization), and failure notifications. Designed for fire-and-forget cron execution on a dedicated server with an optional CLI for manual operations.

### Success Criteria

- SC-1: Zero duplicate posts across 1000 consecutive runs (state integrity)
- SC-2: Graceful handling of empty timelines, API failures, and network timeouts (no crashes)
- SC-3: All credentials loaded from environment variables; no secrets in source code
- SC-4: All HTTPS connections use TLS verification (no `verify=False`)
- SC-5: Cross-post latency < 60 seconds per toot (including enrichment)
- SC-6: Configurable via a single YAML/TOML file without code changes
- SC-7: Notification delivered within 5 minutes of a posting failure

### Timeline

| Phase | Scope | Duration |
|-------|-------|----------|
| Phase 1 | Core architecture + Bluesky adapter | ~2 weeks |
| Phase 2 | LinkedIn adapter + enrichment pipeline | ~1 week |
| Phase 3 | Notifications + CLI + polish | ~1 week |

---

## 2. Goals

| ID | Goal | Metric |
|----|------|--------|
| G-1 | Eliminate all critical/high security vulnerabilities | 0 findings on re-audit |
| G-2 | Eliminate duplicate and lost posts | 0 incidents over 30-day observation |
| G-3 | Support configurable content filtering | Filter rules applied without code changes |
| G-4 | Provide academic content enrichment (DOI, summaries) | DOI-bearing links resolved with title + abstract in 95% of cases |
| G-5 | Deliver failure notifications | Notification received for 100% of posting failures |
| G-6 | Reduce code duplication to near zero | No function duplicated across adapters |
| G-7 | Enable easy addition of new target platforms | New adapter implementable in < 200 lines |

---

## 3. User Stories

### US-001: Cross-Post Latest Toot to Bluesky

**Description**: As an academic on Mastodon, I want my latest unprocessed toot automatically cross-posted to Bluesky so that my research reaches a wider audience without manual effort.

**Acceptance Criteria**:

- [ ] Fetches the oldest unprocessed toot from the Mastodon API (excluding replies)
- [ ] Strips HTML, preserves paragraph breaks and inline formatting intent
- [ ] Handles reblogs with "via OriginalAuthor:" attribution prefix
- [ ] Posts text with resolved facets (mentions, URLs, hashtags) to Bluesky
- [ ] Threads long posts into chunked replies respecting Bluesky's 300-grapheme limit
- [ ] If a chunk fails, the entire thread is abandoned and no partial state is written
- [ ] Updates state atomically only after successful post
- [ ] Exits cleanly with code 0 if no unprocessed toots exist
- [ ] Unit tests written and passing

**Priority**: High

**Dependencies**: Core architecture (US-007), Bluesky adapter

---

### US-002: Cross-Post Latest Toot to LinkedIn

**Description**: As an academic, I want my latest unprocessed toot cross-posted to LinkedIn as either an article share (with link card) or an image post, so that my professional network sees my research.

**Acceptance Criteria**:

- [ ] Authenticates via OAuth2 with automatic token refresh (see US-011, FR-53–FR-61)
- [ ] Uploads media attachments (images) via LinkedIn's asset registration flow
- [ ] Falls back to article share with URL preview when no media is attached
- [ ] Handles missing/empty URL descriptions gracefully (no empty title fields)
- [ ] Updates per-platform state atomically only after successful post
- [ ] Unit tests written and passing

**Priority**: High

**Dependencies**: Core architecture (US-007), LinkedIn adapter

---

### US-003: Filter Content Before Cross-Posting

**Description**: As a user, I want to configure which toots are cross-posted based on hashtags, content types, and other rules, so that only relevant content reaches each platform.

**Acceptance Criteria**:

- [ ] Configuration file supports a `filters` section with `skip_hashtags` (list of hashtags to exclude), `skip_content_types` (e.g., `poll`, `sensitive`), and `require_hashtags` (only post if any of these are present; empty = post all)
- [ ] Filters are evaluated per-platform (different rules for Bluesky vs LinkedIn)
- [ ] A skipped toot still advances the `since_id` state so it is not re-evaluated
- [ ] Filter evaluation is logged at DEBUG level for troubleshooting
- [ ] Unit tests cover: skip by hashtag, skip by content type, require-hashtag gate, empty filter (post all)

**Priority**: High

**Dependencies**: Core architecture (US-007)

---

### US-004: Enrich Links with DOI/Crossref Metadata

**Description**: As an academic, I want DOI-bearing links in my toots automatically enriched with paper titles and abstracts from Crossref, so that link cards on Bluesky and LinkedIn display meaningful academic metadata.

**Acceptance Criteria**:

- [ ] DOIs detected via regex (`10.\d{4,9}/[-.;()/:\w]+`) in toot text and attached URLs
- [ ] Crossref API queried with polite pool etiquette header (configurable email)
- [ ] Paper title and abstract (HTML-stripped) populate link card title and description
- [ ] Falls back gracefully to HTML OG tags when DOI lookup fails or times out (5s)
- [ ] Crossref results cached in `cache.json` to avoid redundant lookups within a configurable TTL (default 7 days)
- [ ] Unit tests cover: DOI found and resolved, DOI not found, Crossref timeout, cached result reuse

**Priority**: High

**Dependencies**: Enrichment pipeline (US-008)

---

### US-005: Summarize Linked Content

**Description**: As an academic, I want high-quality summarization of linked articles and papers, using the best available method (LLM when configured, extractive as fallback), so that cross-posted link cards display an informative description.

**Acceptance Criteria**:

- [ ] Summarization enabled/disabled via config (`enrichment.summarization.enabled`)
- [ ] Tiered summarization backends, configurable via `enrichment.summarization.backend`:
  - `gemini` (default if configured): invokes `gemini-cli` with an academic summarization prompt; requires a Gemini subscription; produces abstractive, context-aware summaries
  - `ollama`: invokes a local LLM via Ollama API (configurable model, e.g. `gemma2:9b`); free, offline-capable
  - `extractive`: sumy KL/LSA as lightweight fallback; no external dependencies
- [ ] Automatic fallback chain: gemini -> ollama -> extractive. If the preferred backend fails or times out, the next tier is tried silently.
- [ ] LLM prompt is academic-tuned and targets 1-sentence output: "Summarize the key finding of this academic paper/article in one sentence (~150 characters) for a social media link card. Be precise and specific." Prompt configurable via config file.
- [ ] Configurable max summary length (default 150 characters — optimized for link card visibility on Bluesky and LinkedIn)
- [ ] Supports HTML pages (extracted via `trafilatura`) and PDFs (extracted via `pymupdf4llm` as Markdown)
- [ ] Summary is always generated when content is available and cached on `LinkEnrichment.summary` for reuse (bibliography, logs). Adapters decide whether to display it based on card description priority (FR-20b).
- [ ] Summary is placed in the link card description field, NOT appended to the cross-posted text (see FR-26a, FR-34a)
- [ ] Card description priority is three-tiered (see FR-20b):
  1. **DOI-enriched links** (any link type): Crossref abstract always wins. Fall back to summary if Crossref returned no abstract, then OG description.
  2. **Files** (PDFs, documents — no DOI): summary first, OG description as fallback
  3. **Web pages** (HTML — no DOI): OG description first, summary as fallback if OG is missing
  4. **Terminal fallback**: empty string (card renders with title + thumbnail only)
- [ ] Priority logic lives as a `card_description` property on `LinkEnrichment` model (not in adapters). Adapters read `link.card_description` directly. Similarly, `card_title` property resolves: Crossref title → OG title → extracted title → empty string.
- [ ] Link type detection: Content-Type header from resolved URL (after FR-14 unshortening); fall back to URL extension heuristics on resolved URL (.pdf, .doc, .docx, .ppt, .pptx → file; all else → web page). Stored as `link_type: LinkType` enum on `LinkEnrichment` (see FR-15a).
- [ ] When a post has media attachments and no link card is generated (Bluesky/LinkedIn only support one embed type), the summary is dropped — the original Mastodon post already provides context
- [ ] External content passed to LLM backends (gemini-cli, ollama) via stdin only, never as CLI arguments. Subprocess invoked with `shell=False`. (see FR-20d)
- [ ] All card text fields (description, title) are sanitized before API submission: NFC Unicode normalization, strip C0/C1 control characters, strip Unicode bidirectional overrides (U+202A–U+202E) and zero-width chars (U+200B, U+FEFF), hard-cap at `max_chars` regardless of backend output. (see FR-20e)
- [ ] Summarization timeout configurable per backend: gemini (30s), ollama (30s), extractive (10s)
- [ ] **Test specifications** (RED tests for new FRs):
  - FR-15a: PDF URL → `link_type == LinkType.FILE`; HTML URL → `LinkType.WEBPAGE`; unknown extension → `WEBPAGE`; HEAD failure → fallback to extension; classification uses resolved URL not original
  - FR-20b: DOI link with abstract → card_description == abstract; DOI without abstract → falls back to summary; PDF without DOI → summary; webpage with OG → OG; webpage without OG → summary; both missing → empty string
  - FR-20c: image post with summary → summary not in output text; link card post → summary in card description
  - FR-20d: content with shell metacharacters ($(), backticks) → no command injection
  - FR-20e: description with Unicode bidi overrides → stripped; description exceeding max_chars → truncated
  - FR-26a/FR-34a: Bluesky/LinkedIn card description matches `link.card_description`; title matches `link.card_title`
  - Multi-link: threaded Bluesky posts use most-enriched URL per chunk; first chunk prefers images over card; DOI URL in chunk 1 with images → promoted to chunk 2 card; ties broken by first appearance

**Priority**: High (upgraded — core differentiator for academic users)

**Dependencies**: Enrichment pipeline (US-008)

---

### US-006: Receive Failure Notifications

**Description**: As a user, I want to be notified when a cross-post fails, so that I can investigate and manually re-post if needed.

**Acceptance Criteria**:

- [ ] Notification backends configurable in config: `ntfy` (push to Android, free), `signal-cli`, `email` (SMTP)
- [ ] At least one backend must be implemented at launch; others can be stubs
- [ ] Notification includes: toot ID, target platform, error type, timestamp
- [ ] Notification sent within 60 seconds of failure
- [ ] Notification failures themselves are logged but do not crash the main process
- [ ] Rate-limited: max 1 notification per platform per run (no spam on bulk failures)
- [ ] Unit tests cover: notification dispatch, notification failure handling, rate limiting

**Priority**: Medium

**Dependencies**: Core architecture (US-007)

---

### US-007: Core Architecture and Shared Modules

**Description**: As a developer, I want a clean, modular architecture with shared enrichment logic, so that adding new platforms requires only a new adapter.

**Acceptance Criteria**:

- [ ] `UnifiedPost` Pydantic model captures: text, media attachments (with alt text), URLs, hashtags, is_reblog, original_author, source_url, enrichment data
- [ ] `MastodonCollector` class fetches toots, strips HTML, extracts media, resolves reblogs
- [ ] `BaseAdapter` abstract class defines the adapter interface: `post(unified_post) -> Result`
- [ ] `StateManager` class manages two JSON files: `state.json` (per-platform post tracking) and `cache.json` (enrichment cache with TTL). Both written atomically via temp file + `os.rename()`.
- [ ] Atomic state updates: state written only after confirmed successful post
- [ ] File-based lock (`fcntl.flock`) prevents concurrent runs from double-posting
- [ ] All HTTP calls use `httpx` with TLS verification enabled, configurable timeout (default 15s)
- [ ] Structured logging via `loguru` with configurable level and output (file + stderr)
- [ ] Configuration loaded from `config.toml` with Pydantic validation
- [ ] All credentials from environment variables (`.env` via `python-dotenv`)
- [ ] Unit tests for: UnifiedPost model, state manager read/write/atomic-rename, cache expiry pruning, file locking, config loading

**Priority**: Critical

**Dependencies**: None (foundational)

---

### US-008: Enrichment Pipeline

**Description**: As a developer, I want a composable enrichment pipeline that processes URLs, DOIs, and media before handing off to adapters.

**Acceptance Criteria**:

- [ ] Pipeline stages: URL unshortening -> content-type detection -> HTML/PDF scraping -> DOI lookup -> summarization -> media download/resize
- [ ] Each stage is independently skippable via config
- [ ] Each stage has its own timeout (default 10s) and logs failures at WARNING level without aborting the post
- [ ] URL unshortening follows redirects with `httpx` (max 5 redirects, 10s timeout)
- [ ] Content-type detection via HTTP HEAD + `Content-Type` header (no blind GET of large files)
- [ ] HTML article text extraction via `trafilatura` (F1=0.958 on extraction benchmarks); OG meta tags via `BeautifulSoup` + `lxml`
- [ ] PDF text extraction via `PyMuPDF` / `pymupdf4llm` (outputs structured Markdown preserving headings — ideal as LLM summarization input); metadata (title, dc:description) via PyMuPDF XMP/DocInfo; page limit configurable (default 20)
- [ ] Image resizing via `Pillow`; respects platform limits: Bluesky (1MB), LinkedIn (varies by endpoint)
- [ ] Video processing via `PyAV`: format detection, duration/size probing, codec check, optional remux/compress to fit platform limits (Bluesky 50MB, LinkedIn 200MB) — no frame-by-frame decoding for metadata-only operations
- [ ] Unit tests for each pipeline stage in isolation

**Priority**: High

**Dependencies**: Core architecture (US-007)

---

### US-009: Handle All Supported Media Types

**Description**: As a user, I want all media types supported by target platforms to be cross-posted correctly.

**Acceptance Criteria**:

- [ ] Images: JPEG, PNG, GIF, WebP — converted to platform-required format if needed
- [ ] Videos: MP4 uploaded where platform supports it (Bluesky: yes; LinkedIn: via asset API); probed via `PyAV` for duration/codec/size without full decode
- [ ] Oversized videos compressed via `PyAV` remux or FFmpeg subprocess (configurable quality target)
- [ ] Alt text preserved from Mastodon media attachments
- [ ] Media type support configurable per platform in config (e.g., disable video for LinkedIn)
- [ ] Oversized images resized/compressed via `Pillow` to fit platform limits before upload
- [ ] Media download timeout: 30s; skip media on timeout (post text-only with warning log)
- [ ] Unit tests cover: image format conversion, video probing, alt text passthrough, oversized media compression, timeout handling

**Priority**: Medium

**Dependencies**: Enrichment pipeline (US-008)

---

### US-010: CLI Interface

**Description**: As a user, I want a CLI for manual operations like dry-run, status checks, and retrying failed posts.

**Acceptance Criteria**:

- [ ] Built with `typer`; entry point: `scholarposter` (or `python -m scholarposter`)
- [ ] `run` subcommand: default fire-and-forget mode (what cron calls); `--dry-run` flag prints what would be posted without posting; `--platform bluesky|linkedin|all` to target specific platforms
- [ ] `status` subcommand: shows last processed toot ID per platform, pending count, last error
- [ ] `retry` subcommand: `--toot-id <id>` re-attempts a failed post
- [ ] `set-watermark` subcommand: set the `last_toot_id` watermark (see US-016)
- [ ] `config validate` subcommand: validates config file and prints resolved values (secrets masked)
- [ ] `--verbose` / `--quiet` global flags control log level
- [ ] Exit codes: 0 = success or nothing to do, 1 = posting error, 2 = configuration error
- [ ] Unit tests for CLI argument parsing

**Priority**: Low

**Dependencies**: Core architecture (US-007)

---

### US-011: Automated LinkedIn Authentication

**Description**: As an academic running scholarposter on a server, I want LinkedIn authentication fully automated so that I never manually exchange codes or copy tokens — I run one CLI command, click "Allow" in my browser, and everything else (callback capture, token exchange, URN lookup, token storage) happens automatically.

**Background**: LinkedIn's `offline_access` scope (required for refresh tokens) is restricted to partner programs and not available to standard developer apps. Access tokens expire after 60 days with no auto-refresh. On expiry, the user must re-run `scholarposter auth linkedin`. This is the same pattern as Mastodon auth.

**Acceptance Criteria**:

- [ ] `auth` is a Typer sub-app (`auth_app = typer.Typer(); app.add_typer(auth_app, name="auth")`), enabling future `auth bluesky` extension
- [ ] `scholarposter auth linkedin` handles the complete OAuth 2.0 authorization code flow
- [ ] Prerequisite check: if `LINKEDIN_CLIENT_ID` or `LINKEDIN_CLIENT_SECRET` is missing from `.env`, print setup instructions and exit 2. Instructions reference "Share on LinkedIn" (or "Community Management API") + "Sign In with LinkedIn using OpenID Connect" as required products.
- [ ] Generates a CSRF `state` token (`secrets.token_urlsafe(32)`) and appends to the authorization URL. Scopes: `openid profile w_member_social`.
- [ ] **Two auth modes** depending on environment:
  - **Desktop mode**: local HTTP server on `127.0.0.1`, port 8080 (`--port`), opens browser, captures callback with state validation
  - **Headless mode**: prints URL, user pastes callback URL, parses code + state
- [ ] Exchanges code for tokens via POST to `https://www.linkedin.com/oauth/v2/accessToken`. Response contains `access_token` and `expires_in`. **No refresh token expected** — LinkedIn restricts `offline_access` to partner programs.
- [ ] Fetches Member URN via `GET https://api.linkedin.com/v2/userinfo` → `urn:li:person:{sub}`
- [ ] Atomic `.env` writes. Stores: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_OWNER_URN`, `LINKEDIN_TOKEN_EXPIRES_AT` (ISO 8601). No refresh token fields.
- [ ] `.env` path: `config.parent / ".env"`.
- [ ] On success: prints "LinkedIn authorized. Token expires {date}. Credentials saved to .env."
- [ ] **Token expiry handling**: on 401 in `_dispatch_post()`, print "LinkedIn token expired. Run `scholarposter auth linkedin` to re-authorize.", send notification, skip LinkedIn. **No auto-refresh** — browser interaction required. Same pattern as Mastodon.
- [ ] **Expiry warning**: 7 days before `LINKEDIN_TOKEN_EXPIRES_AT`, send notification AND log WARNING. "LinkedIn token expires on {date}. Run `scholarposter auth linkedin` to re-authorize." One per day max (track via `PlatformState.refresh_warning_last_sent`). Surface in `scholarposter status`.
- [ ] **Backward compatibility**: existing users with manual `LINKEDIN_ACCESS_TOKEN` but no `LINKEDIN_TOKEN_EXPIRES_AT` work normally — expiry check is skipped when the field is absent. No forced migration for users with working tokens.
- [ ] `scholarposter status` shows: "LinkedIn: token expired — run `scholarposter auth linkedin`" on 401 state, or "LinkedIn: token expires in N days" in normal mode.
- [ ] **Test specifications**:
  - Auth happy path (desktop): mock OAuth endpoints → tokens written to .env, URN fetched
  - Auth happy path (headless): mock stdin paste → same result
  - `error=access_denied` callback → "Authorization denied." exit 2
  - Invalid `state` → rejected, exit 2
  - Malformed pasted URL → parse error, exit 2
  - Timeout → "timed out" exit 2
  - Missing CLIENT_ID/SECRET → setup instructions, exit 2
  - Port conflict → "Port {port} is in use", exit 2
  - Token exchange failure → error message, exit 2
  - 401 at runtime → "token expired" message, notification sent, LinkedIn skipped
  - Expiry warning at 7 days → notification sent, WARNING logged, dedup same day
  - Legacy token (no LINKEDIN_TOKEN_EXPIRES_AT) → works normally, no warning
  - Atomic .env write crash → original unchanged
  - Successful re-auth after expired → auth_status reset

**Priority**: High

**Dependencies**: Core architecture (US-007), Notification system (US-006)

---

### US-012: Automated Mastodon Authentication

**Description**: As an academic setting up scholarposter, I want Mastodon authentication fully automated so that I never run manual Python snippets — I run one CLI command, click "Allow" in my browser, and the app registration, OAuth token capture, storage, and config update happen automatically.

**Background**: Mastodon 4.4.0 removed password-based authentication. The `log_in(email, password)` flow no longer works. All authentication must use the OAuth authorization code flow: browser redirect → user clicks "Authorize" → code captured → exchanged for token. This is the same pattern used for LinkedIn (US-011) and reuses the existing callback infrastructure in `scholarposter/auth/callback.py`.

**Key difference from LinkedIn**: Mastodon tokens do not expire unless manually revoked. There are no refresh tokens. Auto re-create on 401 requires the user to re-run `scholarposter auth mastodon` (browser interaction is mandatory — no silent background re-auth possible).

**Acceptance Criteria**:

- [ ] `scholarposter auth mastodon` added to the existing `auth` Typer sub-app (FR-53)
- [ ] Prompts for instance URL only. **No email or password prompt** — authentication happens in the browser.
- [ ] **Instance URL normalization**: auto-prepend `https://` if missing. Strip trailing slashes.
- [ ] **Non-interactive mode**: if `MASTODON_INSTANCE` is in `.env`, use it without prompting. Always print the success message.
- [ ] App registration: `Mastodon.create_app("scholarposter", api_base_url=instance, redirect_uris=redirect_uri, scopes=["read"], to_file=client_cred_path)`. Sets `0600`. Scope `read` is sufficient for fetching toots.
- [ ] OAuth code flow using existing callback infrastructure:
  - **Desktop mode**: `Mastodon(client_id=client_cred_path, api_base_url=instance).auth_request_url(redirect_uris=redirect_uri, scopes=["read"])` → open in browser + start callback server on `127.0.0.1:{port}` (reuses `wait_for_callback_desktop` from `auth/callback.py`)
  - **Headless mode**: print the auth URL, prompt for pasted callback URL (reuses `wait_for_callback_headless`). Mastodon.py also supports `urn:ietf:wg:oauth:2.0:oob` as redirect URI for out-of-band flow — in headless mode, use OOB redirect and prompt user to paste the code directly.
- [ ] Token exchange: `mastodon.log_in(code=auth_code, redirect_uri=redirect_uri, scopes=["read"], to_file=user_cred_path)`. Sets `0600`.
- [ ] Stores `MASTODON_INSTANCE` in `.env` via atomic write (FR-58). **No email or password stored** — they are not needed (tokens are obtained via browser OAuth, not credentials).
- [ ] Updates `config.toml` with `[mastodon] instance` and `credentials_file` via `tomli_w`.
- [ ] **Re-run idempotency**: if `.secret` files exist, warn "Existing credentials will be overwritten. Continue? [y/N]" in interactive mode.
- [ ] **Mastodon client factory** `_build_mastodon_client(cfg, env_path)`: centralizes 3 construction sites. Calls `account_verify_credentials()`. On 401: print "Mastodon token revoked. Run `scholarposter auth mastodon` to re-authorize." Send notification. Exit 1. On other errors: log WARNING, return client anyway. **No auto re-create** — OAuth code flow requires browser interaction.
- [ ] **Backward compatibility**: existing users with valid `.secret` files work normally. The factory only triggers re-auth message on 401.
- [ ] On success: "Mastodon authorized. Credentials saved to {path}."
- [ ] On instance unreachable: "Could not reach {instance}. Check the URL and try again." Exit 2.
- [ ] On user denying authorization: "Authorization denied." Exit 2.
- [ ] On callback timeout: "Authorization timed out." Exit 2.
- [ ] **Test specifications** (RED tests):
  - Auth happy path (desktop): mock create_app + auth_request_url + callback + log_in(code=) → .secret files written, config.toml updated, .env has MASTODON_INSTANCE
  - Auth happy path (headless/OOB): mock auth URL + pasted code → same result
  - Instance unreachable → "Could not reach" message, exit 2
  - URL normalization: "fediscience.org" → "https://fediscience.org"
  - Re-run overwrites with confirmation in interactive mode
  - Authorization denied (error= in callback) → "denied" message, exit 2
  - Callback timeout → "timed out" message, exit 2
  - Token validation success → run proceeds normally
  - Token validation 401 → "token revoked, re-run auth" message, notification sent, exit 1
  - Token validation network error (non-401) → WARNING logged, run proceeds
  - Config.toml update preserves existing sections
  - .secret file permissions 0600

**Priority**: High

**Dependencies**: Core architecture (US-007), auth sub-app (US-011/FR-53), callback infrastructure (FR-54/FR-55), env_writer (FR-58), tomli_w

---

### US-016: Set Crossposting Watermark

**Description**: As a user starting scholarposter for the first time (or resuming after a gap), I want to set the crossposting watermark to a specific toot or date so that the first automated run starts from exactly the right point — not from the beginning of my timeline.

**Background**: `state.json` tracks `last_toot_id` per platform. The `run` command fetches toots with `min_id=last_toot_id` (strictly after that ID). Without a watermark command, users must manually edit `state.json` to set the starting point. The legacy `migration.py` module (which read `lasttoot*.txt` files from the old shell-script system) is removed as part of this US — no active user still has those files.

**Acceptance Criteria**:

- [ ] `scholarposter set-watermark` subcommand with `--config PATH`, `--platform bluesky|linkedin|all` (default: `all`), `--dry-run`, `--yes / -y` (skip confirmation). Help text for `--date` must read: `"Requires Mastodon credentials configured in config.toml"`.
- [ ] Exactly one of `--toot-id INT`, `--toot-url TEXT`, or `--date YYYY-MM-DD` is required. Zero or >1 provided → exit 2 with explicit message: `"Error: exactly one of --toot-id, --toot-url, or --date is required"` followed by a usage example for each mode.
- [ ] `--toot-id ID`: sets `last_toot_id = ID` on each selected platform. Semantics: the toot with this ID is the last one treated as already-processed; the next `run` fetches toots strictly after it (`min_id=ID`).
- [ ] `--toot-url URL`: extracts the numeric toot ID from URLs matching `https://<host>/@<user>/<id>` or `https://<host>/users/<user>/statuses/<id>`. On parse failure, exits 2 with `"Cannot parse toot ID from URL: {url}"`. Then applies `--toot-id` semantics.
- [ ] `--date YYYY-MM-DD`: pages the Mastodon timeline newest-first to find the most recent toot published before midnight UTC on the given date; sets `last_toot_id` to that toot's ID. See FR-99 for full algorithm spec. When `last_toot_id` resolves to `None` (no toot before the date), the key is **deleted** from the platform state dict (not written as `null`) — consistent with `update_platform_state()`'s `None`-removal convention.
- [ ] Confirmation prompt shows the resolved value AND explains the effect: `"Set watermark for {platform}: last_toot_id = {value}\nThe next 'run' will process toots after this. Continue? [y/N]"`. Skipped when `--dry-run` or `--yes` is passed. User rejecting the prompt → exits 0, state unchanged.
- [ ] State written via `state_mgr.update_platform_state(platform, PlatformState(last_toot_id=value))` — this preserves all other fields in the platform dict (e.g., `last_status`, `last_error`) via merge semantics and calls `_atomic_write()` internally. **Never** call `_atomic_write()` directly from this command.
- [ ] Advisory lock acquired via the **same** `StateManager` instance used for the write (`state_mgr.acquire_lock()`), not by opening a second file descriptor. On `acquire_lock()` returning `False` (EAGAIN), print `"Another scholarposter process is running"` to stderr and exit 1. This is the safe and correct outcome when `run` holds the lock; do NOT retry or loop.
- [ ] `--dry-run`: prints `"[dry-run] Would set {platform} last_toot_id = {value}"` per platform and exits 0 without touching state or acquiring the lock.
- [ ] Removes `scholarposter/migration.py` and `tests/test_migration.py` in the **same commit**. Also updates `README.md` to remove the `migration.py` file-tree entry and the "Legacy migration" code block, replacing both with a `set-watermark` usage example.
- [ ] **Test specifications**:
  - `--toot-id` sets correct value in state.json for each platform combination (bluesky, linkedin, all)
  - `--toot-id` preserves pre-existing fields (`last_status`, `last_error`) in the platform dict
  - `--toot-url` parses both URL formats (`@<user>/<id>` and `users/<user>/statuses/<id>`); fails cleanly on unrecognized URL
  - `--date` with a date that bisects the timeline → correct toot ID found (newest toot before midnight UTC)
  - `--date` exact midnight UTC boundary: toot at 23:59:59 the day before → included as watermark; toot at 00:00:00 on the target date → NOT included
  - `--date` before all account toots → `last_toot_id` key deleted from state (not written as `null`)
  - `--date` future date (all toots before cutoff) → `last_toot_id` set to most recent toot ID
  - `--date` empty account (first `account_statuses()` call returns `[]`) → immediately deletes `last_toot_id` key (account has no toots at all)
  - `--date` paging exhausts timeline (later page returns empty list after one or more non-empty pages) → treats as "no toot before date", deletes `last_toot_id` key
  - `--date` network error / 429 mid-page → exits 1 with error message; state unchanged
  - `--date` missing credentials → exit 2 with message
  - `--dry-run` does not write state.json or acquire the lock; for `--date` still performs the API lookup and prints the resolved value
  - Lock contention (`acquire_lock()` returns False) → exits 1 with `"Another scholarposter process is running"`
  - Zero anchor flags provided → exit 2 with explicit usage message
  - Multiple anchor flags provided → exit 2 with explicit usage message
  - Confirmation prompt shown without `--yes`; user enters "n" → exits 0, state unchanged
  - `--yes` skips confirmation prompt

**Priority**: Medium

**Dependencies**: State management (US-007, FR-37–FR-40), Mastodon auth (.env credentials)

---

## 4. Functional Requirements

### Mastodon Ingestion

| ID | Requirement |
|----|-------------|
| FR-1 | Fetch toots via `Mastodon.py` using `account_statuses(min_id=since_id, limit=50, exclude_replies=True)` |
| FR-2 | Process only the oldest unprocessed toot per platform per run (determined by per-platform state in SQLite) |
| FR-3 | Unwrap reblogs: use reblog content but prefix with "via {display_name}:" and preserve original author's URL |
| FR-4 | Strip HTML to plain text preserving paragraph breaks (`<p>` -> `\n\n`, `<br>` -> `\n`) |
| FR-5 | Extract all media attachments with URLs, alt text, MIME type, and dimensions |
| FR-6 | Extract all URLs from toot content via regex after HTML stripping |
| FR-7 | Handle empty timeline gracefully: log at INFO level, exit 0 |

### Content Filtering

| ID | Requirement |
|----|-------------|
| FR-8 | Evaluate filter rules before enrichment (fail fast, don't waste API calls) |
| FR-9 | `skip_hashtags`: list of hashtags (case-insensitive) that cause a toot to be skipped |
| FR-10 | `skip_content_types`: list of content flags (`sensitive`, `poll`, `media_only`) |
| FR-11 | `require_hashtags`: if non-empty, toot must contain at least one to be posted |
| FR-12 | Skipped toots advance `since_id` to prevent re-evaluation |
| FR-13 | Filters are per-platform: a toot can be skipped for LinkedIn but posted to Bluesky |

### Enrichment

| ID | Requirement |
|----|-------------|
| FR-14 | URL unshortening: follow up to 5 redirects with 10s timeout; return original URL on failure |
| FR-15 | Content-type detection: HTTP HEAD request with 5s timeout; fall back to extension-based guessing |
| FR-15a | Link type classification: classify each enriched link as `LinkType.FILE` or `LinkType.WEBPAGE` using the **resolved** URL (after FR-14 unshortening). Primary: validate Content-Type header against an allowlist of known file MIME types (`application/pdf`, `application/msword`, `application/vnd.openxmlformats-officedocument.*`); unrecognized or missing Content-Type defaults to `WEBPAGE`. Secondary fallback: URL extension heuristics on resolved URL (`.pdf`, `.doc`, `.docx`, `.ppt`, `.pptx` → `FILE`; all else → `WEBPAGE`). Stored as `link_type: LinkType` enum field on `LinkEnrichment` (default `WEBPAGE`). `LinkType` is a `str` enum with values `"file"` and `"webpage"`. |
| FR-16 | HTML content extraction: OG meta tags (title, description, image) via `BeautifulSoup` + `lxml`; full article body text via `trafilatura` for summarization input |
| FR-17 | PDF extraction: title + metadata via `PyMuPDF` XMP/DocInfo API; full text via `pymupdf4llm` as structured Markdown (preserves headings, ideal for LLM input); page limit configurable (default 20) |
| FR-18 | DOI detection: regex `10.\d{4,9}/[-.;()/:\w]+` in URLs and toot text |
| FR-19 | Crossref lookup: query `Works().doi(udoi)` with polite etiquette header; 5s timeout; cache results in SQLite with 7-day TTL |
| FR-20 | Summarization: tiered backend system with automatic fallback chain: (1) `gemini` — invoke `gemini-cli` with academic prompt, 30s timeout; (2) `ollama` — local LLM via HTTP API, configurable model, 30s timeout; (3) `extractive` — sumy KL/LSA, 10s timeout. Each backend produces max 150 chars (configurable). Backend selection via config with fallback on failure. |
| FR-20a | LLM summarization prompt: "Summarize the key finding of this academic paper/article in one sentence (~150 characters) for a social media link card. Be precise and specific." Prompt configurable via config file. |
| FR-20b | Card description priority (three-tiered, implemented as `card_description` property on `LinkEnrichment`): **(1) DOI-enriched links** (any link_type): Crossref abstract first → summary fallback → OG description fallback → empty string. **(2) File links** (no DOI): summary first → OG description fallback → empty string. **(3) Webpage links** (no DOI): OG description first → summary fallback → empty string. The pipeline always generates and caches summaries on `LinkEnrichment.summary`; the property resolves priority at read time. Similarly, `card_title` property resolves: Crossref title → OG title → extracted title → empty string. |
| FR-20c | Summary is never appended to cross-posted text. It is placed exclusively in the link card description field via `link.card_description`. When no link card is generated (e.g. image-only posts), the summary is dropped. |
| FR-20d | LLM prompt assembly safety: external content (PDF text, HTML body) MUST be passed to gemini-cli and ollama backends via stdin, never as CLI arguments. Subprocess invoked with `shell=False`. Content is truncated to a safe byte limit before passing. This prevents command injection from adversarial content. |
| FR-20e | Card text sanitization: a shared `sanitize_card_text(text: str, max_chars: int) -> str` utility is applied to all card description and title fields before API submission. It: (1) normalizes to NFC Unicode, (2) strips C0/C1 control characters, Unicode bidirectional overrides (U+202A–U+202E), zero-width characters (U+200B, U+FEFF), and null bytes, (3) hard-truncates at `max_chars`. This is the single enforcement point for the character limit, independent of whether the LLM honored the prompt constraint. |
| FR-21 | Relative URL resolution: use `urllib.parse.urljoin` (not string concatenation) |
| FR-21a | Video processing: probe format, duration, codec, and file size via `PyAV` without frame decoding; compress/remux oversized videos to fit platform limits |

### Bluesky Posting

| ID | Requirement |
|----|-------------|
| FR-22 | Authenticate via `atproto.Client.login()` with credentials from env vars |
| FR-23 | Parse facets: resolve `@mentions` to DIDs, detect URLs and hashtags with byte-accurate indexing |
| FR-24 | Thread long posts: chunk at 300 graphemes (not bytes) using `textwrap.wrap` with `break_long_words=False` |
| FR-25 | Thread parts numbered as `{chunk} {n}/{total}` for multi-part posts |
| FR-26 | Embed external link card with title, description, and thumbnail (resized to 400x400, JPEG, < 1MB) |
| FR-26a | Link card fields read directly from `link.card_description` and `link.card_title` (FR-20b properties). Sanitized via FR-20e before submission. For threaded posts: first chunk uses images if media is present (no card); otherwise uses the most-enriched URL appearing in that chunk. Subsequent chunks each embed the most-enriched URL from their respective chunk text. **Promotion rule**: if the first chunk used images and suppressed a link card, the post-level most-enriched URL is promoted to chunk 2 even if that URL does not appear in chunk 2's text. Ties at the same enrichment tier broken by first appearance in pre-chunked text. "Most enriched" = DOI-resolved > PDF > HTML with OG > bare link. |
| FR-27 | Upload images via `repo.upload_blob`; support JPEG, PNG, GIF, WebP (convert WebP to JPEG if needed) |
| FR-27a | Upload videos via Bluesky video upload service; support MP4; respect 50MB / 60s limits |
| FR-28 | If any chunk in a thread fails, abort the entire thread and mark the toot as `failed` in state |
| FR-29 | Rate-limit mention resolution: max 10 handle resolves per post, 200ms delay between requests |

### LinkedIn Posting (Community Management API)

| ID | Requirement |
|----|-------------|
| FR-30 | Authenticate via OAuth2 with automatic token refresh. Tokens stored in `.env` file (access_token, refresh_token, client_id, client_secret, owner_urn, expiry timestamps). See FR-53 through FR-60. |
| FR-31 | Post via Community Management API (`/rest/posts`) with `PUBLISHED` lifecycle state (replaces legacy UGC endpoint) |
| FR-32 | Image posts: register upload via `/rest/images`, upload binary, attach to post as `content.media` |
| FR-33 | Video posts: initialize upload via `/rest/videos`, upload binary in chunks, poll for processing completion, attach to post |
| FR-34 | Article posts: attach URL with title and description from enrichment as `content.article` |
| FR-34a | Article card fields read directly from `link.card_description` and `link.card_title` (FR-20b properties). Sanitized via FR-20e before submission. Link selection: most-enriched URL from the post (DOI-resolved > PDF > HTML with OG > bare link). |
| FR-35 | Text-only posts: plain share when no media and no URL |
| FR-36 | Visibility: `PUBLIC` (configurable) |

### State Management

| ID | Requirement |
|----|-------------|
| FR-37 | `state.json` tracks per-platform state. Structure: `{"bluesky": {"last_toot_id": 12345, "last_status": "posted", "last_posted_at": "2026-03-31T12:00:00Z", "last_error": null}, "linkedin": {...}}` |
| FR-38 | `since_id` derived per platform: `state["<platform>"]["last_toot_id"]` |
| FR-39 | State written atomically: write to `state.json.tmp`, then `os.rename()` to `state.json`. Only after confirmed API success. |
| FR-40 | File lock (`fcntl.flock`, exclusive, non-blocking) on `scholarposter.lock` acquired at startup; exit 0 if lock already held |
| FR-41 | `cache.json` stores enrichment cache as `{"doi:10.1234/...": {"value": {...}, "expires_at": "2026-04-07T12:00:00Z"}, ...}`. Expired entries pruned on read. Written atomically (same temp+rename pattern). |

### Watermark Management

| ID | Requirement |
|----|-------------|
| FR-96 | `scholarposter set-watermark` subcommand. Flags: `--config PATH`, `--platform bluesky\|linkedin\|all` (default `all`), `--dry-run` (preview without writing), `--yes / -y` (skip confirmation prompt and proceed immediately). Exactly one of `--toot-id INT`, `--toot-url TEXT`, `--date DATE` is required; zero or >1 exits 2 with `"Error: exactly one of --toot-id, --toot-url, or --date is required"` followed by a usage example for each of the three modes. Help text for `--date` flag must read: `"Requires Mastodon credentials configured in config.toml"`. |
| FR-97 | `--toot-id ID`: sets `last_toot_id = ID` for each selected platform in `state.json`. The toot with this ID is treated as the last-processed toot; the next `run` fetches toots strictly after it (`min_id=ID`). |
| FR-98 | `--toot-url URL`: extracts the numeric toot ID from URLs matching `https://<host>/@<user>/<id>` or `https://<host>/users/<user>/statuses/<id>`. On parse failure exits 2 with `"Cannot parse toot ID from URL: {url}"`. Then applies FR-97 semantics. |
| FR-99 | `--date YYYY-MM-DD` algorithm: (1) Load config via `load_config(config)` and build the Mastodon client via the existing `_build_mastodon_client(cfg)` helper — same credential mechanism as all other commands (credentials file + instance from `config.toml`). If config load fails, exit per the standard config-error path. (2) Build cutoff = `datetime(YYYY, MM, DD, tzinfo=timezone.utc)` (midnight UTC on the given date). (3) Call `account_statuses(account_id, max_id=None, limit=40)` — newest-first. (4) For each toot on the page, if `toot["created_at"] < cutoff`: set `last_toot_id = int(toot["id"])` and stop. (5) If no toot on the page satisfies the condition and the page is non-empty: advance cursor via `max_id = toots[-1]["id"]` and repeat. (6) If the page is **empty** at any point, or if 500 pages (20,000 toots) are consumed without finding a toot before the cutoff: set `last_toot_id = None` and stop. (7) On network error or HTTP 429: log WARNING and exit 1 with the error message; do not write state. (8) When `last_toot_id = None`: the key is **deleted** from the platform state dict, not written as `null` — aligns with `update_platform_state()`'s `None`-removal convention, and means `get_since_id()` returns `None` → `run` fetches from the beginning. |
| FR-100 | State write calls `state_mgr.update_platform_state(platform, PlatformState(last_toot_id=value))` for each selected platform. This invokes `_atomic_write()` internally and preserves all other fields via merge semantics. Advisory lock acquired via `state_mgr.acquire_lock()` (the same `StateManager` instance used for the write — not a second FD). On `acquire_lock()` returning `False`: print `"Another scholarposter process is running"` to stderr and exit 1; do NOT retry. `--dry-run` skips lock acquisition and state write entirely. Confirmation prompt format (shown unless `--dry-run` or `--yes`): `"Set watermark for {platform}: last_toot_id = {value}\nThe next 'run' will process toots after this. Continue? [y/N]"`. `--yes` skips the prompt and proceeds immediately. User rejecting the prompt exits 0 with no state change. |
| FR-101 | Delete `scholarposter/migration.py` and `tests/test_migration.py` in the same commit as the `set-watermark` implementation. Update `README.md` to remove the `migration.py` file-tree entry and any "Legacy migration" code block; replace with a `set-watermark` usage example showing all three modes. |

### Notifications

| ID | Requirement |
|----|-------------|
| FR-42 | `ntfy.sh` backend: HTTP POST to configured topic (free, supports Android push, zero-setup) |
| FR-43 | `signal-cli` backend: send message via signal-cli REST API or dbus |
| FR-44 | `email` backend: send via SMTP with configurable server, port, credentials |
| FR-45 | Notification payload: `"Cross-post to {platform} failed for toot {id}: {error}"` |
| FR-46 | Max 1 notification per platform per run |
| FR-47 | Notification dispatch errors logged at WARNING; never crash the main process |

### Security

| ID | Requirement |
|----|-------------|
| FR-48 | All HTTPS requests use TLS verification (`verify=True`); no exceptions |
| FR-49 | All credentials loaded from environment variables via `python-dotenv` |
| FR-50 | No credentials in source code, config files, or logs |
| FR-51 | Log output redacts any string matching known credential patterns |
| FR-52 | `.env` file permissions checked at startup; warn if world-readable |

### LinkedIn OAuth Automation

| ID | Requirement |
|----|-------------|
| FR-53 | `auth` is a Typer sub-app (`auth_app = typer.Typer(); app.add_typer(auth_app, name="auth")`). `scholarposter auth linkedin` performs the OAuth 2.0 authorization code flow. Prerequisite: `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` in `.env`. If missing, print setup instructions referencing "Share on LinkedIn" (or "Community Management API") + "Sign In with LinkedIn using OpenID Connect" as required products. Exit 2. |
| FR-54 | Authorization URL includes CSRF `state` (`secrets.token_urlsafe(32)`). Scopes: `openid profile w_member_social`. **Desktop mode**: browser + callback server on `127.0.0.1`. **Headless mode**: print URL, user pastes callback URL. |
| FR-55 | **Desktop mode only**: local HTTP server on `127.0.0.1`, port 8080 (`--port`). Port conflict, state validation, error handling, 120s timeout — unchanged from prior spec. |
| FR-56 | Token exchange: POST `https://www.linkedin.com/oauth/v2/accessToken`, `Content-Type: application/x-www-form-urlencoded`, `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `client_secret`. Response contains `access_token` and `expires_in`. **No `refresh_token` expected** — LinkedIn restricts `offline_access` scope to partner programs. If `access_token` is absent, fail with error. |
| FR-57 | Member URN: `GET https://api.linkedin.com/v2/userinfo` → `urn:li:person:{sub}`. |
| FR-58 | Atomic `.env` writes via `write_env` (FR-58 pattern). Stores: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_OWNER_URN`, `LINKEDIN_TOKEN_EXPIRES_AT` (ISO 8601). **No** `LINKEDIN_REFRESH_TOKEN` or `LINKEDIN_REFRESH_EXPIRES_AT`. `CLIENT_ID`/`CLIENT_SECRET` are prerequisites, preserved. |
| FR-59 | **Removed** — no auto-refresh. LinkedIn tokens expire after 60 days with no refresh path. On 401 in `_dispatch_post()`: print "LinkedIn token expired. Run `scholarposter auth linkedin` to re-authorize.", send notification, skip LinkedIn. Same pattern as Mastodon (FR-71). |
| FR-60 | Expiry warning: 7 days before `LINKEDIN_TOKEN_EXPIRES_AT`, notify AND log WARNING. "LinkedIn token expires on {date}. Run `scholarposter auth linkedin` to re-authorize." One per day max (`PlatformState.refresh_warning_last_sent`, UTC date). Surface in `scholarposter status`. |
| FR-61 | **Simplified** — on 401: set `auth_status = "auth_expired"`, notify, skip LinkedIn. `scholarposter auth linkedin` resets `auth_status = "normal"` and `refresh_warning_last_sent = None`. No `refresh_failure_count` (no retry — single 401 = expired, re-auth required). |
| FR-62 | `PlatformState` fields: `auth_status: str = "normal"`, `refresh_failure_count: int = 0` (retained for schema compat), `refresh_warning_last_sent: Optional[date] = None`. Status output: "LinkedIn: token expired — run `scholarposter auth linkedin`" or "token expires in N days". |
| FR-63 | Backward compat: existing users with manual `LINKEDIN_ACCESS_TOKEN` but no `LINKEDIN_TOKEN_EXPIRES_AT` work normally — expiry check is skipped when the field is absent. No forced migration. |
| FR-64 | Log redaction: `code=` and `state=` patterns scoped to OAuth context. Redact `/oauth/v2/accessToken` response bodies and `Authorization: Bearer` headers. Unchanged. |

### Mastodon Authentication Automation

| ID | Requirement |
|----|-------------|
| FR-65 | `scholarposter auth mastodon` added to existing `auth` sub-app (FR-53). Prompts for instance URL only (auto-prefixes `https://` if missing, strips trailing `/`). **No email or password** — Mastodon 4.4.0+ requires OAuth code flow. Prints progress: "Registering app with {instance}..." then "Authorizing...". |
| FR-66 | Non-interactive: if `MASTODON_INSTANCE` is in `.env`, use it without prompting for the URL. The browser/callback step still requires user interaction (or headless paste). Always print success message on completion. |
| FR-67 | App registration: `Mastodon.create_app("scholarposter", api_base_url=instance, redirect_uris=redirect_uri, scopes=["read"], to_file=client_cred_path)` where `client_cred_path = config.parent / "pytooter_clientcred.secret"`. `chmod 0600` after write. Scope `read` is sufficient for timeline fetching. The `redirect_uri` is `http://localhost:{port}/callback` in desktop mode or `urn:ietf:wg:oauth:2.0:oob` in headless mode. |
| FR-68 | OAuth code flow: `mastodon = Mastodon(client_id=client_cred_path, api_base_url=instance)`. Generate auth URL via `mastodon.auth_request_url(redirect_uris=redirect_uri, scopes=["read"])`. **Desktop mode**: open URL in browser via `webbrowser.open()`, capture callback via `wait_for_callback_desktop(port, expected_state=None)` (Mastodon OAuth does not use a `state` parameter — the instance manages session state). **Headless mode**: use `urn:ietf:wg:oauth:2.0:oob` as redirect URI — Mastodon displays the code directly to the user, who pastes it into the terminal prompt. Exchange code for token: `mastodon.log_in(code=auth_code, redirect_uri=redirect_uri, scopes=["read"], to_file=user_cred_path)`. `chmod 0600`. |
| FR-69 | Stores `MASTODON_INSTANCE` in `.env` via `write_env` (FR-58). **No email or password stored** — OAuth code flow does not use them. Simpler and more secure than the password-based design. |
| FR-70 | Updates `config.toml`: read via `tomllib`, update `[mastodon]` section, write via `tomli_w`. Preserves all existing sections. **Re-run idempotency**: if `.secret` files exist, warn "Existing credentials will be overwritten. Continue? [y/N]" in interactive mode; overwrite silently in non-interactive. |
| FR-71 | Mastodon client factory `_build_mastodon_client(cfg, env_path)`: centralizes 3 construction sites. Calls `account_verify_credentials()`. On success: return client. On 401: print "Mastodon token revoked. Run `scholarposter auth mastodon` to re-authorize.", send notification, exit 1. **No auto re-create** — OAuth code flow requires browser interaction, cannot run unattended. On other network errors: log WARNING, return client anyway. |
| FR-72 | **Removed** — auto re-create via saved credentials is no longer possible. Password flow is dead (Mastodon 4.4.0+). On 401, the only option is user-initiated `scholarposter auth mastodon`. The factory (FR-71) handles this by printing the re-auth message and sending a notification. |
| FR-73 | Log redaction: the existing `_REDACT_SECRETS` and `_REDACT_OAUTH` patterns cover OAuth `code=` parameters from the callback. No Mastodon-specific password redaction needed (no password in the flow). The authorization code from the callback is short-lived and single-use. |

---

## 5. Non-Goals

| ID | Explicitly Out of Scope |
|----|------------------------|
| NG-1 | Twitter/X support (deprecated per user decision) |
| NG-2 | Bidirectional sync (replies on Bluesky/LinkedIn are not synced back to Mastodon) |
| NG-3 | Web UI or dashboard |
| NG-4 | Processing multiple toots per platform per run (only oldest unprocessed) |
| NG-5 | Real-time streaming (this is a cron-based batch processor) |
| NG-6 | Mastodon bot commands (no interactive features) |
| NG-7 | Content translation or language detection |
| NG-8 | Analytics or engagement tracking |
| NG-9 | Async/asyncio architecture (unnecessary for single-toot-per-run cron job) |

---

## 6. Design Considerations

### No UI Required

This is a headless CLI/cron application. All configuration is via TOML file and environment variables. The only user-facing output is log messages and notifications.

### Configuration File Structure

```toml
# config.toml

[mastodon]
instance = "https://fediscience.org"
credentials_file = "pytooter_usercred.secret"

[platforms.bluesky]
enabled = true

[platforms.bluesky.filters]
skip_hashtags = ["nobridge", "private"]
skip_content_types = ["sensitive"]
require_hashtags = []  # empty = post all

[platforms.bluesky.media]
enabled = true
max_image_size_kb = 950
max_video_size_mb = 50
supported_types = ["image/jpeg", "image/png", "image/gif", "image/webp", "video/mp4"]

[platforms.linkedin]
enabled = true

[platforms.linkedin.filters]
skip_hashtags = ["nobridge", "shitpost"]
skip_content_types = ["sensitive", "poll"]
require_hashtags = []

[platforms.linkedin.media]
enabled = true
max_image_size_kb = 5000
max_video_size_mb = 200
supported_types = ["image/jpeg", "image/png", "image/gif", "video/mp4"]

[platforms.linkedin.auth]
callback_port = 8080  # local port for OAuth callback server

[enrichment.crossref]
enabled = true
etiquette_email = "your-email@example.com"
cache_ttl_days = 7
timeout_seconds = 5

[enrichment.summarization]
enabled = true
backend = "gemini"  # "gemini", "ollama", "extractive" — falls back automatically on failure
max_chars = 150  # optimized for link card visibility (~150 chars visible on Bluesky/LinkedIn)
prompt = "Summarize the key finding of this academic paper/article in one sentence (~150 characters) for a social media link card. Be precise and specific."

[enrichment.summarization.gemini]
timeout_seconds = 30
# Uses gemini-cli with your existing subscription — no API key needed

[enrichment.summarization.ollama]
model = "gemma2:9b"
host = "http://localhost:11434"
timeout_seconds = 30

[enrichment.summarization.extractive]
algorithm = "kl"  # kl, lsa, luhn, text-rank
max_sentences = 5
timeout_seconds = 10

[enrichment.url_unshorten]
enabled = true
timeout_seconds = 10
max_redirects = 5

[[notifications.backends]]
type = "ntfy"
topic = "scholarposter"
server = "https://ntfy.sh"

# [[notifications.backends]]
# type = "email"
# smtp_host = "smtp.example.com"
# smtp_port = 587
# from = "alerts@example.com"
# to = "you@example.com"

[logging]
level = "INFO"  # DEBUG, INFO, WARNING, ERROR
file = "scholarposter.log"
rotation = "10 MB"
retention = "30 days"

[state]
state_file = "state.json"
cache_file = "cache.json"
lock_file = "scholarposter.lock"
```

---

## 7. Technical Specifications

### Architecture

```
scholarposter/
  __init__.py
  __main__.py              # typer CLI entry point
  cli.py                   # CLI command definitions
  config.py                # Pydantic config model + TOML loader
  models.py                # UnifiedPost, PostResult, State models
  collector.py             # MastodonCollector
  enrichment/
    __init__.py
    pipeline.py            # Orchestrates enrichment stages
    url.py                 # URL unshortening, content-type detection
    html.py                # OG tags (BeautifulSoup+lxml) + body text (trafilatura)
    pdf.py                 # Metadata + full text via PyMuPDF/pymupdf4llm
    doi.py                 # DOI detection + Crossref lookup
    summarizer.py          # Tiered: gemini-cli -> ollama -> sumy fallback
    media.py               # Image (Pillow) + video (PyAV) processing
  adapters/
    __init__.py
    base.py                # BaseAdapter ABC
    bluesky.py             # Bluesky AT Protocol adapter
    linkedin.py            # LinkedIn Community Management API adapter
  state.py                 # JSON StateManager + file locking (atomic temp+rename)
  notifications/
    __init__.py
    base.py                # BaseNotifier ABC
    ntfy.py                # ntfy.sh push notifications
    signal.py              # signal-cli backend
    email.py               # SMTP email backend
  filters.py               # Content filter evaluation
```

### Technology Stack

| Component | Library | Justification |
|-----------|---------|---------------|
| HTTP client | `httpx` | Modern, sync, connection pooling, proper TLS defaults |
| Data models | `pydantic` v2 | Config validation, serialization, type safety |
| CLI | `typer` | Fire-and-forget + subcommands, auto-generated `--help` |
| Logging | `loguru` | Structured logging, rotation, zero-config |
| State storage | JSON files + `os.rename()` | Plain text, inspectable with `cat`, no corruption risk, atomic writes via temp+rename |
| Mastodon API | `Mastodon.py` | Already in use, stable, well-documented |
| Bluesky API | `atproto` | Already in use, official AT Protocol SDK |
| HTML: OG tags | `beautifulsoup4` + `lxml` | Simple, reliable for structured meta tag extraction |
| HTML: body text | `trafilatura` | F1=0.958 on extraction benchmarks; multi-stage fallback (own heuristics -> jusText -> readability); best article text quality for summarization input |
| PDF extraction | `PyMuPDF` (`fitz`) + `pymupdf4llm` | 2x faster than pdfplumber; outputs structured Markdown preserving headings (ideal for LLM summarization); replaces both pdfplumber and pikepdf |
| Summarization (LLM) | `gemini-cli` (subprocess) | Abstractive, context-aware academic summaries; handles full paper context in one call; requires Gemini subscription |
| Summarization (local LLM) | `ollama` (HTTP API) | Free local alternative; configurable model; offline-capable |
| Summarization (extractive) | `sumy` | Lightweight fallback; KL/LSA algorithms; no external dependencies |
| Image processing | `Pillow` | Format conversion, resizing, compression; mature, no native deps |
| Video processing | `PyAV` | Cython bindings to FFmpeg; fast metadata probing without frame decode; remux/compress; actively maintained (replaces abandoned ffmpeg-python) |
| Crossref | `crossrefapi` | Polite pool support, DOI resolution |
| Config format | `tomllib` (stdlib 3.11+) + `tomli-w` (for writing) | Zero-dependency reads; native typed values; no PyYAML needed |
| Push notifications | `httpx` (to ntfy.sh) | No additional dependency; ntfy is a single HTTP POST; zero-setup Android push |

### State File Schemas

**`state.json`** — per-platform post tracking (inspectable with `cat state.json | python -m json.tool`):

```json
{
  "bluesky": {
    "last_toot_id": 113456789012345678,
    "last_status": "posted",
    "last_posted_at": "2026-03-31T12:34:56Z",
    "last_error": null
  },
  "linkedin": {
    "last_toot_id": 113456789012345670,
    "last_status": "failed",
    "last_posted_at": "2026-03-31T12:30:00Z",
    "last_error": "LinkedIn API 429: rate limited"
  }
}
```

**`cache.json`** — enrichment cache with TTL (auto-pruned on read):

```json
{
  "doi:10.1257/aer.20191777": {
    "title": "The Effect of Minimum Wages on Low-Wage Jobs",
    "abstract": "We estimate the effect of minimum wages...",
    "fetched_at": "2026-03-28T10:00:00Z",
    "expires_at": "2026-04-04T10:00:00Z"
  }
}
```

Both files are written atomically: write to `*.tmp`, then `os.rename()`. This guarantees the file is always valid JSON — a crash mid-write leaves only the `.tmp` file, and the original remains intact.

### Security Architecture

- **Credentials**: All from `os.environ` via `.env` file. No fallback to config file for secrets.
- **TLS**: `httpx` defaults to `verify=True`. No override anywhere in the codebase.
- **File permissions**: Startup check warns if `.env` is group/world-readable (`os.stat` + mode check).
- **Log redaction**: `loguru` filter strips patterns matching `Bearer `, `password=`, API keys > 20 chars.
- **State files**: `state.json`, `cache.json` permissions set to 0600 on creation. Atomic writes via temp+rename prevent corruption.

---

## 8. Testing & Validation

### Test Strategy

| Level | Scope | Tools |
|-------|-------|-------|
| Unit | Models, filters, enrichment stages, state manager | `pytest`, `pytest-mock` |
| Integration | Mastodon fetch -> enrich -> post (mocked APIs) | `pytest`, `respx` (httpx mock) |
| End-to-end | Full run with `--dry-run` against live Mastodon | Manual + CI script |

### Key Test Scenarios

| ID | Scenario | Type |
|----|----------|------|
| T-1 | Empty timeline: exits cleanly with code 0 | Unit |
| T-2 | Toot with DOI link: Crossref enrichment populates title/abstract via PyMuPDF metadata | Unit |
| T-3 | Toot with `#nobridge` hashtag: skipped, state advanced | Unit |
| T-4 | Bluesky thread: 800-char toot split into 3 chunks with correct facets | Unit |
| T-5 | Bluesky chunk 2 fails: entire thread marked `failed`, no partial state | Unit |
| T-6 | Concurrent run: second instance exits immediately due to `scholarposter.lock` | Integration |
| T-7 | Network timeout on Crossref: falls back to OG tags via trafilatura/BeautifulSoup | Unit |
| T-7a | Gemini summarization timeout: falls back to ollama, then extractive | Unit |
| T-7b | Video probe: PyAV reads duration/codec without decoding frames | Unit |
| T-7c | trafilatura body extraction vs BeautifulSoup OG tags: both populated independently | Unit |
| T-7d | pymupdf4llm produces Markdown output from academic PDF | Unit |
| T-8 | LinkedIn image upload: registers asset, uploads binary, posts UGC | Integration |
| T-9 | Notification on failure: ntfy.sh receives POST with correct payload | Unit |
| T-10 | Config validation: missing required field raises clear error | Unit |
| T-11 | `.env` permission check: warns on world-readable | Unit |
| T-12 | Summarization timeout: skipped silently, post proceeds without summary | Unit |
| T-13 | Reblog unwrap: attribution prefix correct, original URL preserved | Unit |
| T-14 | PDF with no title: falls back to pdftitle heuristic, then "PDF" | Unit |
| T-15 | `--dry-run`: prints formatted output, makes zero API calls, writes zero state | Integration |

---

## 9. Risks & Mitigation

| ID | Risk | Impact | Probability | Mitigation |
|----|------|--------|-------------|------------|
| R-1 | LinkedIn Community Management API breaking changes | Bluesky works, LinkedIn breaks | Low | Already using the modern API; monitor LinkedIn Developer changelog for v2 deprecation notices |
| R-2 | Bluesky rate limits on mention resolution | Facets fail, post goes without resolved mentions | Medium | Cap at 10 resolves/post, 200ms delay, cache DIDs |
| R-3 | Crossref downtime | Link cards lack academic metadata | Low | OG tag fallback already in design; cache recent lookups |
| R-4 | LLM summarization produces hallucinated claims | Misleading TL;DR appended to academic posts | Medium | LLM prompt constrains to factual summarization; max char limit prevents runaway output; user can switch to extractive (zero hallucination risk) via config; `--dry-run` allows preview |
| R-4a | Gemini CLI unavailable or subscription lapses | No LLM summarization | Low | Automatic fallback to ollama -> extractive; never blocks the post |
| R-4b | PyAV/FFmpeg not installed on server | Video processing fails | Low | PyAV is a pip install with bundled FFmpeg libs; degrade gracefully to skip video with warning |
| R-5 | Mastodon instance rate-limits the bot account | No toots fetched | Low | Single fetch per 30min is well within limits; add retry with backoff |
| R-6 | State file corruption from power loss | State lost, duplicate posts | Very Low | Atomic writes (temp+rename) guarantee `state.json` is always valid; worst case = `.tmp` file left behind, original intact |
| R-7 | `.env` file accidentally committed | Credentials leaked | Low | `.gitignore` already configured; startup permission check |

---

## 10. Phased Rollout Plan

### Phase 1: Core + Bluesky (Milestone 1)

**Deliverables**:
- Project scaffolding (`scholarposter/` package)
- `config.py` (Pydantic config + YAML loading)
- `models.py` (UnifiedPost, PostResult)
- `collector.py` (MastodonCollector)
- `state.py` (JSON StateManager + atomic writes + file locking)
- `filters.py` (content filtering)
- `adapters/bluesky.py` (full Bluesky posting with threads, facets, embeds)
- `enrichment/url.py` (URL unshortening, content-type detection)
- `enrichment/html.py` (trafilatura body extraction + BeautifulSoup OG tags)
- `enrichment/pdf.py` (PyMuPDF/pymupdf4llm extraction)
- `enrichment/doi.py` (Crossref lookup + caching)
- `enrichment/summarizer.py` (tiered: gemini-cli -> ollama -> sumy)
- `enrichment/media.py` (Pillow images + PyAV video)
- `__main__.py` with `run` command and `--dry-run`
- Unit tests for all above
- `config.toml` template

**Success gate**: 50 consecutive cron runs on the dedicated server with zero crashes, zero duplicates, zero lost posts.

### Phase 2: LinkedIn + Summarization (Milestone 2)

**Deliverables**:
- `adapters/linkedin.py` (full LinkedIn posting via Community Management API, including video)
- Per-platform filter configuration
- Unit tests for all above

**Success gate**: Both platforms cross-posting correctly for 48 hours.

### Phase 3: Notifications + CLI + Polish (Milestone 3)

**Deliverables**:
- `notifications/ntfy.py` (push notifications)
- Additional notification backends (signal, email) as stubs or full implementations
- Full CLI: `status`, `retry`, `config validate` subcommands
- Log redaction
- `.env` permission check
- Documentation in README
- Integration tests

**Success gate**: Full feature set operational, `--dry-run` works for all platforms, notifications delivered on simulated failures.

---

## 11. Success Metrics & KPIs

### Primary Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Post success rate | > 99% over 30 days | `scholarposter status` output + log analysis |
| Zero duplicate posts | 0 duplicates | Manual audit of platform feeds vs `state.json` |
| Crash-free runs | 100% exit 0 or 1 (never unhandled exception) | Cron log / systemd journal |
| Mean cross-post latency | < 30 seconds | `loguru` timing between "fetch" and "posted" log entries |

### Secondary Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| DOI enrichment hit rate | > 90% of DOI-bearing links resolved | Log analysis |
| Notification delivery rate | 100% of failures notified | Compare `status='failed'` count with notification log |
| Config change frequency | User can adjust filters without code changes | Qualitative |

---

## 12. Open Questions & Future Enhancements

### Resolved Questions

| ID | Question | Decision | Rationale |
|----|----------|----------|-----------|
| OQ-1 | LinkedIn API: legacy UGC or Community Management API? | **Community Management API** | Most modern, feature-rich, and future-proof. UGC is in maintenance mode. |
| OQ-2 | Mastodon credentials: env vars or `.secret` files? | **Keep file-based** | `Mastodon.py` expects `.secret` files natively; fighting the library adds complexity for no security gain (files are already gitignored). |
| OQ-3 | Config format: YAML or TOML? | **TOML** | Stdlib in Python 3.11+ (`tomllib`), no extra dependency. Simpler syntax, native support for typed values. |
| OQ-4 | Video support in Phase 1 or Phase 2? | **Phase 1** | Included from the start to avoid media-handling rework. |
| OQ-5 | ntfy.sh setup? | **Zero-setup default** | ntfy.sh requires only installing the free Android app and subscribing to a topic — no server, no account, no API key. Document the 2-step setup in README. |

### Future Enhancements (Post-Launch)

| ID | Enhancement | Value |
|----|-------------|-------|
| FE-1 | Threads.net / Instagram adapter | Broader reach |
| FE-2 | LLM-based alt text generation for images missing descriptions (infrastructure already in place via summarizer's gemini/ollama backends) | Accessibility |
| FE-3 | Scheduling: hold posts and publish at optimal times per platform | Engagement |
| FE-4 | Multi-account support: cross-post from multiple Mastodon accounts | Scale |
| FE-5 | Web dashboard for monitoring post history and errors | Observability |
| FE-6 | Content transformation per platform (e.g., longer text for LinkedIn, shorter for Bluesky) | Platform optimization |

> **Note**: NG-8 ("Analytics or engagement tracking") is superseded by Amendment v2 (US-015). Audit log and Bluesky engagement sync are now in scope. LinkedIn engagement remains out of scope (API tier restriction).

---

## Appendix A: ~~Migration from Current System~~ (Retired)

> **Note (2026-04-08)**: The `lasttoot*.txt` → `state.json` migration path has been completed and removed. `scholarposter/migration.py` and `tests/test_migration.py` are deleted. New users and users resuming after a gap should use `scholarposter set-watermark` (US-016) to set their starting point.

### Starting Fresh or Resuming After a Gap

Use `scholarposter set-watermark`:

```bash
# Start crossposting with toots from a specific date
scholarposter set-watermark --date 2026-01-15

# Start crossposting after a specific toot (that toot is the last one skipped)
scholarposter set-watermark --toot-id 113456789012345678

# Start crossposting after a toot linked by URL
scholarposter set-watermark --toot-url "https://mastodon.social/@you/113456789012345678"

# Preview without writing
scholarposter set-watermark --date 2026-01-15 --dry-run
```

---

## Appendix B: Bugs Fixed by This Rewrite

This rewrite eliminates all 16 findings from the security audit:

| ID | Severity | Issue | How Fixed |
|----|----------|-------|-----------|
| C1 | CRITICAL | SSL disabled globally | `httpx` defaults to `verify=True`; no override |
| C2 | CRITICAL | `.env` not access-controlled | Startup permission check (FR-51) |
| C3 | CRITICAL | `credentials.json` unvalidated | Pydantic config validation (US-007) |
| H1 | HIGH | No `__main__` guard | Proper package structure with `__main__.py` |
| H2 | HIGH | `IndexError` on empty timeline | FR-7: explicit empty check |
| H3 | HIGH | `r`/`log_id` unbound | Adapter returns `Result` object; no global state |
| H4 | HIGH | `unshorten_url` no timeout | FR-14: 10s timeout on all redirects |
| H5 | HIGH | 12 bare `except:` blocks | Typed exception handling throughout |
| H6 | HIGH | `reqs=None` not guarded | trafilatura handles fetch+extract atomically; PyMuPDF returns clean errors |
| M1 | MEDIUM | Non-atomic state writes | FR-39: atomic temp+rename writes after confirmed success |
| M2 | MEDIUM | Naive URL joining | FR-21: `urllib.parse.urljoin` |
| M3 | MEDIUM | Off-by-one truncation | FR-24: grapheme-based chunking |
| M4 | MEDIUM | `url`/`eurl` scope confusion | No global variables; data flows through `UnifiedPost` |
| M5 | MEDIUM | `__main__` guard misplaced | Proper package entry point |
| M6 | MEDIUM | Log files opened without `with` | `loguru` handles all file I/O |
| L1-L6 | LOW | Dead code, no retry, etc. | Clean rewrite eliminates all |

---

# Amendment v2: Progressive Enrichment, Citation Graph Discovery, Audit Log & Engagement Feedback

> **Date**: 2026-04-07
> **Status**: APPROVED — design review gate passed (iteration 4, 2026-04-07)
> **Supersedes**: NG-8 (Analytics or engagement tracking)
> **Design decisions captured**: Q1–Q8 from lateral thinking analysis session

---

## New User Stories

### US-013: Progressive Enrichment Gating

**Description**: As a developer, I want the enrichment pipeline to skip expensive stages when earlier stages already produced sufficient content, so that well-indexed papers (those with Crossref abstracts) post faster and consume fewer API resources.

**Background**: The current pipeline always runs all stages regardless of what has been resolved. A paper with a full Crossref abstract does not need PDF download, PDF text extraction, or an LLM call on multi-thousand-word PDF text. The abstract already contains the key finding. The right strategy is: if a Crossref abstract is present, feed only the abstract to the LLM (short, fast, cheap); skip PDF download and `pymupdf4llm` extraction entirely. The LLM call itself is not skipped — a 200-word abstract still needs distillation to 150 chars; but the input size drops by 10–50× vs a full PDF. Gating predicate is purely metadata-presence-based (DOI abstract ≥ 20 chars). It does NOT depend on audit.jsonl data. Phase 4 can ship US-013 before audit.jsonl exists.

**Design decision**: Abstract present → skip PDF extraction; still run LLM on abstract text only. Gate evaluated by `EnrichmentPipeline.enrich()` (the orchestrator), not inside individual stage `run()` methods. Stages remain dumb transformers.

**Acceptance Criteria**:

- [ ] `EnrichmentPipeline.enrich()` evaluates `_should_skip_pdf(link: LinkEnrichment) -> bool` after the DOI/Crossref stage completes and before dispatching the PDF stage
- [ ] `_should_skip_pdf` returns `True` when `link.doi_metadata` is present and contains a non-empty abstract (minimum 20 characters)
- [ ] When PDF stage is skipped: `link.pdf_text` is left as `None`; `link.pdf_metadata` is left as `None`; an `INFO`-level log entry records `"Progressive gating: PDF stage skipped — abstract present ({N} chars)"`
- [ ] Summarizer receives `link.crossref_abstract` as input text when PDF is skipped — the same `summarize()` function, same prompt, same `max_chars`, just shorter input (`link.crossref_abstract` is a `str`, not a dict; it is populated by the Crossref stage as `crossref_abstract: Optional[str]` on `LinkEnrichment`)
- [ ] Stage 5 summarizer guard in `EnrichmentPipeline` changes from `if cfg.summarization.enabled and link.body_text:` to `text_input = link.body_text or link.crossref_abstract; if cfg.summarization.enabled and text_input:` — without this change the summarizer does not fire when PDF is skipped and `body_text` is `None`
- [ ] `card_description` priority chain (FR-20b) is unchanged — Crossref abstract still takes priority over LLM summary regardless of whether PDF was skipped; gating only changes what the LLM is given as input
- [ ] Gating is opt-out via config: `enrichment.progressive.enabled = true` (default `true`). Setting `false` restores the current full-pipeline behaviour.
- [ ] Gating is stage-specific — only the PDF stage is gated on abstract presence in v2; future gates (e.g. skip HTML extraction when OG description is sufficient) are architecturally possible but not implemented
- [ ] Unit tests:
  - Paper with Crossref abstract → PDF stage call count = 0; summarizer called with abstract text
  - Paper without abstract → PDF stage runs normally
  - `progressive.enabled = false` → PDF stage always runs regardless of abstract presence
  - Abstract present but < 20 chars (e.g. `"See full text"`) → PDF stage still runs (threshold not met)
  - Gating decision logged at INFO with character count (matching FR-78)

**Priority**: Medium

**Dependencies**: Enrichment pipeline (US-008), DOI lookup (FR-19)

---

### US-014: Citation Graph Discovery

**Description**: As an academic, I want to discover papers that are intellectually related to my past posts — papers I cited, papers that cite me, and papers co-cited alongside mine — so that I have a continuously-refreshed pool of high-quality content to share, without spending time manually searching related literature.

Typical workflow: run `scholarposter discover`, identify a paper, post about it on Mastodon (optionally preview with `scholarposter enrich <doi-url>`), and the normal cron pipeline picks it up.

**Background**: scholarposter's `bibliography.json` accumulates DOIs of every enriched post. This is a seed set for citation network traversal. OpenAlex, Crossref, and Semantic Scholar each expose different slices of the citation graph: OpenAlex has `related_works` and `cited_by_count` with velocity; Crossref has forward citations; Semantic Scholar has influence scores and citation velocity trends. Running a 2-hop traversal from the bibliography produces a ranked list of candidate papers the user has not posted yet.

**Design decisions**:
- Three traversal modes: `cited-by` (papers you cited), `cites` (papers citing yours), `co-cited` (papers co-appearing in reference lists)
- All three modes enabled by default; toggled via `--mode` CLI flag
- Data sources: OpenAlex + Crossref + Semantic Scholar (all three)
- Output: `scholarposter discover` prints ranked candidates to stdout
- Optional weekly email digest via existing SMTP backend (no new dependency)
- No auto-posting; user manually toots, normal pipeline picks it up
- MVP scope (Phase 5): OpenAlex only (already integrated). Crossref Cited-by (requires paid membership) and Semantic Scholar co-citation (expensive N+1 algorithm) are deferred to Phase 6 with separate WU declarations.

**Acceptance Criteria**:

- [ ] New CLI subcommand: `scholarposter discover [--config PATH] [--mode cited-by|cites|co-cited|all] [--limit N] [--since YYYY-MM-DD] [--email-digest]`
- [ ] `--mode` is a single-value Typer option (matching the `--platform` pattern). Valid values: `cited-by`, `cites`, `co-cited`, `all`. When `--mode all` is passed explicitly on the CLI, all three traversal modes run (including `co-cited`) regardless of the `[discovery] modes` config list. When `--mode` is omitted, the `[discovery] modes` config list governs which modes run — so the default config `modes = ["cited-by", "cites"]` means an unadorned `scholarposter discover` invocation only runs cited-by and cites (co-cited is opt-in). Default `--limit 20` candidates per mode.
- [ ] `--since` filters to papers published after the given date (ISO 8601); default: no date filter. `--days N` retained as deprecated alias for `--since` (computed as `today - timedelta(days=N)`) with a deprecation WARNING printed to stderr. Existing cron jobs using `--days 30` continue to work.
- [ ] **cited-by mode**: for each DOI in `bibliography.json`, use `GET https://api.openalex.org/works?filter=cites:{openalex_id}` (papers that cite the seed); deduplicate; filter against bibliography DOIs (already-posted) and return novel entries
- [ ] **cites mode**: for each DOI in `bibliography.json`, fetch `referenced_works` from the seed's own OpenAlex record (papers the seed cites); deduplicate; filter against bibliography
- [ ] **co-cited mode**: fetch papers that cite each bibliography seed via OpenAlex `filter=cites:{seed_id}` (cap at 50 per seed); for each citing paper, fetch its reference list from Semantic Scholar `/references`; Co-cited = papers appearing as references in ≥ 2 of those citing papers' reference lists. Mode is disabled by default (`"co-cited"` absent from `[discovery] modes`); user opts in by adding it to `modes` config or using `--mode co-cited` on the CLI. Filter against bibliography.
- [ ] **Deduplication**: a DOI already in `bibliography.json` is never returned as a candidate, regardless of traversal mode
- [ ] **Ranking**: candidates ranked by composite score: `citation_velocity` (Semantic Scholar) × `open_access_weight` (OA papers preferred, OA flag from OpenAlex) × `recency` (exponential decay, half-life 2 years). Weights configurable in `[discovery]` config section.
- [ ] **Output format** (stdout):
  ```
  DISCOVERY RESULTS — 2026-04-07 — 23 candidates (cited-by: 8, cites: 9, co-cited: 6)

  Rank  DOI                            Title                                  Year  Citations  Velocity  Source      Why surfaced
  ----  -----------------------------  -------------------------------------  ----  ---------  --------  ----------  ---------------------------
  1     10.1038/s41586-023-06598-x     Language models are few-shot learners  2020  52,341     +1200/mo  OpenAlex    co-cited with 10.1145/3442188
  2     ...
  Title column truncated to 40 chars with '…'. DOI truncated to 30 chars. Assumes 120-column terminal. `--wide` flag disables truncation.
  ```
- [ ] **Weekly email digest**: if `[discovery] digest_email` is configured (non-empty SMTP `to` address) and `--email-digest` flag is passed (or `digest_auto = true` in config), format the top-N results as a plain-text email and dispatch via the existing SMTP notification backend. Subject: `"scholarposter discovery digest — {date}: {N} candidates"`.
- [ ] **Rate limiting**: OpenAlex polite pool (existing `etiquette_email`), Crossref polite pool (existing), Semantic Scholar: 1 req/sec with retry on 429. All three sources use `httpx` with existing TLS defaults.
- [ ] **Graceful degradation**: if any one source fails or times out, log WARNING and continue with the remaining sources. Results are labelled by source so the user can see which graph slice produced each candidate.
- [ ] **No state mutation**: `scholarposter discover` is read-only. It never writes to `state.json`, `bibliography.json`, or `cache.json`. It is safe to run at any time without interfering with cron runs.
- [ ] **Caching**: OpenAlex/Crossref/Semantic Scholar responses cached in `discovery_cache.json` with configurable TTL (default 24h). Same atomic temp+rename pattern as `cache.json`.
- [ ] Unit tests:
  - `cited-by` mode returns referenced works from OpenAlex mock, filtered against bibliography
  - `cites` mode merges OpenAlex + Crossref forward citations, deduplicates correctly
  - `co-cited` mode calls Semantic Scholar; handles 429 with backoff
  - DOI already in bibliography is never returned
  - Single source failure → remaining sources still return results
  - `--limit 5` caps output at 5 per mode
  - Email digest: SMTP mock receives correctly-formatted message
  - Ranking: higher velocity paper ranked above lower velocity peer with same citations

**Priority**: Low (no blocking dependencies; can ship as a standalone subcommand)

**Dependencies**: Core architecture (US-007), DOI enrichment (US-004), Bibliography (implicit in `bibliography.json`), Notification email backend (US-006)

---

### US-015a: Audit Log

**Description**: As a user, I want every post attempt recorded in `audit.jsonl` so that I can debug failures and inspect posting history.

**Acceptance Criteria**: See "Audit Log (FR-90–FR-95)" and "`scholarposter audit` CLI subcommand" sections under [Acceptance Criteria](#acceptance-criteria-3) below.

### US-015b: Engagement Sync

**Description**: As an academic, I want to fetch Bluesky engagement metrics (likes, reposts) for my cross-posted content so that I can see which posts resonated with my audience.

**Acceptance Criteria**: See "`scholarposter sync-engagement` CLI subcommand" section under [Acceptance Criteria](#acceptance-criteria-3) below.

### US-015c: Engagement Report

**Description**: As an academic, I want a report correlating hashtags, LLM backends, and posting times with engagement so that I can identify what content approach works best.

**Background**: scholarposter currently has no post-hoc record of what was posted or how it was enriched. `state.json` records only the last-toot-ID per platform. This makes it impossible to answer: "Did my DOI-enriched posts get more engagement than bare-link posts?" or "Does Gemini summarization produce higher-engagement cards than extractive?" The audit log answers these questions without requiring a UI or database.

**Design decisions**:
- Format: JSON-lines (`audit.jsonl`) in the state directory, same location as `state.json`
- Query interface: `scholarposter audit` CLI subcommand
- Engagement pull: `scholarposter sync-engagement` fetches Bluesky likes/reposts for audit records; runs on-demand (not on every `run` invocation)
- LinkedIn engagement: out of scope (requires Marketing Partner API tier)
- Analysis: `scholarposter report` computes and prints correlation table; no automatic tuning

**Acceptance Criteria** {#acceptance-criteria-3}:

#### Audit Log (FR-90–FR-95)

- [ ] After every `scholarposter run` post attempt (success or failure), one record is appended to `audit.jsonl` in the state directory:
  ```json
  {
    "timestamp": "2026-04-07T14:23:11Z",
    "toot_id": "113456789012345678",
    "platform": "bluesky",
    "status": "posted",
    "enrichment_path": ["crossref", "summarizer:gemini"],
    "pdf_stage_skipped": true,
    "llm_backend_used": "gemini",
    "abstract_chars": 312,
    "summary_chars": 148,
    "doi": "10.1038/s41586-023-06598-x",
    "link_type": "webpage",
    "post_url": "https://bsky.app/profile/did:plc:abc/post/3k7jh",
    "bluesky_likes": null,
    "bluesky_reposts": null,
    "engagement_synced_at": null,
    "hashtags": ["science", "ml"],
    "chunk_count": 2
  }
  ```
- [ ] `enrichment_path` is an ordered list of stage names that produced non-null output (e.g. `["crossref", "summarizer:gemini"]`); skipped stages are absent from the list
- [ ] `pdf_stage_skipped` is `true` when US-013 gating fired
- [ ] `llm_backend_used` records which summarizer backend produced the final summary (`"gemini"`, `"lemonade"`, `"ollama"`, `"extractive"`, or `null` if no summary was generated)
- [ ] Dry-run (`--dry-run`) writes an audit record with `status: "dry_run"` — same structure, no post URL
- [ ] Audit append is non-blocking: write failures are logged at WARNING and never affect post outcome
- [ ] `audit.jsonl` is protected by the existing `scholarposter` advisory lock (`fcntl.flock`) for both append operations (FR-91) and the in-place atomic rewrite performed by `sync-engagement` (FR-93); `sync-engagement` must acquire the lock before reading and hold it through `os.replace()`
- [ ] `audit.jsonl` is excluded from git via `.gitignore` (same as `state.json`)

#### `scholarposter audit` CLI subcommand

- [ ] `scholarposter audit [--config PATH] [--platform bluesky|linkedin] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--status posted|failed|dry_run] [--limit N]`
- [ ] Default output: tabular display with columns `timestamp | toot_id | platform | status | doi | llm_backend | summary_chars | engagement`
- [ ] `--json` flag: emit raw JSON-lines matching the filter (for piping to `jq`)
- [ ] `--csv` flag: emit CSV for import into spreadsheet tools
- [ ] If no records match the filter, print `"No audit records matching filter."` and exit 0

#### `scholarposter sync-engagement` CLI subcommand

- [ ] `scholarposter sync-engagement [--config PATH] [--platform bluesky] [--since YYYY-MM-DD] [--dry-run] [--force]`
- [ ] Reads all audit records where `platform == "bluesky"`, `status == "posted"`, and `engagement_synced_at is null` (or `--force` to re-sync all)
- [ ] For each such record, calls AT Protocol `app.bsky.feed.getLikes` and `app.bsky.feed.getRepostedBy` using the `post_url` to derive the AT URI (`at://did/app.bsky.feed.post/rkey`)
- [ ] Updates `audit.jsonl` in-place: rewrites the matching line with `bluesky_likes`, `bluesky_reposts`, and `engagement_synced_at` populated. All other lines unchanged. Atomic write (temp+rename).
- [ ] `--dry-run`: prints what would be updated without writing
- [ ] Rate-limits AT Protocol calls: 50ms delay between requests; respects 429 responses with exponential backoff (max 3 retries)
- [ ] Records where `post_url` is null (failed posts, dry-run posts) are silently skipped
- [ ] On completion: prints summary `"Synced engagement for N posts (M skipped, K errors)."`

#### `scholarposter report` CLI subcommand

- [ ] `scholarposter report [--config PATH] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--output-dir DIR]`
- [ ] Reads all audit records with `bluesky_likes is not null` (i.e. engagement has been synced)
- [ ] Computes and prints three correlation tables:

  **Table 1 — Hashtag rule engagement** (which configured hashtag rules correlate with higher likes+reposts):
  ```
  Hashtag        Posts  Avg likes  Avg reposts  Avg total
  -----------    -----  ---------  -----------  ---------
  #science         42     3.2        1.1          4.3
  #openaccess      18     5.7        2.3          8.0
  (no hashtag)     11     1.1        0.3          1.4
  ```

  **Table 2 — LLM backend engagement** (does the summarizer backend affect engagement?):
  ```
  Backend        Posts  Avg total engagement
  -----------    -----  --------------------
  gemini           38     6.1
  extractive       14     3.2
  lemonade          7     5.8
  (no summary)      6     2.1
  ```

  **Table 3 — Posting time engagement** (UTC hour of post vs engagement):
  ```
  Hour (UTC)  Posts  Avg total engagement
  ----------  -----  --------------------
  08:00         12     7.3
  12:00          9     4.1
  ...
  ```

- [ ] Minimum sample size threshold: tables suppress rows with fewer than 3 posts (configurable via `[audit] min_report_sample = 3`)
- [ ] `--output-dir DIR` (default: current directory): write three CSV files (`report_hashtags.csv`, `report_backends.csv`, `report_timing.csv`) to DIR; fail with a user-visible `typer.BadParameter` if DIR does not exist (consistent with `bibliography --output` pattern)
- [ ] Add to each correlation table: footnote `n={sample_size}`. Add warning when total synced records < 30: "Correlations with fewer than 30 data points are indicative only — results may not be statistically reliable."
- [ ] The report is descriptive only — no automatic rule changes, no config modification. The user interprets the data and adjusts `config.toml` manually.
- [ ] If fewer than 10 total synced records exist, print `"Insufficient data for report (need ≥ 10 synced posts)."` and exit 0

**Priority**: Low

**Dependencies**: Core architecture (US-007), Bluesky adapter (US-001), AT Protocol client

---

## New Functional Requirements

### Progressive Enrichment Gating

| ID | Requirement |
|----|-------------|
| FR-74 | `EnrichmentPipeline.enrich()` calls `_should_skip_pdf(link: LinkEnrichment) -> bool` after the Crossref stage and before dispatching the PDF stage. |
| FR-75 | `_should_skip_pdf` returns `True` when `link.crossref_abstract` is a non-None string of length ≥ 20. All other cases return `False`. (`link.crossref_abstract` is the `Optional[str]` field on `LinkEnrichment`, populated by the Crossref stage — not a dict lookup.) |
| FR-76 | When PDF stage is skipped, `link.pdf_text` and `link.pdf_metadata` remain `None`. Stage 5 summarizer guard in `EnrichmentPipeline` changes from `if cfg.summarization.enabled and link.body_text:` to `text_input = link.body_text or link.crossref_abstract; if cfg.summarization.enabled and text_input:` (declared here as a required change to `enrichment/pipeline.py`). Summarizer is called with `link.crossref_abstract` as the `text` argument when PDF is skipped. FR-20b priority chain produces non-null card_description when pdf_text=None: falls back to `link.crossref_abstract` (populated by Crossref stage before gating) → og_description → empty string. Gating bypass creates no null card_description path. |
| FR-77 | Progressive gating is controlled by `enrichment.progressive.enabled` (bool, default `true`). When `false`, pipeline dispatches all stages as before (no behaviour change). |
| FR-78 | Gating decision is logged at `INFO`: `"Progressive gating: PDF stage skipped — abstract present ({N} chars)"` so users without DEBUG logging can confirm gating is active. |

### Citation Graph Discovery

| ID | Requirement |
|----|-------------|
| FR-79 | `scholarposter discover` CLI subcommand. Options: `--config PATH`, `--mode [cited-by|cites|co-cited|all]` (single-value flag; when omitted, `[discovery] modes` config list governs; when `all` is passed, all three modes run including co-cited), `--limit N` (default 20, per mode), `--since YYYY-MM-DD`, `--email-digest`, `--wide` (disables column truncation). |
| FR-80 | **cited-by traversal**: for each DOI in `bibliography.json`, use `GET https://api.openalex.org/works?filter=cites:{openalex_id}` (papers that cite the seed). Deduplicate across bibliography DOIs. Filter: remove any DOI already in `bibliography.json`. |
| FR-81 | **cites traversal**: for each DOI in `bibliography.json`, fetch `referenced_works` from the seed's own OpenAlex record. Crossref Cited-by service requires a separate Crossref membership agreement (paid). Defer to Phase 6. OpenAlex `filter=cites:{openalex_id}` is the sole v1 forward-citation source. Filter: remove bibliography DOIs. |
| FR-82 | **co-cited traversal**: fetch papers that cite each bibliography seed via OpenAlex `filter=cites:{seed_id}` (cap at 50 per seed). For each citing paper, fetch its reference list from Semantic Scholar `/references`. Co-cited = papers appearing as references in ≥ 2 of those citing papers' reference lists. Mode is disabled by default (`"co-cited"` is absent from the default `modes` list). User opts in by adding `"co-cited"` to `[discovery] modes` in config or by passing `--mode co-cited` on the CLI. `modes` is the sole gate — no separate `co_cited_enabled` flag. Filter: remove bibliography DOIs. |
| FR-83 | **Ranking formula (Phase 5)**: `score = citation_velocity_rank × oa_weight × recency_weight` where `citation_velocity_rank` = OpenAlex `cited_by_count / max(1, age_years)` (velocity proxy; Semantic Scholar `influentialCitationCount` is Phase 6 only — not available in Phase 5 OpenAlex-only scope); `oa_weight = 1.2` if `open_access.is_oa == true` (OpenAlex), else `1.0`; `recency_weight = exp(-0.693 × age_years / 2.0)` (half-life 2 years). Weights configurable in `[discovery]` config. |
| FR-84 | **Deduplication**: a DOI is returned at most once across all modes, attributed to the mode that produced the highest-ranked entry. |
| FR-85 | **Discovery cache**: API responses stored in `discovery_cache.json` (same atomic pattern as `cache.json`). TTL: configurable via `cache_ttl_hours` (default 24). Pruning: on-read (_prune_cache pattern, same as cache.json). Cache key: `{source}:{openalex_id_or_doi}`. Value schema: `{doi, openalex_id, title, year, cited_by_count, influential_citation_count, open_access: {is_oa}, fetched_at, expires_at, source}`. Lock: all reads/writes acquire fcntl advisory lock. Permissions: `discovery_cache.json` and its temp file are created with mode `0o600`. |
| FR-86 | **Graceful source degradation**: each source (OpenAlex, Crossref, Semantic Scholar) is tried independently. On timeout or HTTP error, log `WARNING "discovery: {source} failed for {doi}: {error}"` and continue. Results from remaining sources are still returned. |
| FR-87 | **Rate limits**: OpenAlex: polite pool via `User-Agent: scholarposter/1.0 (mailto:{etiquette_email})`; Crossref: same polite pool header; Semantic Scholar: 1 req/sec (100ms sleep between requests), retry on 429 with exponential backoff up to 3 attempts. Before running discovery, validate that `crossref.etiquette_email` is a non-empty string and not 'example.com'; strip `\r` and `\n` from the value before constructing the `User-Agent` header to prevent HTTP header injection (OWASP A03); if the stripped value contains no `@`, log WARNING and omit the polite-pool header. Also: all `openalex_id` values are passed as `httpx` query params (not f-string URL interpolation) so that reserved characters are percent-encoded automatically. |
| FR-88 | **Email digest**: if `[discovery] digest_email` is a non-empty string, `--email-digest` flag sends top-N results (default 20) via the existing SMTP notification backend. `digest_email` is validated at config load time via `email.utils.parseaddr()`; values containing `\r` or `\n` are rejected with a startup-time `ConfigError` to prevent SMTP header injection (OWASP A03). Subject: `"scholarposter discovery digest — {date}: {N} new candidates"`. Body: plain-text formatted table identical to stdout output. |
| FR-89 | **No state mutation**: `scholarposter discover` is read-only with respect to canonical state files: zero writes to `state.json`, `bibliography.json`, `cache.json`, or `audit.jsonl`. `discovery_cache.json` is the sole exception — `discover` reads and populates it (FR-85). |

### Audit Log and Engagement Feedback

| ID | Requirement |
|----|-------------|
| FR-90 | After every post attempt, append one JSON-lines record to `audit.jsonl` in the state directory. Fields: `timestamp` (ISO 8601 UTC), `toot_id`, `platform`, `status` (`posted|failed|dry_run`), `enrichment_path` (ordered list of stages that produced output), `pdf_stage_skipped` (bool), `llm_backend_used` (string or null), `abstract_chars` (int or null), `summary_chars` (int or null), `doi` (string or null), `link_type` (`file|webpage|null`), `post_url` (string or null), `bluesky_likes` (null at write time), `bluesky_reposts` (null at write time), `engagement_synced_at` (null at write time), `hashtags` (list of strings), `chunk_count` (int, 1 for single posts). |
| FR-91 | Audit append uses `os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)` with `json.dumps(record) + "\n"`. Lock semantics: (a) for in-process calls during `run` — `StateManager` already holds the advisory lock; `audit/log.py` is called via `StateManager.append_audit(record)` which runs under the already-held lock fd (no second acquisition); (b) for out-of-process commands (`audit`, `sync-engagement`) — acquire the lock with `fcntl.flock(fd, LOCK_EX | LOCK_NB)`; on `EAGAIN` print error to stderr and exit non-zero. Non-blocking for write failures: wrapped in `try/except`; failures logged at WARNING; post outcome unaffected. |
| FR-92 | `scholarposter audit` subcommand with `--config PATH` reads `audit.jsonl`, applies optional filters (`--platform`, `--since`, `--until`, `--status`, `--limit`), and prints tabular output. `--json` emits raw JSON-lines; `--csv` emits CSV. |
| FR-93 | `scholarposter sync-engagement` with `--config PATH` reads audit records where `platform == "bluesky"` and `engagement_synced_at is null` (or all records when `--force` is passed). For each, derives AT URI by resolving the handle from `post_url` to a DID via `com.atproto.identity.resolveHandle` at sync time (not from a cached/stored value, to handle handle renames). Calls `app.bsky.feed.getLikes(uri=at_uri)` and `app.bsky.feed.getRepostedBy(uri=at_uri)`. Both paginate via `cursor`. Follow cursor until absent in response to ensure accurate counts. Must acquire the existing `scholarposter` advisory lock (`fcntl.flock(fd, LOCK_EX | LOCK_NB)`; on `EAGAIN` print error to stderr and exit non-zero) before reading and hold it through `os.replace()`. Temp file created with `os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)` to ensure `0o600` permissions before atomic rename. Before `os.replace(tmp_path, audit_path)`, check `if len(written_records) != len(read_records): log ERROR; os.unlink(tmp_path); raise RuntimeError(f"audit rewrite aborted: record count mismatch ({len(written_records)} != {len(read_records)})")` — do NOT use Python `assert` (disabled under `python -O`). Rewrites `audit.jsonl` atomically. |
| FR-94 | `scholarposter report` with `--config PATH` reads all audit records with `bluesky_likes is not null`. Prints three correlation tables: (1) hashtag vs avg engagement, (2) LLM backend vs avg engagement, (3) UTC posting hour vs avg engagement. Rows with < `min_report_sample` posts (default 3) are suppressed. `--output-dir DIR` writes three CSV files (`report_hashtags.csv`, `report_backends.csv`, `report_timing.csv`) to DIR (default: cwd); fails with `typer.BadParameter` if DIR does not exist. |
| FR-95 | `audit.jsonl` added to `.gitignore` alongside `state.json`, `cache.json`. |

---

## Architecture Changes

New modules added to `scholarposter/`:

```
scholarposter/
  config.py                # AMENDED: add ProgressiveEnrichmentConfig (fields: enabled: bool = True);
                           #   DiscoveryRankingConfig (oa_weight: float = 1.2,
                           #   recency_half_life_years: float = 2.0);
                           #   DiscoveryConfig (enabled, sources, modes, limit, digest_email,
                           #   digest_auto, cache_ttl_hours, ranking: DiscoveryRankingConfig);
                           #   AuditConfig (enabled, file, min_report_sample, rotation_max_mb,
                           #   retention_days); EnrichmentConfig amended to include
                           #   progressive: ProgressiveEnrichmentConfig;
                           #   ScholarposterConfig amended to include discovery: DiscoveryConfig
                           #   and audit: AuditConfig. All new sub-configs use extra="ignore".
  cli.py                   # AMENDED: (Phase 5) `discover` subcommand rewritten to use
                           #   discovery.graph API (--mode, --limit, --since, --email-digest,
                           #   --wide); existing extract_interests() / discover_papers() calls
                           #   removed (no backward-compat shim — fully replaced by graph API);
                           #   `audit`, `sync-engagement`, and `report` subcommands added.
  enrichment/
    pipeline.py            # AMENDED: add _should_skip_pdf() gating logic; amend Stage 5 guard
                           #   to accept crossref_abstract as fallback input when body_text=None
  discovery/
    __init__.py            # MIGRATION: existing discovery.py converted to discovery/__init__.py;
                           #   extract_interests() and discover_papers() removed — no shim;
                           #   cli.py rewrite (above) is the migration path
    graph.py               # Citation graph traversal (OpenAlex, Crossref-Phase6, Semantic Scholar-Phase6)
    ranking.py             # Composite score formula + ranking (Phase 5: OpenAlex cited_by_count)
    digest.py              # Weekly email digest formatter
    cache.py               # discovery_cache.json atomic read/write (0o600)
  audit/
    __init__.py
    log.py                 # audit.jsonl append; non-blocking; called via StateManager.append_audit()
    engagement.py          # Bluesky AT Protocol engagement sync
    report.py              # Correlation tables (hashtag, backend, timing) — Phase 6
```

---

## Config Additions

New sections to append to `config.toml.example`:

```toml
[enrichment.progressive]
enabled = true  # skip PDF extraction when Crossref abstract is present

[discovery]
enabled = true
sources = ["openalex"]  # Phase 5: OpenAlex only. Add "crossref" (paid membership required), "semantic_scholar" in Phase 6.
modes = ["cited-by", "cites"]  # "co-cited" is disabled by default (expensive N+1 Semantic Scholar); add to list to enable
limit = 20                                   # candidates per mode per run
digest_email = ""                            # SMTP to-address; empty = no digest
digest_auto = false                          # send digest automatically on --cron runs
cache_ttl_hours = 24

[discovery.ranking]
oa_weight = 1.2            # bonus multiplier for open-access papers
recency_half_life_years = 2.0

[audit]
enabled = true
file = "audit.jsonl"
min_report_sample = 3      # minimum posts per row in scholarposter report
rotation_max_mb = 50
retention_days = 365
```

---

## New Test Scenarios

| ID | Scenario | Type | WU |
|----|----------|------|----|
| T-16 | Paper with Crossref abstract: PDF stage call count = 0; summarizer receives abstract text; assert `download_media` mock call count = 0 (HTTP call to PDF URL was not made) | Unit | US-013 |
| T-17 | Paper without abstract: PDF stage runs normally; progressive gating has no effect; assert `download_media` mock call count ≥ 1 (HTTP call to PDF URL was made) | Unit | US-013 |
| T-18 | `progressive.enabled = false`: PDF stage always runs regardless of abstract | Unit | US-013 |
| T-19 | Abstract < 20 chars: gating threshold not met; PDF stage still runs | Unit | US-013 |
| T-20 | `discover --mode cited-by`: mock returns papers from OpenAlex `filter=cites:{openalex_id}` (papers citing the seed); assert result contains only papers NOT already in bibliography DOIs | Unit | US-014 |
| T-21 | `discover --mode cites`: returns papers from OpenAlex `referenced_works` of the seed record; deduplicates against bibliography; assert no Crossref API call is made in Phase 5 | Unit | US-014 |
| T-22 | `discover --mode co-cited`: Semantic Scholar 429 → exponential backoff → retry succeeds | Unit | US-014 (Phase 6) |
| T-23 | DOI in bibliography never returned as discovery candidate | Unit | US-014 |
| T-24 | Single source failure: remaining two sources still return results | Unit | US-014 |
| T-25 | `--email-digest`: SMTP mock receives correctly-formatted message with top-20 candidates | Unit | US-014 |
| T-25b | Ranking formula: paper with higher OpenAlex `cited_by_count / age_years` ranks above paper with same year and lower count. OA paper (oa_weight=1.2) ranks above non-OA peer with identical citation velocity. | Unit | US-014 |
| T-26 | Audit record written after successful Bluesky post | Unit | US-015 |
| T-27 | Audit record written after failed LinkedIn post with `status: "failed"` | Unit | US-015 |
| T-28 | Audit append failure: post outcome unchanged; WARNING logged | Unit | US-015 |
| T-29 | `sync-engagement`: AT URI correctly derived from `post_url`; likes/reposts written to audit | Unit | US-015 |
| T-30 | `sync-engagement --dry-run`: no writes; prints what would be updated | Unit | US-015 |
| T-31 | `report`: hashtag table suppresses row with < min_report_sample posts | Unit | US-015 |
| T-32 | `report`: "Insufficient data" when < 10 synced records | Unit | US-015 |

---

## New Risks

| ID | Risk | Impact | Probability | Mitigation |
|----|------|--------|-------------|------------|
| R-8 | Semantic Scholar API rate limits block co-cited traversal on large bibliographies | Discovery slow or incomplete | Medium | Per-DOI 100ms sleep; exponential backoff on 429; cache responses 24h; partial results still useful |
| R-9 | `audit.jsonl` grows unboundedly on long-running installations | Disk space; slow `report` command | Low | Document recommended periodic rotation (e.g. `logrotate`); `scholarposter audit --since` supports time-windowed queries |
| R-10 | Bluesky AT Protocol pagination limits engagement counts for high-engagement posts | Under-counts likes/reposts > 100 | Low | AT Protocol `getLikes` paginates; `sync-engagement` follows `cursor` until exhausted |
| R-11 | Progressive gating skips PDF for a paper where the Crossref abstract is a stub (`"See full text"`) | Poor summary quality | Low | 20-char minimum threshold on abstract; users can disable gating per-run via config |

---

## Phased Rollout — Phase 4 (Amendment v2)

**Phase 4: Progressive Enrichment + Audit Log** (after Phase 3 is stable)

**Deliverables**:
- `enrichment/pipeline.py` amended: `_should_skip_pdf()` gating
- `audit/log.py`: `audit.jsonl` append
- `audit/engagement.py`: Bluesky engagement sync
- `scholarposter audit`, `scholarposter sync-engagement` subcommands
- Config additions: `[enrichment.progressive]`, `[audit]`
- Unit tests for all above (T-16 to T-30)

**Phase 5: Citation Graph Discovery & Basic Engagement** (after Phase 4)

**Deliverables**:
- `discovery/graph.py`, `discovery/ranking.py`, `discovery/digest.py`, `discovery/cache.py`
- `scholarposter discover` subcommand
- Config additions: `[discovery]`, `[discovery.ranking]`
- Unit tests (T-20 to T-25)

**Phase 6: Advanced Analytics & Deep Graph Traversal** (Deferred)

**Deliverables**:
- `audit/report.py`: correlation tables and `scholarposter report` subcommand
- Semantic Scholar co-citation traversal
- Crossref Cited-by traversal
- Phase 6 WUs for Crossref Cited-by must declare: membership status, endpoint version `https://doi.crossref.org/servlet/getForwardLinks`, credential tier (paid membership).
- Phase 6 WUs for Semantic Scholar must declare: API tier (free: 100 req/5min; partner: application required), credential type, rate limit handling.

**Success gate (Phase 4)**: Mean enrichment latency for DOI-bearing posts with abstracts reduces ≥ 30% vs baseline (50 consecutive runs); 100% of runs produce valid audit records (valid JSON, all required fields).

**Success gate (Phase 5)**: `scholarposter discover --mode all` returns ≥ 5 OpenAlex candidates from a bibliography of ≥ 3 DOIs; `discover --mode cited-by` correctly queries `filter=cites:{openalex_id}` (verified by integration test against live OpenAlex API with known seed DOI); email digest delivered to configured SMTP address.

---

# Amendment v3: Config Migration on Package Update

> **Date**: 2026-04-08
> **Status**: APPROVED — design review gate passed (iteration 3, 2026-04-08)

## Requirement

When a new version of scholarposter introduces config keys required by new features, the package **must not overwrite the user's existing `config.toml`**. Instead, a `scholarposter config-update` command appends any missing keys to the existing file as **commented-out blocks at EOF**, preserving all user-set values. Migration is always explicit — never automatic on run.

## New User Story

### US-017: Non-destructive Config Migration

**As an existing scholarposter user upgrading the package**, I want `scholarposter config-update` to append new config options (commented out) to my existing `config.toml`, so that I can see what's new and opt in without manually diffing example files or losing my customisations.

> **Scope note**: This command is relevant only after a package upgrade. New users receive the full `config.example.toml` on initial setup and do not need to run `config-update`.

**Priority**: High — upgrade experience

**Acceptance Criteria**:
- [ ] `scholarposter config-update` is a new top-level command (hyphenated, consistent with `sync-engagement`, `set-watermark`). It does **not** extend the existing `scholarposter config validate` command.
- [ ] Reads `config.toml` (user file) and `config.example.toml` (shipped package data, accessed via `importlib.resources`). If the example file cannot be located, exits 1 with `"Shipped example config not found — reinstall the package"`.
- [ ] **Append-only**: all additions are appended to **EOF**, never inserted mid-file. Missing top-level sections and missing keys within existing sections are all grouped at the end under a clearly labelled block. Existing lines are never modified.
- [ ] Each appended block is preceded by `# --- config-update: <section> ---` (version-agnostic sentinel) and an adjacent `# Added by scholarposter config-update {version}` line. Version read via `importlib.metadata.version("scholarposter")`; falls back to `"dev"` on `PackageNotFoundError`. Appended key lines are commented out with `# `. Exactly one blank line separates existing file content from the first appended block.
- [ ] Idempotency: detection is **text-based** (raw line scan), not TOML-parse-based. A key present in the file as a commented-out line (`# key = ...`) is treated as **absent** (commented ≠ active). A key present as an active (uncommented) line is treated as present. Second run against an already-updated file produces no new additions.
- [ ] Existing keys and values are **never modified or removed**.
- [ ] `--dry-run`: prints the lines that would be appended, writes nothing, exits 0.
- [ ] `--diff`: prints a unified diff (`--- config.toml / +++ config.toml (updated)`), writes nothing, exits 0. `--diff` implies `--dry-run`; passing both flags is valid and equivalent to `--diff` alone.
- [ ] All output from `--dry-run` and `--diff` is passed through `_SENSITIVE_FIELDS` redaction (same mechanism as `cli.py` `_redact()`) before printing. Sensitive placeholder values in `config.example.toml` are masked as `<redacted>`.
- [ ] File write uses atomic replacement: write updated content to a `.tmp` file (text mode, `encoding="utf-8"`) then `os.rename()` to the target path — matching the `_atomic_write()` pattern in `state.py` but text-mode rather than binary.
- [ ] If `config.toml` has no missing keys, prints `"config.toml is up to date"` and exits 0.
- [ ] FR-105 invariant: `config.example.toml` must be updated in the **same commit** as any new Pydantic config field. Enforced via the Config Schema WU gate in `CLAUDE.md`. A round-trip validation test (`ScholarposterConfig.model_validate(tomllib.loads(example_content))`) must pass on CI to catch drift.

## New Functional Requirements

| # | Requirement |
|---|-------------|
| FR-102 | `scholarposter config-update` top-level CLI command. Options: `--config PATH` (default `config.toml`), `--dry-run`, `--diff`. |
| FR-103 | **Append-only diff algorithm**: (1) Parse both files with `tomllib` to produce candidate list: all top-level sections and leaf keys present in the example but absent from the user file. (2) **Filter candidates using sentinel scan** (FR-107) — remove any section already marked as processed. (3) For each remaining candidate section, append the full commented-out block to EOF. Never insert mid-file. Handles arbitrarily nested sub-tables (e.g. `[enrichment.providers]`) by appending full subtable blocks. Ensure exactly one blank line between existing file content and the first appended block. |
| FR-104 | Comment format: each appended block is preceded by `# --- config-update: {section} ---` (section name only — **no version in sentinel**; version is informational only and appears in an adjacent comment line: `# Added by scholarposter config-update {version}`). Each appended key line is prefixed `# `. Version from `importlib.metadata.version("scholarposter")`, fallback `"dev"`. |
| FR-105 | `config.example.toml` is the single source of truth for default config structure. Located at `scholarposter/data/config.example.toml`. Shipped as package data declared in `pyproject.toml` under `[tool.setuptools.package-data]` (`{"scholarposter" = ["data/*.toml"]}`). Accessed at runtime via `importlib.resources.files("scholarposter.data").joinpath("config.example.toml")`. Updated in the **same commit** as any new config field; CI round-trip test enforces this. |
| FR-106 | `--dry-run`: prints appended lines (redacted) to stdout, exits 0, writes nothing. `--diff`: prints unified diff (redacted) to stdout, exits 0, writes nothing. Both flags together: equivalent to `--diff`. |
| FR-107 | Idempotency via raw-text sentinel scan: before appending any block, scan the raw text of `config.toml` for `# --- config-update: {section} ---` (section name only, no version). If found, skip that section. This sentinel is version-agnostic — it persists across upgrades, preventing duplicate blocks for unchanged sections on every version bump. The TOML-parse diff (FR-103 step 1) produces the candidate list; the sentinel scan (this FR) is the gate that filters already-processed sections from that list. |
| FR-108 | If `config.example.toml` cannot be located via `importlib.resources`, exit 1 with `"Shipped example config not found — reinstall the package"`. Do not raise an unhandled exception. |
| FR-109 | `config.example.toml` must not contain live credential values. Placeholder values for any field matching `_SENSITIVE_FIELDS` must be empty strings or descriptive placeholders (e.g. `""`). |

## Design Considerations

- **Append-only, never edit**: avoids the complexity and fragility of in-place TOML manipulation. All additions land at EOF, clearly labelled. Users can `grep "config-update"` to find all additions. Limitation: new keys for an existing section appear at EOF rather than alongside related keys — acceptable for infrequent upgrade migrations.
- **Text-based idempotency sentinel**: `tomllib` won't see commented-out keys, so TOML-parse-based idempotency would re-append on every run. Sentinel comment lines (`# --- config-update ...`) in the raw file are the source of truth for "already done."
- **`importlib.resources` for package data**: works in zipped installs, editable installs, and regular installs. `Path(__file__).parent` breaks in some packaging scenarios.
- **`config.example.toml` as ground truth**: decouples migration logic from Pydantic model internals. CI round-trip test catches drift automatically.
- **Upgrade-only, always explicit**: no auto-migration on `run`. Silent mutation of user config files is surprising and hard to audit.
- **`--diff` implies `--dry-run`**: reduces CLI surface; two synonymous flags for the same "don't write" behaviour is confusing.
- **Version-agnostic sentinel**: the idempotency sentinel `# --- config-update: {section} ---` deliberately omits the version number. Including the version would cause duplicate appended blocks on every package upgrade for sections with no new keys. The `# Added by ... {version}` line is informational only.
- **Commented-out section header edge case**: if a user manually comments out an entire section (e.g. `# [discovery]`) and `config-update` later appends an active `[discovery]` block, the file will contain two `[discovery]` headers. Uncommenting both causes a TOML parse error. Users should remove manually-commented sections before uncommenting `config-update` additions. This is documented but not prevented by the tool — the append-only design cannot safely detect or merge this case.

## Prerequisites (same WU)

- Create `scholarposter/data/__init__.py` (empty, makes `scholarposter.data` a package for `importlib.resources`)
- Create `scholarposter/data/config.example.toml` with all current config sections and keys
- Add `[tool.setuptools.package-data]` stanza to `pyproject.toml`: `{"scholarposter" = ["data/*.toml"]}`
- Add CI round-trip test: `ScholarposterConfig.model_validate(tomllib.loads(example_content))` must pass

## Test Scenarios

| ID | Scenario | Type |
|----|----------|------|
| T-31 | User config missing `[audit]`: appended EOF block is commented out; original keys and values untouched; sentinel line present | Unit |
| T-32 | User config missing a leaf key inside an existing section: full section block appended at EOF under sentinel; existing section content unchanged | Unit |
| T-33 | Fully up-to-date config: exits 0, prints `"config.toml is up to date"`, file byte-identical after run | Unit |
| T-34 | `--dry-run`: additions printed to stdout (redacted); file not written | Unit |
| T-35 | `--diff`: unified diff printed (redacted); file not written; `--dry-run --diff` equivalent to `--diff` alone | Unit |
| T-36 | **Idempotency**: run twice on a config missing `[audit]`; first run appends block; second run detects sentinel and makes no further changes | Unit |
| T-37 | Config with `[discovery]` block commented out (`# [discovery]`): treated as absent; active block appended on next run | Unit |
| T-38 | User value differs from example default: user value preserved; example value never overwrites | Unit |
| T-39 | `config.example.toml` missing at runtime: exits 1 with prescribed error message; no file written | Unit |
| T-40 | Config with inline comments on existing keys: append does not corrupt those lines | Unit |
| T-41 | Round-trip: `ScholarposterConfig.model_validate(tomllib.loads(example_content))` passes (runs on every CI commit) | Unit |
| T-42 | **Cross-version idempotency**: config updated with v1.2.0 sentinel present; re-run under v1.3.0 with no new keys — file byte-identical, no duplicate block appended | Unit |

