---
title: "tldr-scholar: Fix review findings (7 issues)"
status: in-progress
created: 2026-04-03
work_units: 3
baseline_tests: 64 (tldr-scholar) + 445 (scholarposter)
---

# Fix Review Findings

## Issues from code review (ranked by severity)

| # | Severity | Issue |
|---|----------|-------|
| 1 | BLOCKING | CLI config merge: auto mode merges all backend configs into one flat dict, last wins |
| 2 | REQUIRED | `_PROMPT_TEMPLATE` duplicated in 3 backend files |
| 3 | REQUIRED | Gemini backend sends `text=""` with no comment explaining why |
| 4 | REQUIRED | `{} or None` falsy gotcha in `__init__.py:79` |
| 5 | SUGGESTION | Heavy imports (fitz, pymupdf4llm, trafilatura) at module top of ingest.py |
| 6 | SUGGESTION | Unused `Optional` import in `__init__.py` |
| 7 | SUGGESTION | `_strip_url_credentials` doesn't handle `hostname=None` |

---

## WU-1: Fix config merge bug + prompt deduplication

**Issues:** #1 (config merge), #2 (prompt duplication)
**Files:** `tldr_scholar/cli.py`, `tldr_scholar/backends/gemini.py`,
  `tldr_scholar/backends/lemonade.py`, `tldr_scholar/backends/ollama.py`,
  `tldr_scholar/backends/base.py`, `tldr_scholar/backends/__init__.py`,
  `tests/test_cli.py`, `tests/test_backends.py`

### Fix #1: Config merge bug

The CLI currently does:
```python
backend_config = {}
if backend in ("gemini", "auto"):
    backend_config.update(cfg.gemini.model_dump())
if backend in ("lemonade", "auto"):
    backend_config.update(cfg.lemonade.model_dump())
```

When `backend="auto"`, Ollama's `host` overwrites Lemonade's `host`. Fix:

**Approach:** Pass config as a dict-of-dicts keyed by backend name. Each backend
extracts its own sub-config.

```python
# In cli.py:
if config_path:
    cfg = load_config(Path(config_path))
    backend_config = {
        "gemini": cfg.gemini.model_dump(),
        "lemonade": cfg.lemonade.model_dump(),
        "ollama": cfg.ollama.model_dump(),
    }
```

In `backends/__init__.py`, `get_backend` extracts the sub-config:
```python
def get_backend(name: str, config: dict | None = None) -> BackendBase:
    cls = _BACKEND_MAP.get(name)
    if cls is None:
        raise ValueError(...)
    # Extract sub-config for this specific backend
    if config and name in config:
        return cls(config[name])
    return cls(config)  # fallback: pass full dict (library API compatibility)
```

This way:
- CLI with `--backend auto` + config: each backend gets its own sub-dict
- CLI with `--backend lemonade` + config: gets `config["lemonade"]` specifically
- Library API: `summarize(backend_config={"host": "..."})` still works (flat dict, no nesting)

### Fix #2: Extract shared prompt template

Move `_PROMPT_TEMPLATE` to `backends/base.py` as a module constant:

```python
# backends/base.py
SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the following document in approximately {max_chars} characters.\n"
    "Focus on: {focus}.\n"
    "Be concise, precise, and factual. Do not add information not in the source.\n"
    "{hashtag_instruction}\n\n"
    "<document>\n{text}\n</document>"
)
```

Each LLM backend imports it:
```python
from tldr_scholar.backends.base import BackendBase, SUMMARY_PROMPT_TEMPLATE
```

Delete the `_PROMPT_TEMPLATE` definition from `gemini.py`, `lemonade.py`, `ollama.py`.

### TDD Tests
- `--backend auto` with config containing different hosts per backend → each backend gets its own host
- `--backend lemonade` with config → gets lemonade-specific config, not merged
- Library API with flat `backend_config={"host": "..."}` → still works
- `SUMMARY_PROMPT_TEMPLATE` imported from `base.py` → contains `<document>` delimiters
- Existing tests pass unchanged

