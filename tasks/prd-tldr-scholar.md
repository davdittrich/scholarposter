# PRD: tldr-scholar (Revision 2)

**Standalone academic text summarizer — CLI + Python library**

| Field | Value |
|-------|-------|
| Status | Draft (R2 — addresses 17 blocking issues from design review R1) |
| Author | Claude Code |
| Date | 2026-04-03 |
| Location | `tldr-scholar/` (new top-level directory, separate package) |

---

## 1. Executive Summary

### Problem

Researchers need to quickly assess papers, articles, and documents before reading
in full. Existing tools require cloud APIs with no local option, produce low-quality
extractive summaries, or are tightly coupled to specific workflows.

### Solution

**tldr-scholar** is a standalone Python package (CLI + library) that summarizes PDFs,
Markdown files, plain text files, and web pages. Four summarization backends
(Gemini CLI via ACP, Lemonade, Ollama, extractive). Content-derived hashtags for
social media. Designed as a reusable dependency — scholarposter will eventually
import it instead of maintaining its own summarization code.

### Key Architectural Decision (R2)

The Gemini ACP client (`gemini_client.py`) is extracted into a **shared `gemini-acp/`
package** — a minimal third package that both tldr-scholar and scholarposter import.
This avoids maintaining two divergent copies of a non-trivial async/ACP client.

### Success Criteria

- Summarize a PDF, URL, .md, or .txt file in a single command
- Produce output in text, JSON, or Markdown format
- Generate relevant, short hashtags from content
- Work with zero configuration (extractive fallback)
- Be importable as a Python library by scholarposter
- 100% test coverage

### Timeline

Phase 1: 5-7 work units. Core summarization + CLI + all backends + shared gemini-acp.

---

## 2. Goals

| # | Goal | Measure |
|---|------|---------|
| G1 | Single-command summarization of any supported input | `tldr-scholar paper.pdf` produces a summary |
| G2 | Configurable summary length | `--length short\|medium\|long` or `--max-chars N` |
| G3 | Thematic focus control | `--focus "methodology"` changes summary emphasis |
| G4 | Hashtag generation | `--hashtags 5` produces 5 content-derived hashtags |
| G5 | All scholarposter backends available | Gemini ACP, Lemonade, Ollama, extractive |
| G6 | Library API for programmatic use | `from tldr_scholar import summarize` |
| G7 | Zero-config startup | Works out of the box with extractive backend |
| G8 | 100% test coverage | Matches scholarposter standards |

---

## 3. User Stories

### US-001: Summarize a PDF from the command line

**Description**: As a researcher, I want to summarize a PDF paper so that I can
decide whether to read it in full.

**Acceptance Criteria**:
- [ ] `tldr-scholar paper.pdf` prints a summary to stdout
- [ ] Summary defaults to ~500 characters (configurable via `--max-chars`)
- [ ] PDF text extraction uses PyMuPDF (first 20 pages)
- [ ] Works with no config file (extractive backend)
- [ ] Exit code 0 on success, 1 on failure with error to stderr
- [ ] Password-protected PDF → exit 1 with "Error: PDF is password-protected"
- [ ] Empty text after extraction → exit 1 with "Error: no text extracted from <source>"
- [ ] Unsupported file type (.png, .zip, etc.) → exit 2 with "Unsupported file type: <ext>"

**Priority**: High

### US-002: Summarize a web page by URL

**Description**: As a researcher, I want to summarize an article by URL.

**Acceptance Criteria**:
- [ ] `tldr-scholar https://example.com/article` fetches, extracts text, summarizes
- [ ] URL resolution follows redirects (HEAD for Content-Type detection)
- [ ] HTML text extraction via trafilatura
- [ ] PDF URLs detected by Content-Type containing "pdf" OR `.pdf` extension
- [ ] Streaming HTML fetch with 5MB cap
- [ ] Only `http` and `https` schemes allowed; others → exit 2

**Priority**: High

### US-003: Summarize Markdown and plain text files

**Description**: As a researcher, I want to summarize local .md and .txt files.

**Acceptance Criteria**:
- [ ] `tldr-scholar notes.md` reads file, strips Markdown formatting, summarizes
- [ ] `tldr-scholar notes.txt` reads and summarizes plain text
- [ ] Files >5MB → warning to stderr, truncate to first 5MB
- [ ] File type detected by extension (.md, .txt, .pdf)

