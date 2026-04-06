# CLI Reference

## Global Flags

The `run`, `status`, and `retry` commands accept these flags:

| Flag | Effect |
|------|--------|
| `--config PATH` | Path to config file (default: `config.toml`) |
| `--verbose` | Enable DEBUG logging to stderr |
| `--quiet` | Suppress INFO, show WARNING and above |

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

### `retry`

Retry posting a specific toot to a single platform.

```bash
scholarposter retry --platform bluesky --toot-id 123456789 [--dry-run]
```

| Flag | Required | Description |
|------|----------|-------------|
| `--platform` | yes | `bluesky` or `linkedin` |
| `--toot-id` | yes | Mastodon toot ID to retry |
| `--dry-run` | no | Simulate without posting |

Fetches the toot by ID (bypasses timeline pagination), re-enriches, and posts.

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

Register app and log in to a Mastodon instance.

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | `config.toml` | Path to config file |

Prompts for instance URL, email, and password (hidden). If `MASTODON_INSTANCE`, `MASTODON_EMAIL`, and `MASTODON_PASSWORD` are in `.env`, runs non-interactively.

Updates `config.toml` and writes credential files. See [auth-mastodon.md](auth-mastodon.md) for details.

### `config validate`

Print parsed configuration with sensitive fields (credentials, tokens) redacted.

```bash
scholarposter config validate
```

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
| `--output` | stdout | Write to file instead of stdout |

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

Discover recent papers matching your sharing interests via OpenAlex.

```bash
scholarposter discover [--days N] [--limit N] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days` | 30 | Look back N days for recent papers |
| `--limit` | 10 | Maximum number of suggestions |
| `--json` | off | Machine-readable JSON output |

Analyzes your `bibliography.json` to find authors you share frequently, then
queries OpenAlex for their recent publications. Excludes papers you've already
shared. Requires at least one bibliography entry with DOI metadata.

**Operational dependency:** Requires internet access to `api.openalex.org`. Returns
zero results when the API is unavailable.