---

## WU-2: Clean code fixes

**Issues:** #3 (empty text comment), #4 (falsy gotcha), #6 (unused import), #7 (hostname None)
**Files:** `tldr_scholar/__init__.py`, `tldr_scholar/backends/gemini.py`

### Fix #3: Add comment to Gemini empty text

```python
return summarize_via_gemini(
    text="",  # text already embedded in prompt via <document> delimiters
    prompt=prompt,
    model=self._model, timeout=self._timeout,
)
```

### Fix #4: Replace `{} or None` with explicit check

```python
# Before:
config=req.backend_config or None,
# After:
config=req.backend_config if req.backend_config else None,
```

### Fix #6: Remove unused `Optional` import

Delete `Optional` from `from typing import Any, Optional` in `__init__.py`.

### Fix #7: Handle `hostname=None` in `_strip_url_credentials`

```python
def _strip_url_credentials(url: str) -> str:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        hostname = parsed.hostname or ""
        netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
        return parsed._replace(netloc=netloc).geturl()
    return url
```

### TDD Tests
- `_strip_url_credentials("http:///path")` → returns URL without crash
- `_strip_url_credentials("https://user:pass@host:8080/p")` → strips credentials, keeps port
- Existing tests pass

---

## WU-3: Lazy imports for heavy dependencies

**Issue:** #5 (fitz, pymupdf4llm, trafilatura at module top)
**Files:** `tldr_scholar/ingest.py`, `tests/test_ingest.py`

### Fix #5: Move heavy imports inside functions

Move `import fitz`, `import pymupdf4llm`, `import trafilatura` from module top
back into the functions that use them (`_ingest_pdf`, `_ingest_url`).

Keep `import httpx` and `from loguru import logger` at module top (lightweight).

This means `from tldr_scholar import summarize` no longer pays ~200ms for PDF/HTML
libraries when summarizing plain text.

**Test impact:** The URL tests that `patch("tldr_scholar.ingest.trafilatura")`
will break because trafilatura is no longer a module-level attribute. Fix:
mock at the import path inside the function — use `patch.dict("sys.modules", ...)`
or restructure the tests to mock at a higher level.

Simpler approach: keep `import trafilatura` at module top (it's lightweight — pure
Python heuristics). Only move `fitz` and `pymupdf4llm` inside `_ingest_pdf` and
`_ingest_url`'s PDF branch (these are the heavy C-extension imports).

### TDD Tests
- `from tldr_scholar import summarize` → no fitz import (verify with sys.modules check)
- Existing ingest tests pass
- URL tests with mock trafilatura still work

---

## Execution Order

WU-1 (config + prompt) → WU-2 (clean code) → WU-3 (lazy imports)

## Files Modified

| File | WU |
|---|---|
| `tldr_scholar/cli.py` | WU-1 |
| `tldr_scholar/backends/base.py` | WU-1 |
| `tldr_scholar/backends/__init__.py` | WU-1 |
| `tldr_scholar/backends/gemini.py` | WU-1, WU-2 |
| `tldr_scholar/backends/lemonade.py` | WU-1 |
| `tldr_scholar/backends/ollama.py` | WU-1 |
| `tldr_scholar/__init__.py` | WU-2 |
| `tldr_scholar/ingest.py` | WU-3 |
| `tests/test_cli.py` | WU-1 |
| `tests/test_backends.py` | WU-1 |
| `tests/test_ingest.py` | WU-3 |

## Success Criteria

1. All 64 tldr-scholar tests pass
2. All 445 scholarposter tests pass
3. `--backend auto` with config → each backend gets its own config (not merged)
4. `_PROMPT_TEMPLATE` defined in exactly one place
5. No unused imports
6. `_strip_url_credentials` handles malformed URLs