**Priority**: High

### US-004: Control summary length

**Description**: As a user, I want to control how long the summary is.

**Acceptance Criteria**:
- [ ] `--length short` → ~200 chars, `--length medium` → ~500 chars, `--length long` → ~1000 chars
- [ ] `--max-chars 300` overrides `--length` presets
- [ ] Default is `medium` (500 chars) when neither flag is given
- [ ] `--max-chars` is the canonical parameter internally; `--length` translates to it
- [ ] LLM backends receive the length constraint in the prompt
- [ ] Extractive backend uses `max_chars` parameter directly

**Priority**: High

### US-005: Control thematic focus

**Description**: As a researcher, I want to focus the summary on a specific aspect.

**Acceptance Criteria**:
- [ ] `--focus "methodology and experimental design"` adjusts the prompt
- [ ] Default focus: "main findings and novel insights"
- [ ] LLM backends: focus text included in summarization prompt
- [ ] Extractive backend: keyword biasing — sentences containing focus terms ranked higher
- [ ] Focus text appears in JSON output metadata

**Priority**: Medium

### US-006: Generate hashtags

**Description**: As a user who shares research, I want content-derived hashtags.

**Acceptance Criteria**:
- [ ] `--hashtags 5` generates 5 hashtags from the content
- [ ] Hashtags: short (1-2 words), lowercase, `#`-prefixed, max 30 chars each
- [ ] Default: 0 (no hashtags unless requested)
- [ ] LLM backends: hashtag generation is part of the summarization prompt
- [ ] Extractive backend: TF-IDF heuristic (top terms by frequency, no NLTK)
- [ ] LLM returns fewer than requested → return what was produced (best-effort, log DEBUG)
- [ ] Hashtags included in JSON and Markdown output; text output: separate line after summary

**Priority**: Medium

### US-007: Output format selection

**Description**: As a user, I want to choose output format.

**Acceptance Criteria**:
- [ ] `--format text` (default): summary to stdout; hashtags on next line only if N > 0
- [ ] `--format json`: `{"summary": "...", "hashtags": [...], "metadata": {...}}`
- [ ] `--format markdown`: `## Summary\n...\n## Hashtags\n#tag1 #tag2`
- [ ] JSON metadata includes: `source`, `input_type` (`"pdf"|"html"|"markdown"|"text"`),
      `backend_used`, `max_chars`, `focus`, `char_count`
- [ ] When `--hashtags 0` and `--format text`: no second line (clean pipe-friendly output)

**Priority**: High

### US-008: Backend selection

**Description**: As a user, I want to choose which summarization backend to use.

**Acceptance Criteria**:
- [ ] `--backend gemini|lemonade|ollama|extractive|auto`
- [ ] Default: `auto` (fallback chain: gemini → lemonade → ollama → extractive)
- [ ] Explicit `--backend X`: use only X, no fallback; exit 1 on failure
- [ ] Invalid `--backend` value → exit 2 with "Invalid backend. Choose: gemini, lemonade, ollama, extractive, auto"
- [ ] `--backend` validated at parse time (Typer Choice), not runtime
- [ ] Lemonade auto-loads model if none loaded (preferred_models, ctx_size)
- [ ] Gemini uses ACP protocol (from shared `gemini-acp` package)
- [ ] Optional `tldr-scholar.toml` config for persistent backend settings
- [ ] Works with no config (extractive fallback)

**Priority**: High

### US-009: Python library API

**Description**: As a developer (or scholarposter), I want to use tldr-scholar as a library.

**Acceptance Criteria**:
- [ ] `from tldr_scholar import summarize` — main entry point
- [ ] `summarize(text="...", max_chars=500)` → `SummaryResult`
- [ ] `summarize(request=SummaryRequest(...))` → `SummaryResult`
- [ ] `text=` and `request=` are mutually exclusive; both → `ValueError`
- [ ] Neither `text=` nor `request=` → `ValueError("text or request required")`
- [ ] `summarize_file(path="paper.pdf", ...)` — handles ingestion
- [ ] `summarize_url(url="https://...", ...)` — handles URL fetch
- [ ] `SummaryResult` has `.text`, `.hashtags`, `.metadata` attributes
- [ ] Backend config passable as parameters (no forced config file)
- [ ] LLM gibberish/refusal response → library: return result with empty summary, log WARNING
- [ ] CLI with `--backend auto` + LLM gibberish: fallback chain continues to next backend
- [ ] CLI with `--backend X` (explicit) + LLM gibberish/empty: exit 1 with "Error: backend 'X' returned empty response"

