# Knowledge Base — mastodon-crosspost

## Architecture

Three independent scripts cross-post the latest unprocessed Mastodon toot:

| Script | Target | Key deps |
|---|---|---|
| `m2blusky.py` | Bluesky (AT Protocol) | `atproto`, `Mastodon.py` |
| `m2tw.py` | Twitter/X | `tweepy`, `Mastodon.py` |
| `m2li.py` | LinkedIn | `requests`, `ln_oauth.py` |

Each script tracks its last-processed toot ID in a `lasttoot*.txt` file.

The `scholarposter/` package is the rewritten version with proper architecture:
adapters, enrichment pipeline, state management, notifications.

## Shared utilities
- `config.py` — Twitter API credentials (loaded from `.env`)
- `ln_oauth.py` — LinkedIn OAuth2 flow, reads `credentials.json`
- `credentials.json` — LinkedIn OAuth tokens (gitignored)

## Common patterns across scripts
- HTML parsing of Mastodon toot content via BeautifulSoup
- URL unshortening and content-type detection
- PDF metadata extraction (pikepdf, pdfplumber, pdftitle)
- Text summarization via sumy (LSA + KL)
- DOI resolution via crossref API
- Long posts are chunked into threads (textwrap)

## Patterns (scholarposter)
- State merge-update: `entry = state.get(platform, {}).copy()` then selective overlay — never replace entire platform dict
- HEAD for URL unshortening with GET fallback on 405 — avoids downloading full response bodies
- DOI post-match stripping (`.;,:`) instead of regex modification — DOIs legitimately end in `)`, `/`
- Bluesky grapheme counting via `grapheme.slice()` and `grapheme.length()` — AT Protocol counts grapheme clusters, not code points
- Thumbnail bytes downloaded in `_enrich_html` to unblock Bluesky link card embeds
- `_enrich_doi` skips `detect_dois()` when `link.doi` already set by HTML/PDF stage — avoids redundant detection after URL resolution

## Gotchas (scholarposter)
- `grapheme` PyPI package pins `>=0.6` (no 1.0 release exists)
- `config.toml.example` must use `from_addr`/`to_addr` (not `from`/`to`) — Python keyword conflict
- `ExtractiveSummarizationConfig.timeout_seconds` kept for backward compat even though `summarize_extractive` no longer uses it
- STARTTLS must be capability-based (`has_extn("starttls")`), not port-based; port 465 uses SMTP_SSL
