# Project Context (Maintained by Orchestrator)

## Tooling
- Language: Python 3.11+
- Package manager: pip (with pyproject.toml)
- Test runner: pytest 8.0+ (config in pyproject.toml)
- Linter: ruff
- Build: setuptools
- Venv: .venv (activate with `source .venv/bin/activate`)

## Completed Work Units
| WU | Title | Key Files | Status |
|----|-------|-----------|--------|

## Established Patterns
- Tests use `respx` for mocking httpx calls
- Tests use loguru list-based sink for log assertions (not pytest caplog)
- Pydantic v2 models with `model_copy(update={...})` for immutable updates
- State persisted as JSON with atomic write (tmp + rename)
