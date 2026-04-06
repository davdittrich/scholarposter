"""Atomic .env file reader/writer with secure permissions."""
from __future__ import annotations

import os
from pathlib import Path


def read_env(env_path: Path) -> dict[str, str]:
    """Read a .env file into a dict. Returns {} if file doesn't exist."""
    if not env_path.exists():
        return {}
    result = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            result[key.strip()] = value
    return result


def write_env(env_path: Path, updates: dict[str, str]) -> None:
    """Atomically update .env: read existing, merge updates, write via temp+rename.

    Creates temp file with 0600 permissions from creation.
    Preserves all existing entries not in updates.
    """
    existing = read_env(env_path)
    existing.update(updates)

    tmp_path = env_path.parent / (env_path.name + ".tmp")
    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            for key, value in existing.items():
                f.write(f'{key}="{value}"\n')
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    os.rename(str(tmp_path), str(env_path))
