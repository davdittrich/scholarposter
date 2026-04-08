# scholarposter

Cross-post Mastodon toots to Bluesky and LinkedIn with automatic enrichment,
summarization, and paper discovery.

## Features

- **Cross-post** with DOI/abstract enrichment, link cards, and media
- **Thread** long posts on Bluesky (grapheme-safe, AT Protocol compliant)
- **Filter** by hashtag, content type, or reblog status per platform
- **Summarize** shared papers into link card descriptions via Gemini, Lemonade, Ollama, or extractive fallback
- **Export** a BibTeX bibliography of everything you've shared
- **Enrich** any URL from the terminal — DOI, title, abstract, summary
- **Discover** new papers matching your interests via OpenAlex
- **Notify** on failure via ntfy, Signal, or email

## How it works

```mermaid
flowchart TD
    A[Mastodon timeline] --> B[Fetch oldest unprocessed toot]
    B --> C{Filters pass?}
    C -->|no| D[Skip, advance state]
    C -->|yes| E[Enrichment pipeline]

    E --> E1[Unshorten URLs]
    E1 --> E1a[Classify link type]
    E1a -->|webpage| E3[OG tags + trafilatura body text]
    E1a -->|file| E4[PyMuPDF metadata + text]
    E3 & E4 --> E5[DOI detection from URL]
    E5 --> E6[Crossref lookup + cache]
    E6 --> E7{Summarize?}
    E7 -->|yes| E8[Fallback chain]
    E7 -->|no| F

    E8 --> S1[Gemini ACP]
    S1 -->|fail| S2[Lemonade]
    S2 -->|fail| S3[Ollama]
    S3 -->|fail| S4[Extractive]
    S1 & S2 & S3 & S4 --> F

    F[Enriched post] --> F1[Resolve card title + description]
    F1 --> G{Platform}
    G -->|Bluesky| H[Thread + facets + link card per chunk]
    G -->|LinkedIn| I[Article card with best link]
    H & I --> J[Update state + bibliography]
```

---

## Installation

Requires Python 3.11+ and git.

```bash
git clone https://github.com/davdittrich/scholarposter.git scholarposter-src
cd scholarposter-src
./install.sh ~/scholarposter
```

The script creates a virtualenv, installs dependencies, and scaffolds `config.toml`
and `.env` from their examples. Re-running upgrades in place without overwriting
config.

Verify:

```bash
~/scholarposter/.venv/bin/scholarposter --help
```

The install script symlinks `scholarposter` to `~/.local/bin/` and adds it to your
shell PATH if needed. Open a new terminal after install for the PATH change to take
effect.

### Post-install setup

- [ ] **Mastodon credentials** — run `scholarposter auth mastodon` (see [docs/auth-mastodon.md](docs/auth-mastodon.md))
- [ ] **Edit `config.toml`** — set instance URL and credentials file path
- [ ] **Bluesky credentials** — see [docs/auth-bluesky.md](docs/auth-bluesky.md)
- [ ] **LinkedIn credentials** — run `scholarposter auth linkedin` (see [docs/auth-linkedin.md](docs/auth-linkedin.md))
- [ ] **Summarization** (optional) — see [docs/summarization.md](docs/summarization.md)

---

## Workflows

### Cross-posting

```bash
scholarposter run                      # all platforms
scholarposter run --platform bluesky   # single platform
scholarposter run --dry-run            # simulate without posting
```

Each invocation processes one toot (the oldest unprocessed). Schedule via cron to
process the backlog continuously.

### Retrying a failed post

```bash
scholarposter retry --platform bluesky --toot-id 123456789
```

Fetches the specific toot by ID, re-enriches it, and posts to the given platform.

### Checking status

```bash
scholarposter status
```

Shows last-posted toot ID, status, pending count, and last error per platform.

### Exporting your bibliography

Every successful post with a DOI automatically saves the paper's metadata (title,
authors, abstract, publication year) to `bibliography.json`.

```bash
scholarposter bibliography                          # BibTeX to stdout
scholarposter bibliography --format markdown        # Markdown reading list
scholarposter bibliography --format json            # JSON
scholarposter bibliography --output refs.bib        # write to file
```

### Enriching a URL

Look up metadata for any URL without posting:

```bash
scholarposter enrich https://arxiv.org/abs/2401.12345
scholarposter enrich https://doi.org/10.1234/foo --json
scholarposter enrich https://example.com/paper --no-summarize
```

Prints title, DOI, abstract, resolved URL, and summary.

### Discovering new papers

Traverse the OpenAlex citation graph using your bibliography as seed DOIs:

