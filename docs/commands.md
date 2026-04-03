# CLI Reference

## Global Flags

All commands accept these flags:

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

### `enrich`

Enrich a URL: resolve redirects, extract metadata, look up DOI, summarize.

```bash
scholarposter enrich URL [--json] [--no-summarize]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | off | Machine-readable JSON output (excludes body_text/thumbnail_bytes) |
| `--summarize/--no-summarize` | on | Include/skip summarization |

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
