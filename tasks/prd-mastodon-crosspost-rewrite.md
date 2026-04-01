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

- [ ] Authenticates via OAuth2 using credentials from environment or token file
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

**Description**: As an academic, I want high-quality summarization of linked articles and papers, using the best available method (LLM when configured, extractive as fallback), so that cross-posted toots include an informative TL;DR.

**Acceptance Criteria**:

- [ ] Summarization enabled/disabled via config (`enrichment.summarization.enabled`)
- [ ] Tiered summarization backends, configurable via `enrichment.summarization.backend`:
  - `gemini` (default if configured): invokes `gemini-cli` with an academic summarization prompt; requires a Gemini subscription; produces abstractive, context-aware summaries
  - `ollama`: invokes a local LLM via Ollama API (configurable model, e.g. `gemma2:9b`); free, offline-capable
  - `extractive`: sumy KL/LSA as lightweight fallback; no external dependencies
- [ ] Automatic fallback chain: gemini -> ollama -> extractive. If the preferred backend fails or times out, the next tier is tried silently.
- [ ] LLM prompt is academic-tuned: "Summarize this academic paper/article in 2-3 sentences for a social media post. Focus on the key finding and methodology. Be concise and precise."
- [ ] Configurable max summary length (default 500 characters)
- [ ] Supports HTML pages (extracted via `trafilatura`) and PDFs (extracted via `pymupdf4llm` as Markdown)
- [ ] Summary appended to post as "\n\ntldr:\n{summary}" only when toot body + summary fits within platform character limit
- [ ] Summarization timeout configurable per backend: gemini (30s), ollama (30s), extractive (10s)
- [ ] Unit tests cover: each backend individually, fallback chain, too-long summary truncation, timeout fallback

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
- [ ] `config validate` subcommand: validates config file and prints resolved values (secrets masked)
- [ ] `--verbose` / `--quiet` global flags control log level
- [ ] Exit codes: 0 = success or nothing to do, 1 = posting error, 2 = configuration error
- [ ] Unit tests for CLI argument parsing

**Priority**: Low

**Dependencies**: Core architecture (US-007)

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
| FR-16 | HTML content extraction: OG meta tags (title, description, image) via `BeautifulSoup` + `lxml`; full article body text via `trafilatura` for summarization input |
| FR-17 | PDF extraction: title + metadata via `PyMuPDF` XMP/DocInfo API; full text via `pymupdf4llm` as structured Markdown (preserves headings, ideal for LLM input); page limit configurable (default 20) |
| FR-18 | DOI detection: regex `10.\d{4,9}/[-.;()/:\w]+` in URLs and toot text |
| FR-19 | Crossref lookup: query `Works().doi(udoi)` with polite etiquette header; 5s timeout; cache results in SQLite with 7-day TTL |
| FR-20 | Summarization: tiered backend system with automatic fallback chain: (1) `gemini` — invoke `gemini-cli` with academic prompt, 30s timeout; (2) `ollama` — local LLM via HTTP API, configurable model, 30s timeout; (3) `extractive` — sumy KL/LSA, 10s timeout. Each backend produces max 500 chars (configurable). Backend selection via config with fallback on failure. |
| FR-20a | LLM summarization prompt: "Summarize this academic paper/article in 2-3 sentences for a social media post. Focus on the key finding and methodology. Be concise and precise." Prompt configurable via config file. |
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
| FR-27 | Upload images via `repo.upload_blob`; support JPEG, PNG, GIF, WebP (convert WebP to JPEG if needed) |
| FR-27a | Upload videos via Bluesky video upload service; support MP4; respect 50MB / 60s limits |
| FR-28 | If any chunk in a thread fails, abort the entire thread and mark the toot as `failed` in state |
| FR-29 | Rate-limit mention resolution: max 10 handle resolves per post, 200ms delay between requests |

### LinkedIn Posting (Community Management API)

| ID | Requirement |
|----|-------------|
| FR-30 | Authenticate via OAuth2 with token refresh; tokens stored in env or encrypted file |
| FR-31 | Post via Community Management API (`/rest/posts`) with `PUBLISHED` lifecycle state (replaces legacy UGC endpoint) |
| FR-32 | Image posts: register upload via `/rest/images`, upload binary, attach to post as `content.media` |
| FR-33 | Video posts: initialize upload via `/rest/videos`, upload binary in chunks, poll for processing completion, attach to post |
| FR-34 | Article posts: attach URL with title and description from enrichment as `content.article` |
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

[enrichment.crossref]
enabled = true
etiquette_email = "your-email@example.com"
cache_ttl_days = 7
timeout_seconds = 5

[enrichment.summarization]
enabled = true
backend = "gemini"  # "gemini", "ollama", "extractive" — falls back automatically on failure
max_chars = 500
prompt = "Summarize this academic paper/article in 2-3 sentences for a social media post. Focus on the key finding and methodology. Be concise and precise."

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

---

## Appendix A: Migration from Current System

### Data Migration

1. Read current `lasttoot_bluesky.txt` and `lasttoot.txt` values
2. Create `state.json` with initial state:
   ```json
   {
     "bluesky": {"last_toot_id": <bluesky_id>, "last_status": "posted", "last_posted_at": null, "last_error": null},
     "linkedin": {"last_toot_id": <linkedin_id>, "last_status": "posted", "last_posted_at": null, "last_error": null}
   }
   ```
3. Old `.txt` files can be archived after migration is verified

### Cron Migration

Replace three separate cron entries:
```cron
# OLD
*/30 * * * * cd /path && python3 m2blusky.py
*/30 * * * * cd /path && python3 m2li.py
```

With a single entry:
```cron
# NEW
*/30 * * * * cd /path && python3 -m scholarposter run 2>&1 | tee -a /var/log/scholarposter.log
```

### Rollback Plan

Keep old scripts and `.txt` state files intact during Phase 1. If the new system fails:
1. Disable new cron entry
2. Re-enable old cron entries
3. Old scripts resume from their own `lasttoot*.txt` state (independent of SQLite)

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