```bash
scholarposter discover                          # all modes from config
scholarposter discover --mode cited-by          # papers that cite your work
scholarposter discover --mode cites             # papers your work references
scholarposter discover --mode all               # cited-by + cites combined
scholarposter discover --since 2025-01-01       # papers from 2025 onwards
scholarposter discover --limit 20               # top 20 suggestions
scholarposter discover --json                   # JSON output
scholarposter discover --wide                   # full-length titles
scholarposter discover --email-digest           # send digest to discovery.digest_email
```

Requires `[discovery] enabled = true` in config.  Uses OpenAlex polite pool
(set `etiquette_email` in `[enrichment.crossref]`).  Excludes papers already
in your bibliography.

Results are ranked by a composite score: citation velocity × OA weight × recency
decay (configurable via `[discovery.ranking]`).  Duplicate DOIs from different
modes are deduplicated, keeping the highest-scoring copy.

To receive an email digest, set `digest_email` in `[discovery]` and pass
`--email-digest`.  The digest is sent via SMTP (defaults to `localhost:25`; uses
the first `[[notifications.backends]]` entry of `type = "email"` if configured).
Subject: `scholarposter discovery digest — YYYY-MM-DD: N new candidates`.

### Querying the audit log

When `[audit] enabled = true`, every cross-post is recorded to `audit.jsonl`:

```bash
scholarposter audit                           # tabular summary
scholarposter audit --platform bluesky        # filter by platform
scholarposter audit --status failed           # filter by status
scholarposter audit --since 2026-01-01        # date range
scholarposter audit --json                    # raw JSON-lines output
scholarposter audit --csv                     # CSV for spreadsheets
scholarposter audit --limit 20                # most recent 20 records
```

### Syncing Bluesky engagement

After posting, fetch current like and repost counts from Bluesky and write
them back into `audit.jsonl` (requires `[audit] enabled = true`):

```bash
scholarposter sync-engagement                 # sync all unsynced posts
scholarposter sync-engagement --dry-run       # preview without writing
scholarposter sync-engagement --force         # re-sync already-synced posts
```

Requires Bluesky credentials in `.env` (`BLUESKY_EMAIL` and `BLUESKY_PASSWORD`).
Only Bluesky records are synced; LinkedIn records are skipped.
Prints `Synced engagement for N posts (M skipped, K errors).`

### Validating configuration

```bash
scholarposter config validate
```

Prints parsed config with sensitive fields redacted.

### Updating configuration after upgrades

```bash
scholarposter config-update            # append new keys (commented out) to config.toml
scholarposter config-update --dry-run  # preview additions without writing
scholarposter config-update --diff     # show unified diff of proposed changes
```

Appends any keys present in the shipped example but absent from your `config.toml`, as commented-out lines at EOF. Safe to run repeatedly — each key is appended at most once.

---

## Global flags

The `run`, `status`, and `retry` commands accept:

| Flag | Effect |
|------|--------|
| `--config PATH` | Config file (default: `config.toml`) |
| `--verbose` | DEBUG logging to stderr |
| `--quiet` | Suppress INFO, show WARNING and above |

Full CLI reference: [docs/commands.md](docs/commands.md)

---

## Configuration

