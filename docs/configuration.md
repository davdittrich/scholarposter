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
| `skip_content_types` | `[]` | Skip: `"sensitive"`, `"poll"`, `"media_only"`, `"reblog"` |
| `require_hashtags` | `[]` | Only post toots with at least one of these; empty = post all |

### `[platforms.<name>.media]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Upload images when present; set `false` to suppress media |
| `max_image_size_kb` | `950` (Bluesky) / `5000` (LinkedIn) | Images are JPEG-resized to fit |

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
| `max_chars` | `500` | Truncate summary to this length |
| `prompt` | (academic summary prompt) | System prompt sent to Gemini/Ollama |

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
| `timeout_seconds` | `10` | *Deprecated — kept for config backward compatibility; not used at runtime* |

## `[enrichment.url_unshorten]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Follow redirects to resolve shortened URLs |
| `timeout_seconds` | `10` | |
| `max_redirects` | `5` | |

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