**Priority**: High

---

## 4. Functional Requirements

### Input Handling

| # | Requirement |
|---|-------------|
| FR-01 | Accept a single positional argument: file path or URL |
| FR-02 | Detect input type by extension (.pdf, .md, .txt) or URL scheme (http/https) |
| FR-03 | Unsupported extension or scheme → exit 2 with descriptive error |
| FR-04 | For URLs: streaming fetch (5MB cap), detect PDF via Content-Type or extension |
| FR-05 | For PDFs: extract text from first 20 pages via PyMuPDF; password-protected → error |
| FR-06 | For Markdown: strip formatting before summarizing |
| FR-07 | For plain text: read as-is |
| FR-08 | Input exceeding 5MB: warn to stderr, truncate |
| FR-09 | Empty text after extraction (any input type) → exit 1 with "no text extracted" message |

### Summarization

| # | Requirement |
|---|-------------|
| FR-10 | Support 4 backends: gemini (ACP via shared gemini-acp), lemonade, ollama, extractive |
| FR-11 | Default fallback chain (auto): gemini → lemonade → ollama → extractive |
| FR-12 | `--backend X` (explicit): use only X, no fallback; invalid value → exit 2 at parse time |
| FR-13 | `--max-chars` controls output length (default 500) |
| FR-14 | `--length` presets: short=200, medium=500, long=1000; `--max-chars` overrides |
| FR-15 | `--focus` adds thematic guidance to LLM prompts; for extractive, biases sentence ranking |
| FR-16 | LLM prompt template includes `{max_chars}`, `{focus}`, `{text}` with `<document>` delimiters |
| FR-17 | Extractive: copy sumy KL+LSA two-pass algorithm verbatim from scholarposter |

### Hashtag Generation

| # | Requirement |
|---|-------------|
| FR-20 | `--hashtags N` generates N hashtags (default 0) |
| FR-21 | LLM: append instruction to prompt; extract from response via `re.findall(r'#\w+')` |
| FR-22 | Extractive: TF-IDF heuristic (top N terms, no NLTK dependency) |
| FR-23 | Hashtags: lowercase, no spaces, `#`-prefixed, max 30 chars each |
| FR-24 | LLM returns fewer than N → return what was produced (best-effort), log DEBUG |

### Output

| # | Requirement |
|---|-------------|
| FR-30 | `--format text`: summary to stdout; hashtags on next line ONLY when N > 0 |
| FR-31 | `--format json`: structured with `summary`, `hashtags`, `metadata` |
| FR-32 | `--format markdown`: `## Summary` section; `## Hashtags` section ONLY when N > 0 |
| FR-33 | JSON metadata: `source`, `input_type`, `backend_used`, `max_chars`, `focus`, `char_count` |
| FR-34 | Exit codes: 0 success, 1 runtime error, 2 invalid arguments |
| FR-35 | Explicit `--backend X` failure → stderr: "Error: backend 'X' failed: <reason>" |

### Configuration

| # | Requirement |
|---|-------------|
| FR-40 | Optional `tldr-scholar.toml` config file |
| FR-41 | Config sections: `[gemini]`, `[lemonade]`, `[ollama]` (per-backend settings) |
| FR-42 | `--config` flag or `TLDR_SCHOLAR_CONFIG` env var |
| FR-43 | Library API: all config passed as parameters (no file dependency) |

---

## 5. Non-Goals

| # | Non-Goal | Rationale |
|---|----------|-----------|
| NG-01 | Multi-document summarization | MVP is single-input |
| NG-02 | Translation | Pipe through translation tools |
| NG-03 | Platform-specific hashtag trends | Content-derived only |
| NG-04 | GUI or web interface | CLI + library only |
| NG-05 | Citation extraction / bibliography | scholarposter's domain |
| NG-06 | Cross-posting | scholarposter's domain |
| NG-07 | Refactoring scholarposter to use tldr-scholar | Phase 2 project |
| NG-08 | Non-English text optimization | Document limitation; use LLM backend for non-Latin |

