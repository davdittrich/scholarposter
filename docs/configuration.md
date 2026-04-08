# Configuration Reference

All settings live in `config.toml` ([TOML](https://toml.io/) format). Copy
`config.toml.example` and edit to match your setup.

---

## `[mastodon]`

| Key | Required | Description |
|-----|----------|-------------|
| `instance` | yes | Your Mastodon server URL, e.g. `https://fediscience.org` |
| `credentials_file` | yes | Path to `pytooter_usercred.secret` |

## `[platforms.<name>]`

One section per platform (`bluesky`, `linkedin`).

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Set to `false` to disable |

### `[platforms.<name>.filters]`

| Key | Default | Description |
|-----|---------|-------------|
| `skip_hashtags` | `[]` | Skip toots containing any of these (case-insensitive) |
| `skip_content_types` | `[]` | Skip toots matching these content types: `"sensitive"`, `"poll"`, `"media_only"`, `"reblog"`, `"reply"`, `"self_thread_reply"`, `"direct"`, `"private"`, `"unlisted"`, `"content_warning"`, `"mention"` |
| `require_hashtags` | `[]` | Only post toots with at least one of these hashtags; empty list posts everything |

See [filtering.md](filtering.md) for evaluation order, content type definitions, and hashtag rule behavior.

### `[platforms.<name>.media]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Upload images when present; set `false` to suppress media |
| `max_image_size_kb` | `950` | Images are JPEG-resized to fit (example config suggests 5000 for LinkedIn) |

### `[[platforms.<name>.hashtag_rules]]`

Prepend a hashtag when the original toot contains specific trigger hashtags.

```toml
[[platforms.bluesky.hashtag_rules]]
add_hashtag = "EconSky"
if_any_hashtag = ["Economics", "GameTheory", "Labor"]
```

Multiple rules fire independently.

---

## `[enrichment.crossref]`

DOI metadata lookup via the Crossref API.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | |
| `etiquette_email` | `""` | Your email for Crossref polite-pool access (recommended) |
| `cache_ttl_days` | `7` | Cache DOI lookups for this many days |
| `timeout_seconds` | `5` | |

## `[enrichment.summarization]`

Paper/article summary generation. See [summarization.md](summarization.md) for
backend setup.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | |
| `backend` | `"extractive"` | `"gemini"`, `"lemonade"`, `"ollama"`, or `"extractive"` |
| `max_chars` | `150` | Truncate summary to this length |
| `prompt` | (academic summary prompt) | System prompt for LLM backends; default targets a one-sentence summary (~150 chars) for link card display |

`max_chars` controls the link card description length (visible on Bluesky and LinkedIn). The summary appears only in the link card description, not appended to the post text. A three-tiered priority determines the card content: DOI-enriched links use the Crossref abstract; file links (PDFs) use the AI summary; web pages use the OG description with AI summary as fallback. See [summarization.md](summarization.md) for details.

Backends are tried in fallback order starting from the preferred one.
Fallback is only to cheaper/simpler backends (no wrap-around).

### `[enrichment.summarization.gemini]`

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `""` | Gemini model name (empty = CLI default); e.g. `"gemini-3-flash-preview"` |
| `timeout_seconds` | `30` | ACP session timeout for Gemini CLI |

### `[enrichment.summarization.lemonade]`

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `""` | Model ID (empty = auto-load best downloaded model) |
| `host` | `"http://127.0.0.1:8000"` | Lemonade server URL |
| `timeout_seconds` | `60` | Inference HTTP timeout |
| `ctx_size` | `8192` | Context window for auto-loaded models (tokens) |
| `load_timeout_seconds` | `180` | Max seconds for model loading |
| `preferred_models` | (see example) | Ordered preference list for auto-load |

### `[enrichment.summarization.ollama]`

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `"gemma3:9b"` | Ollama model name |
| `host` | `"http://localhost:11434"` | Ollama API endpoint |
| `timeout_seconds` | `30` | HTTP timeout |

### `[enrichment.summarization.extractive]`

| Key | Default | Description |
|-----|---------|-------------|
| `max_sentences` | `5` | Maximum sentences in extractive summary |

## `[enrichment.url_unshorten]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Follow redirects to resolve shortened URLs |
| `timeout_seconds` | `10` | |
| `max_redirects` | `5` | |

## `[enrichment.progressive]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Skip the PDF download stage for PDF links when Crossref already provides an abstract. Reduces bandwidth for papers whose DOI metadata is sufficient for summarization. |

---

## `[[notifications.backends]]`

Push notifications on posting failure. Configure any combination.

### ntfy (recommended)

```toml
[[notifications.backends]]
type = "ntfy"
topic = "scholarposter"
server = "https://ntfy.sh"
```

### Signal (via signal-cli REST API)

```toml
[[notifications.backends]]
type = "signal"
api_url = "http://localhost:8080"
phone_number = "+1234567890"
recipients = ["+0987654321"]
```

### Email (SMTP)

```toml
[[notifications.backends]]
type = "email"
smtp_host = "smtp.example.com"
smtp_port = 587
from_addr = "alerts@example.com"
to_addr = "you@example.com"
```

SMTP credentials via environment variables: `SMTP_USER` and `SMTP_PASSWORD`.
Port 465 uses implicit TLS (SMTP_SSL). Other ports use capability-based STARTTLS.

---

## `[logging]`

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `file` | `"scholarposter.log"` | Log file path |
| `rotation` | `"10 MB"` | Rotate when file exceeds this size |
| `retention` | `"30 days"` | Delete logs older than this |

## `[state]`

| Key | Default | Description |
|-----|---------|-------------|
| `state_file` | `"state.json"` | Per-platform toot tracking |
| `cache_file` | `"cache.json"` | DOI/URL enrichment cache |
| `lock_file` | `"scholarposter.lock"` | Prevents concurrent runs |
| `bibliography_file` | `"bibliography.json"` | Bibliography of DOI-enriched posts |

State files are resolved relative to the directory containing `config.toml`, not the
working directory. This ensures all commands (`run`, `retry`, `status`, `bibliography`,
`enrich`, `discover`) read and write the same files regardless of where you invoke
scholarposter from.

---

## `[audit]`

Records every cross-post attempt as a JSON line in a `.jsonl` file. Required by the
`audit` and `sync-engagement` commands. Disabled by default.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Write an audit record for each cross-post attempt |
| `file` | `"audit.jsonl"` | Log file path; resolved relative to the config directory |
| `min_report_sample` | `3` | Minimum number of records before the `audit` command displays aggregate statistics |
| `rotation_max_mb` | `50` | Maximum log size in MB before rotation (not yet enforced) |
| `retention_days` | `365` | Retain records for this many days; older records are deleted (not yet enforced) |

---

## `[discovery]`

Citation graph traversal via OpenAlex. Disabled by default; requires `enabled = true`
before the `discover` command runs traversal.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable citation graph discovery |
| `sources` | `["openalex"]` | Data sources for graph traversal |
| `modes` | `["cited-by", "cites"]` | Traversal modes to run by default; valid values: `"cited-by"`, `"cites"`, `"all"` |
| `limit` | `20` | Maximum number of candidates to return |
| `digest_email` | (none) | Email address for discovery digests; required when `--email-digest` is passed to `discover` |
| `digest_auto` | `false` | Send a digest email automatically on every `discover` run when results are non-empty. Requires `digest_email` to be set; logs a warning and skips if absent. |
| `cache_ttl_hours` | `24` | Time-to-live for cached discovery results, in hours |

### `[discovery.ranking]`

Controls the composite score used to rank candidates. Candidates with higher scores appear first.

| Key | Default | Description |
|-----|---------|-------------|
| `oa_weight` | `1.2` | Multiplier applied to open-access papers in the composite score |
| `recency_half_life_years` | `2.0` | Exponential decay half-life for the recency component, in years |

---

## Environment variables

scholarposter loads credentials from a `.env` file via python-dotenv. Store `.env` alongside `config.toml` with `chmod 600` permissions.

| Variable | Required | Description |
|----------|----------|-------------|
| `MASTODON_INSTANCE` | managed by auth | Mastodon instance URL (auto-managed by `auth mastodon`) |
| `BLUESKY_EMAIL` | if Bluesky enabled | Bluesky account email |
| `BLUESKY_PASSWORD` | if Bluesky enabled | Bluesky app password (see [auth-bluesky.md](auth-bluesky.md)) |
| `LINKEDIN_CLIENT_ID` | if LinkedIn enabled | LinkedIn app Client ID (from Developer Portal) |
| `LINKEDIN_CLIENT_SECRET` | if LinkedIn enabled | LinkedIn app Client Secret |
| `LINKEDIN_ACCESS_TOKEN` | managed by auth | LinkedIn OAuth access token (auto-managed) |
| `LINKEDIN_OWNER_URN` | managed by auth | LinkedIn member URN, e.g. `urn:li:person:abc123` (auto-managed) |
| `LINKEDIN_TOKEN_EXPIRES_AT` | managed by auth | Access token expiry (ISO 8601, auto-managed) |
| `SMTP_USER` | no | SMTP username for email notifications |
| `SMTP_PASSWORD` | no | SMTP password for email notifications |
