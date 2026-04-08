# CLI Reference

## Global Flags

All commands accept `--config PATH` (default: `config.toml`). The `run`, `status`, and `retry` commands additionally accept `--verbose` (DEBUG logging to stderr) and `--quiet` (suppress INFO, show WARNING and above).

---

## Core Commands

### `run`

Cross-post the oldest unprocessed Mastodon toot to configured platforms.

```bash
scholarposter run [--platform bluesky|linkedin|all] [--dry-run]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--platform` | `all` | Post to a single platform or all enabled |
| `--dry-run` | off | Simulate without posting or advancing state |

Behavior:
- Fetches the oldest toot newer than the last-processed ID per platform
- Evaluates filters before enrichment (fail fast)
- Enriches: resolve URLs, extract DOI/metadata, summarize
- Posts with media, link cards, and threaded text
- On success: saves metadata to `bibliography.json` for DOI-enriched posts
- On failure: sends notification, records error in state
- Retries transient errors (HTTP 429/5xx) up to 2 times with backoff
- If a multi-chunk Bluesky thread fails mid-way, scholarposter deletes already-posted chunks before returning the failure. The post is marked failed in state and retried on the next run.

### `retry`

Retry posting a specific toot to a single platform.

```bash
scholarposter retry --platform bluesky --toot-id 123456789 [--dry-run]
```

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | no | Path to config file (default: `config.toml`) |
| `--platform` | yes | `bluesky` or `linkedin` |
| `--toot-id` | yes | Mastodon toot ID to retry |
| `--dry-run` | no | Simulate without posting |

Fetches the toot by ID (bypasses timeline pagination), re-enriches, and posts.

### `sync-engagement`

Fetch current like and repost counts from Bluesky and write them into `audit.jsonl`.

```bash
scholarposter sync-engagement [--dry-run] [--force]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.toml` | Path to config file |
| `--dry-run` | off | Print planned updates without writing |
| `--force` | off | Re-sync records that already have engagement data |

**Prerequisite:** `[audit] enabled = true` must be set in `config.toml`. The command exits with an error if audit logging is disabled.

Only Bluesky records are synced. LinkedIn records are skipped. Bluesky records with handle-based post URLs are resolved to DIDs automatically. Records where resolution fails are counted as skipped. Requires `BLUESKY_EMAIL` and `BLUESKY_PASSWORD` in `.env`. Prints `Synced engagement for N posts (M skipped, K errors).`

### `set-watermark`

Configure the crossposting watermark — the toot after which the next `run` begins.

```bash
scholarposter set-watermark (--toot-id INT | --toot-url URL | --date YYYY-MM-DD) [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--platform` | `all` | Platform to update: `bluesky`, `linkedin`, or `all` |
| `--toot-id INT` | — | Set watermark to this toot ID directly |
| `--toot-url URL` | — | Extract toot ID from a Mastodon URL |
| `--date YYYY-MM-DD` | — | Find the last toot before midnight UTC on this date (requires Mastodon credentials configured in `config.toml`) |
| `--dry-run` | off | Print what would be set without writing state (for `--date`, the API lookup still executes) |
| `--yes` / `-y` | off | Skip confirmation prompt |

Exactly one of `--toot-id`, `--toot-url`, or `--date` is required. Zero or more than one → exits 2 with a usage message.

**Examples:**

```bash
# Start from a specific toot ID
scholarposter set-watermark --toot-id 113456789012345678

# Parse the ID from a toot URL
scholarposter set-watermark --toot-url "https://mastodon.social/@you/113456789012345678"

# Start from the last toot before January 15, 2026
scholarposter set-watermark --date 2026-01-15

# Preview without writing
scholarposter set-watermark --date 2026-01-15 --dry-run

# Skip confirmation prompt
scholarposter set-watermark --toot-id 113456789012345678 --yes
```

When `--date` finds no toot before the given date (e.g. the account had no toots that early, or
the date is in the future), the `last_toot_id` key is deleted from `state.json` — the next `run`
fetches from the beginning of the timeline. The `--date` mode pages up to 20,000 toots (500 pages
of 40) before giving up.

### `status`

Show last-posted toot ID, status, pending count, and last error per platform.

```bash
scholarposter status
```

Queries the Mastodon API for pending toot count (up to 50). Pending shows `?` if
the API is unreachable.

### `scholarposter auth linkedin`

Authorize scholarposter to post to LinkedIn via OAuth 2.0.

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | `config.toml` | Path to config file (`.env` is resolved from the same directory) |
| `--port` | `8080` | Local port for the OAuth callback server |

Requires `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` in `.env`. See [auth-linkedin.md](auth-linkedin.md) for setup.

### `scholarposter auth mastodon`

Register an OAuth app on a Mastodon instance and authorize scholarposter via the OAuth code flow.

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | `config.toml` | Path to config file (`.env` is resolved from the same directory) |
| `--port` | `8080` | Local port for the OAuth callback server (desktop mode) |

Prompts for the instance URL the first time, or reads `MASTODON_INSTANCE` from `.env` on subsequent runs. Opens your browser to the Mastodon authorization page (or prints the URL on headless servers). Writes `pytooter_clientcred.secret` and `pytooter_usercred.secret` (`chmod 600`) and updates the `[mastodon]` section of `config.toml`. See [auth-mastodon.md](auth-mastodon.md) for details.