---

## 6. Technical Specifications

### Package Structure

```
gemini-acp/                          ← NEW shared package
├── pyproject.toml
├── gemini_acp/
│   ├── __init__.py                  # exports: summarize_via_gemini, ACP_AVAILABLE
│   └── client.py                    # _GeminiClient, _run_prompt, _run_sync
└── tests/test_gemini_acp.py

tldr-scholar/                        ← NEW standalone package
├── pyproject.toml
├── README.md
├── tldr-scholar.toml.example
├── tldr_scholar/
│   ├── __init__.py                  # Public API: summarize, summarize_file, summarize_url
│   ├── __main__.py
│   ├── cli.py                       # Typer CLI
│   ├── config.py                    # Pydantic config models
│   ├── ingest.py                    # PDF, HTML, Markdown, plain text ingestion
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── base.py                  # BackendBase ABC
│   │   ├── gemini.py                # imports from gemini_acp
│   │   ├── lemonade.py              # OpenAI-compat + auto-load
│   │   ├── ollama.py                # Ollama API
│   │   └── extractive.py            # sumy KL+LSA (copied from scholarposter)
│   ├── hashtags.py                  # Parse from LLM response + TF-IDF fallback
│   └── models.py                    # SummaryResult, SummaryRequest, BackendConfig
└── tests/
    ├── test_cli.py
    ├── test_ingest.py
    ├── test_backends.py
    ├── test_hashtags.py
    └── test_models.py

scholarposter/                       ← EXISTING (Phase 2: migrate to use tldr-scholar)
├── gemini_client.py                 ← becomes: from gemini_acp import summarize_via_gemini
└── ...
```

### Backend Abstract Interface (R2 — CTO blocker D2)

```python
class BackendBase(ABC):
    @abstractmethod
    def summarize(
        self,
        text: str,
        max_chars: int,
        focus: str,
        hashtag_instruction: str,
    ) -> Optional[str]:
        """Returns raw response text (summary + hashtags combined), or None on failure."""
```

All backends return a raw string. `hashtags.py` handles parsing the summary/hashtags
split for all backends (LLM response parsing + TF-IDF for extractive).

### Backend Config Schema (R2 — CTO blocker D3)

```python
# Per-backend config keys when passed via library API:
# summarize(backend="lemonade", backend_config={...})

# Gemini:
{"model": str, "timeout": int}  # model="" = CLI default

# Lemonade:
{"model": str, "host": str, "timeout": int, "ctx_size": int,
 "load_timeout": int, "preferred_models": list[str]}

# Ollama:
{"model": str, "host": str, "timeout": int}

# Extractive:
{"max_sentences": int}
```

### Data Models

```python
class SummaryRequest(BaseModel):
    text: str
    max_chars: int = 500
    focus: str = "main findings and novel insights"
    hashtags: int = 0
    backend: Literal["auto", "gemini", "lemonade", "ollama", "extractive"] = "auto"
    backend_config: dict[str, Any] = {}
    # backend_config is intentionally dict[str, Any] for flexibility.
    # Each backend validates its own keys at runtime and raises ValueError
    # on unrecognized keys. See "Backend Config Schema" for valid keys.

class SummaryResult(BaseModel):
    text: str
    hashtags: list[str] = []
    metadata: SummaryMetadata

class SummaryMetadata(BaseModel):
    source: str = ""
    input_type: str = ""       # "pdf", "html", "markdown", "text"
    backend_used: str = ""
    max_chars: int = 500
    focus: str = ""
    char_count: int = 0
```

### LLM Prompt Template (R2 — Security: delimiter wrapping)

```
Summarize the following document in approximately {max_chars} characters.
Focus on: {focus}.
Be concise, precise, and factual. Do not add information not in the source.
{hashtag_instruction}

<document>
{text}
</document>
```

The `<document>` delimiters provide a minimal boundary between system instructions
and user content, reducing (but not eliminating) prompt injection risk.

---

## 7. Security Considerations (R2 — new section)

### Threat Model

