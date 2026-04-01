# scholarposter

Cross-post your Mastodon toots to Bluesky and LinkedIn — automatically, one at a time, on a schedule.

**What it does:**

- Fetches the oldest unprocessed toot from your Mastodon account
- Enriches it: resolves short URLs, fetches DOI/paper metadata via Crossref, generates a summary (Gemini CLI, Ollama, or extractive fallback)
- Applies content filters: skip hashtags, skip sensitive content, require specific hashtags
- Posts to Bluesky and/or LinkedIn with media attachments, link cards, and rich-text facets
- Threads long posts automatically (Bluesky 300-grapheme limit)
- Prepends platform-specific hashtags based on trigger rules (e.g., add `#EconSky` when the toot contains `#Economics`)
- Sends failure notifications via ntfy.sh, signal-cli, or email (SMTP)
- Maintains state so each toot is posted exactly once, per platform

---

## Prerequisites

- Python 3.11 or later
- `git` (to clone the repo)
- On some Debian/Ubuntu systems, video support requires `libavcodec`: `sudo apt install ffmpeg`

---

## Installation

```bash
git clone <repo-url> scholarposter-src
cd scholarposter-src
./install.sh ~/scholarposter
```

The script copies the source, creates a virtualenv, installs all dependencies, and scaffolds `config.toml` and `.env` from their examples. Re-running the script upgrades in place without overwriting your config.

Verify the installation:

```bash
~/scholarposter/.venv/bin/scholarposter --help
```

---

## Post-install setup

Complete these steps before running for the first time:

- [ ] **Mastodon credentials** — see [docs/auth-mastodon.md](docs/auth-mastodon.md); place `pytooter_usercred.secret` in your install directory
- [ ] **Edit `config.toml`** — set `[mastodon] instance` to your server URL and `credentials_file` to the path of your `.secret` file
- [ ] **Fill in `.env`** — Bluesky app password; LinkedIn token (see [docs/auth-linkedin.md](docs/auth-linkedin.md))
- [ ] **Configure summarization** (optional) — see [docs/summarization.md](docs/summarization.md)

---

## Configuration reference