### `config validate`

Print parsed configuration with sensitive fields (credentials, tokens) redacted.

```bash
scholarposter config validate
```

### `config-update`

Append missing config keys (commented out) to an existing `config.toml` without overwriting user values. Safe to run after every package upgrade.

```bash
scholarposter config-update            # append new keys (commented out) to config.toml
scholarposter config-update --dry-run  # preview additions without writing
scholarposter config-update --diff     # show unified diff of proposed changes
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.toml` | Path to config file |
| `--dry-run` | off | Print additions without writing |
| `--diff` | off | Show unified diff without writing |

Reads `config.toml` and the shipped `config.example.toml`, then appends any missing keys as commented-out lines at EOF. Sensitive fields (`access_token`, `smtp_password`, etc.) are shown as `"<redacted>"`. Running twice is safe — each key is appended at most once.

---

## Academic Tools

### `bibliography`

Export bibliography of shared papers. Entries accumulate in `bibliography.json`
automatically after each successful DOI-enriched post.

```bash
scholarposter bibliography [--format bibtex|json|markdown] [--output FILE]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | `bibtex` | Output format |
| `--output` / `-o` | stdout | Write to file instead of stdout |

BibTeX output escapes all LaTeX special characters (`& % $ # _ { } ~ ^ \`).
Publication year is extracted from Crossref metadata when available.

#### Bibliography fields

Each entry in `bibliography.json` contains:

| Field | Description |
|-------|-------------|
| `doi` | Digital Object Identifier |
| `title` | Paper title (from Crossref or OG metadata) |
| `authors` | Author list from Crossref |
| `abstract` | Paper abstract (may be truncated) |
| `url` | Resolved URL of the shared link |
| `shared_at` | ISO 8601 timestamp of first share |
| `publication_year` | Year from Crossref metadata |
| `platforms` | Platforms shared to (e.g. `["bluesky", "linkedin"]`) |
| `source_toot_id` | Mastodon toot ID that contained the DOI |

Entries are deduplicated by DOI. When the same paper is shared to multiple platforms, the `platforms` list is merged.

### `enrich`

Enrich a URL: resolve redirects, extract metadata, look up DOI, summarize.

```bash
scholarposter enrich URL [--json] [--no-summarize]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | off | Machine-readable JSON output (excludes body_text/thumbnail_bytes) |
| `--summarize/--no-summarize` | on | Include/skip summarization |

The `--json` flag outputs all enrichment fields including:
- `link_type` — `"file"` or `"webpage"` (based on Content-Type or URL extension)
- `crossref_title`, `crossref_abstract` — Crossref-specific metadata (separate from OG)
- `card_description`, `card_title` — resolved card text after three-tiered priority and sanitization
- `enrichment_rank` — link quality score (4=DOI, 3=file, 2=has title or description, 1=bare)

Human-readable output shows Title, DOI, Abstract (truncated to 200 chars), Resolved URL, and Summary.

Works without a config file (falls back to defaults). Useful for quick paper
triage from the terminal.

### `discover`

Traverse the OpenAlex citation graph using your bibliography as seed DOIs.

```bash
scholarposter discover [--mode MODE] [--since DATE] [--limit N] [--json] [--wide] [--email-digest]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.toml` | Path to config file |
| `--mode` | (config modes) | Traversal mode: `cited-by`, `cites`, or `all`. `co-cited` is accepted but not yet implemented. |
| `--since` | (none) | Restrict results to papers published from this date (YYYY-MM-DD) |
| `--limit` | `10` | Maximum number of suggestions |
| `--json` | off | Machine-readable JSON output |
| `--wide` | off | Print full-length titles without truncation |
| `--email-digest` | off | Send a digest to `discovery.digest_email` after displaying results |
| `--days N` | (none) | **Deprecated.** Use `--since` instead. |

Requires `[discovery] enabled = true` in `config.toml`. Excludes papers already in `bibliography.json`. Uses the OpenAlex polite pool; set `etiquette_email` in `[enrichment.crossref]` for priority access.

**Operational dependency:** Requires internet access to `api.openalex.org`. Returns zero results when the API is unavailable.

### `audit`

Query the cross-post audit log.

```bash
scholarposter audit [--platform PLATFORM] [--since DATE] [--until DATE] [--status STATUS] [--limit N] [--json] [--csv]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.toml` | Path to config file |
| `--platform` | (all) | Filter by platform name (`bluesky`, `linkedin`) |
| `--since` | (none) | Show records from this date onwards (YYYY-MM-DD) |
| `--until` | (none) | Show records up to this date, inclusive (YYYY-MM-DD) |
| `--status` | (all) | Filter by status: `posted`, `failed`, or `dry_run` |
| `--limit` | (none) | Maximum number of records to display |
| `--json` | off | Output raw JSON-lines |
| `--csv` | off | Output CSV |

**Prerequisite:** `[audit] enabled = true` must be set in `config.toml`. The command exits with an error if audit logging is disabled.

The default tabular output shows: timestamp, toot ID, platform, status, DOI, LLM backend used, summary character count, and Bluesky engagement (likes and reposts, if synced).