| Threat | Risk | Mitigation |
|--------|------|------------|
| **Path traversal** (`../../etc/passwd`) | Low (local CLI tool) | **Allowed by design** — tool runs as the user's own process with the user's permissions. Documented as known behavior. |
| **SSRF** (private IPs via URL input) | Medium | **Documented limitation.** Only `http`/`https` schemes allowed (FR-02). No private-IP blocking in Phase 1. Add note: "Do not use on untrusted URLs in cloud environments." |
| **Prompt injection** (malicious text in documents) | Low-Medium | `<document>` delimiters in prompt template (FR-16). Impact limited to output manipulation (bad summary/hashtags). Documented as known limitation. |
| **Credential leakage** | Medium | `backend_config` values NEVER logged. URL credentials (userinfo) stripped from `SummaryMetadata.source`. Error messages from backend failures do not echo API keys. |
| **Subprocess injection** (model names) | Low | Model names passed via `subprocess.run` list (no shell). Validated against format regex `^[A-Za-z0-9._-]{1,128}$` before invocation. |

---

## 8. Risks & Mitigation

| # | Risk | Impact | Probability | Mitigation |
|---|------|--------|-------------|------------|
| R1 | LLM produces hashtags in wrong format | Missing hashtags | Medium | Best-effort parsing; TF-IDF fallback; logged at DEBUG |
| R2 | PDF is scanned/password-protected | No text extracted | Low | Detect and return specific error message per cause |
| R3 | Config conflict with scholarposter | Two configs | Medium | Library API: all config as params; no file dependency |
| R4 | Large input causes OOM | Process crash | Low | 5MB cap on all inputs |
| R5 | LLM returns gibberish or refusal | Bad summary | Medium | Return empty summary, log WARNING; fallback for auto |
| R6 | gemini-acp shared package versioning | Breaking changes | Low | Monorepo path dependency for Phase 1; semantic versioning + PyPI if needed later |
| R7 | Non-English extractive quality | Poor hashtags/focus | Medium | Documented in NG-08; recommend LLM backend |

---

## 9. Phased Rollout

### Phase 1: Core MVP (this PRD)

1. Extract `gemini-acp/` shared package from scholarposter
2. Create `tldr-scholar/` with all 4 backends
3. CLI + library API
4. Hashtag generation (LLM + TF-IDF)
5. Text/JSON/Markdown output
6. Update scholarposter to import from `gemini-acp/` (thin migration)

### Phase 2: scholarposter Integration

- Refactor scholarposter to use `tldr_scholar` for summarization
- Remove duplicate code from scholarposter
- Map scholarposter's config to tldr_scholar's backend_config

### Phase 3: Enhancements

- Batch processing
- Custom prompt templates
- `--language` flag for non-English
- Citation extraction from PDFs

---

## 10. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Summarize 10-page PDF (extractive) | < 2 seconds | `time tldr-scholar paper.pdf` |
| Summarize 10-page PDF (LLM, loaded) | < 30 seconds | `time tldr-scholar --backend lemonade paper.pdf` |
| Test coverage | 100% | `pytest --cov` |
| Zero-config works | Exit 0 | `pip install tldr-scholar && tldr-scholar paper.pdf` (DoD item) |
| Library import | < 100ms | `python -c "import time; t=time.time(); from tldr_scholar import summarize; print(time.time()-t)"` |

---

## 11. Open Questions

| # | Question | Decision Needed By |
|---|----------|--------------------|
| Q3 | When scholarposter migrates (Phase 2), should config map 1:1 or use a translation layer? | Phase 2 |

Q1 (focus for extractive) and Q2 (hashtag method) resolved: keyword biasing + TF-IDF.

---

## Definition of Done

- [ ] `gemini-acp/` package extracted and installable
- [ ] `pip install tldr-scholar` works
- [ ] `tldr-scholar paper.pdf` produces a summary (extractive, no config)
- [ ] `tldr-scholar https://arxiv.org/... --format json --hashtags 5` works
- [ ] `from tldr_scholar import summarize` works in Python
- [ ] All 4 backends tested (mocked for LLM backends)
- [ ] `--length`, `--max-chars`, `--focus`, `--hashtags`, `--format`, `--backend` flags work
- [ ] Password-protected PDF, empty text, unsupported file type produce correct errors
- [ ] `--backend invalid` → exit 2 at parse time
- [ ] Tests pass with 100% coverage
- [ ] README with installation, quick start, and API reference
- [ ] scholarposter's `gemini_client.py` migrated to import from `gemini-acp/`