All settings live in `config.toml`. The file is [TOML](https://toml.io/) format.

### `[mastodon]`

| Key | Required | Description |
|-----|----------|-------------|
| `instance` | yes | Your Mastodon server URL, e.g. `https://fediscience.org` |
| `credentials_file` | yes | Path to `pytooter_usercred.secret` (absolute or relative to CWD) |

### `[platforms.bluesky]` / `[platforms.linkedin]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Set to `false` to disable this platform entirely |

### `[platforms.*.filters]`

Controls which toots get posted.

| Key | Default | Description |
|-----|---------|-------------|
| `skip_hashtags` | `[]` | Skip toots containing any of these hashtags (case-insensitive) |
| `skip_content_types` | `[]` | Skip `"sensitive"` (CW/NSFW), `"poll"`, and/or `"media_only"` (media with no text) toots |
| `require_hashtags` | `[]` | Only post toots that contain at least one of these hashtags; empty = post all |

### `[[platforms.bluesky.hashtag_rules]]`

Prepend a hashtag to the Bluesky post when the original toot contains specific hashtags. Multiple rules are independent — each fires on its own trigger list.

```toml
[[platforms.bluesky.hashtag_rules]]
add_hashtag = "EconSky"
if_any_hashtag = ["Economics", "GameTheory", "Labor", "Market", "Wage"]

[[platforms.bluesky.hashtag_rules]]
add_hashtag = "AcademicSky"
if_any_hashtag = ["Research", "Science", "Academia"]
```

### `[platforms.*.media]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Attach images/video when present |
| `max_image_size_kb` | `950` (Bluesky) / `5000` (LinkedIn) | Images are resized to fit |
| `max_video_size_mb` | `50` (Bluesky) / `200` (LinkedIn) | Videos exceeding this limit are skipped |
| `supported_types` | see example | MIME types to include; others are skipped |

### `[enrichment.crossref]`

Fetches paper title, journal, and abstract when the toot contains a DOI.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | |
| `etiquette_email` | `""` | Polite-pool email for Crossref API (recommended) |
| `cache_ttl_days` | `7` | How long to cache DOI lookups |
| `timeout_seconds` | `5` | |

### `[enrichment.summarization]`

Generates a 2-3 sentence summary of the linked paper/article. See [docs/summarization.md](docs/summarization.md) for setup.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | |
| `backend` | `"extractive"` | Preferred backend: `"gemini"`, `"ollama"`, or `"extractive"` |
| `max_chars` | `500` | Truncate summary to this length |
| `prompt` | (academic summary prompt) | System prompt sent to Gemini/Ollama |

Backends are tried in order `gemini → ollama → extractive`; the first to succeed wins.

### `[enrichment.url_unshorten]`

Follows redirects to resolve shortened URLs before enrichment.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | |
| `timeout_seconds` | `10` | |
| `max_redirects` | `5` | |

### `[[notifications.backends]]`

Push notifications on error. Supported backends: `ntfy`, `signal`, `email`.

```toml
# ntfy (recommended — zero-setup push notifications)
[[notifications.backends]]
type = "ntfy"
topic = "scholarposter"
server = "https://ntfy.sh"

# signal-cli REST API
[[notifications.backends]]
type = "signal"
api_url = "http://localhost:8080"
phone_number = "+1234567890"
recipients = ["+0987654321"]

# Email via SMTP (credentials via SMTP_USER/SMTP_PASSWORD env vars)
[[notifications.backends]]
type = "email"
smtp_host = "smtp.example.com"
smtp_port = 587
from_addr = "alerts@example.com"
to_addr = "you@example.com"
```

Subscribe to ntfy at `https://ntfy.sh/your-topic` or install the ntfy Android/iOS app.

### `[logging]`

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `file` | `"scholarposter.log"` | Log file path (relative to CWD) |
| `rotation` | `"10 MB"` | Rotate when file exceeds this size |
| `retention` | `"30 days"` | Delete logs older than this |

### `[state]`

| Key | Default | Description |
|-----|---------|-------------|
| `state_file` | `"state.json"` | Tracks last-processed toot ID per platform |
| `cache_file` | `"cache.json"` | Crossref/URL enrichment cache |
| `lock_file` | `"scholarposter.lock"` | Prevents concurrent runs |

---

## Running

Run all enabled platforms:

```bash
scholarposter run
```

Run a specific platform:

```bash
scholarposter run --platform bluesky
scholarposter run --platform linkedin
```

Simulate a run without making any API calls:

```bash
scholarposter run --dry-run
```

Increase log verbosity:

```bash
scholarposter run --verbose      # DEBUG level to stderr
scholarposter run --quiet        # suppress INFO, show WARNING and above
```

Show last processed toot ID and pending count per platform:

```bash
scholarposter status
```

Use a config file in a non-standard location:

```bash
scholarposter run --config /path/to/config.toml
```

---

## Cron setup

Use the full path to the virtualenv's binary to avoid PATH issues.

```
crontab -e
```

Add:

```
*/30 * * * * /home/user/scholarposter/.venv/bin/scholarposter run --config /home/user/scholarposter/config.toml >> /home/user/scholarposter/scholarposter.log 2>&1
```

If you use **Gemini summarization**, cron runs with a minimal PATH that may not find the `gemini` binary. Add a PATH line at the top of your crontab:

```
PATH=/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin
```

Adjust to match wherever your `gemini` binary lives (`which gemini`).

---

## Notifications

scholarposter sends a notification when a post fails (max 1 per platform per run). Three backends are available — configure any combination in `config.toml`:

```toml
[[notifications.backends]]
type = "ntfy"
topic = "my-scholarposter-alerts"   # choose something unique
server = "https://ntfy.sh"
```

Subscribe at `https://ntfy.sh/my-scholarposter-alerts` in a browser, or install the ntfy app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347)) and subscribe to the same topic.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Another instance is running (lock file held)` | Stale lock from a crashed run | `rm /path/to/scholarposter.lock` |
| No posts, no errors in log | All toots already processed | `scholarposter status` — check the last toot ID; if it's correct, there's nothing new to post |
| `HTTP 401` on LinkedIn | Access token expired (60-day TTL) | Re-run the OAuth flow; see [docs/auth-linkedin.md](docs/auth-linkedin.md) |
| Summarization always falls back to extractive | Gemini/Ollama unreachable | Check PATH in cron; verify Ollama is running; see [docs/summarization.md](docs/summarization.md) |
| Images downsampled aggressively | Source image is large | Expected — Bluesky enforces a ~976 KB blob limit; images are resized automatically |
| `ModuleNotFoundError` on startup | venv not activated / wrong binary | Use the full path: `/path/to/.venv/bin/scholarposter` |

---

## Legacy migration

If you were previously using the older shell-script version of scholarposter, you can seed the new `state.json` from the old last-toot files:

```bash
cd /path/to/scholarposter
python3 -c "
from scholarposter.migration import migrate_state_files
from pathlib import Path
migrate_state_files(Path('.'), Path('.'))
"
```

This reads `lasttoot_bluesky.txt` and `lasttoot.txt` (if present) and writes their values into `state.json`.

---

## Development

```bash
git clone <repo-url> scholarposter
cd scholarposter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Run with coverage:

```bash
pytest --cov=scholarposter --cov-report=term-missing
```