All settings live in `config.toml` ([TOML](https://toml.io/) format).
See [docs/configuration.md](docs/configuration.md) for the complete reference.

Key sections:

- `[mastodon]` — instance URL and credentials file
- `[platforms.bluesky]` / `[platforms.linkedin]` — per-platform filters, media, hashtag rules
- `[enrichment]` — DOI lookup, summarization (4 backends), URL unshortening
- `[[notifications.backends]]` — failure alerts (ntfy, signal, email)
- `[logging]` / `[state]` — log rotation, state file paths

State files (`state.json`, `cache.json`, `bibliography.json`, lock file) are resolved
relative to the directory containing `config.toml`, so all commands operate on the
same files regardless of working directory.

---

## Summarization

scholarposter generates a one-sentence summary (~150 characters) and places it in the
link card description on Bluesky and LinkedIn — not in the post text. Four backends
fall back in order: `gemini → lemonade → ollama → extractive`.

A three-tiered priority determines each card's description: DOI-enriched links use
the Crossref abstract; file links (PDFs) use the AI summary; web pages use the OG
description with the AI summary as fallback. See
[docs/summarization.md](docs/summarization.md) for backend setup, card placement
details, and the link selection algorithm.

---

## Cron setup

```
crontab -e
```

```
*/30 * * * * /home/user/scholarposter/.venv/bin/scholarposter run --config /home/user/scholarposter/config.toml >> /home/user/scholarposter/scholarposter.log 2>&1
```

If using Gemini or Lemonade, add a PATH line at the top of your crontab so cron
can find the binaries:

```
PATH=/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin
```

---

## Notifications

Push notifications on failure (max 1 per platform per run):

```toml
[[notifications.backends]]
type = "ntfy"
topic = "my-scholarposter-alerts"
server = "https://ntfy.sh"
```

Subscribe at `https://ntfy.sh/my-scholarposter-alerts` or install the ntfy app
([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) /
[iOS](https://apps.apple.com/app/ntfy/id1625396347)).

See [docs/configuration.md](docs/configuration.md) for Signal and email backends.

---

## Architecture

```
scholarposter/
├── cli.py                  # Typer CLI — 7 commands + auth sub-app
├── config.py               # Pydantic config models + TOML loading
├── models.py               # UnifiedPost, LinkType, card_description/card_title, PostResult
├── state.py                # JSON state/cache, file locking, bibliography
├── collector.py            # Mastodon toot fetching and HTML→text parsing
├── filters.py              # Hashtag/content-type filtering, hashtag rules
├── gemini_client.py        # Thin re-export from gemini-acp package
├── bibliography.py         # BibTeX and Markdown export formatting
├── discovery/              # OpenAlex citation graph discovery
│   ├── __init__.py         #   CandidatePaper dataclass
│   ├── graph.py            #   cited_by / cites traversal (httpx sync)
│   ├── cache.py            #   atomic TTL cache (discovery_cache.json)
│   ├── ranking.py          #   composite score + top-N ranking
│   └── digest.py           #   format_table + send_digest (SMTP)
├── env_writer.py           # Atomic .env read/write with 0600 permissions
├── auth/
│   ├── cli.py              # scholarposter auth linkedin | mastodon — OAuth sub-app
│   ├── oauth.py            # LinkedIn token exchange and member URN lookup
│   └── callback.py         # Desktop HTTP callback server + headless paste
├── enrichment/
│   ├── pipeline.py         # 5-stage enrichment orchestrator
│   ├── url.py              # URL unshortening, content-type detection, link type classification
│   ├── html.py             # OG tag extraction, trafilatura body text
│   ├── pdf.py              # PyMuPDF metadata + pymupdf4llm text
│   ├── doi.py              # DOI regex detection + Crossref API lookup
│   ├── summarizer.py       # 4 backends: Gemini ACP, Lemonade, Ollama, extractive
│   └── media.py            # Image download, resize, JPEG conversion
├── adapters/
│   ├── base.py             # BaseAdapter ABC
│   ├── bluesky.py          # AT Protocol posting with threading + facets
│   └── linkedin.py         # Community Management API posting
└── notifications/
    ├── base.py             # BaseNotifier ABC
    ├── ntfy.py             # ntfy.sh push notifications
    ├── email.py            # SMTP email notifications
    └── signal.py           # signal-cli REST API notifications
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Another instance is already running` | Stale lock | `rm scholarposter.lock` |
| `sync-engagement` fails to re-run after crash | Stale audit lock | Delete `<audit-log>.lock` (e.g. `rm audit.lock`) before retrying |
| No posts, no errors | All toots processed | `scholarposter status` to check |
| `HTTP 401` on LinkedIn | Token expired (60-day TTL) | Re-run OAuth; see [docs/auth-linkedin.md](docs/auth-linkedin.md) |
| Summarization falls back to extractive | LLM backends unreachable | Check PATH; verify Lemonade/Ollama running; see [docs/summarization.md](docs/summarization.md) |
| Images downsampled | Source too large | Expected — Bluesky enforces ~976 KB blob limit |
| `ModuleNotFoundError` | Wrong Python binary | Use full path: `.venv/bin/scholarposter` |
| `Config not found` | Missing config.toml | Copy `config.toml.example` to `config.toml` |
| After upgrade, new features don't appear in config | Missing config keys | Run `scholarposter config-update` to append new options |
| `Missing BLUESKY_EMAIL env var` | .env not loaded | Verify `.env` exists and has credentials |
| `Mastodon token revoked` | App authorization removed | Re-run `scholarposter auth mastodon`; see [docs/auth-mastodon.md](docs/auth-mastodon.md) |

---

## Setting the crossposting watermark

Use `scholarposter set-watermark` to configure where crossposting begins (see [CLI Reference](docs/commands.md#set-watermark)):

```bash
scholarposter set-watermark --date 2026-01-15        # start after all toots before this date
scholarposter set-watermark --toot-id 113456789012345678  # start after a specific toot
scholarposter set-watermark --toot-url "https://mastodon.social/@you/113456789012345678"
scholarposter set-watermark --date 2026-01-15 --dry-run  # preview without writing
```

---

## Development

```bash
git clone https://github.com/davdittrich/scholarposter.git
cd scholarposter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Run with coverage:

```bash
pytest --cov=scholarposter --cov-report=term-missing
```
