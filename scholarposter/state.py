"""State management, caching, and file locking for scholarposter."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Generator, Optional

from loguru import logger

from scholarposter.models import BibliographyEntry, PlatformState


class StateManager:
    def __init__(
        self,
        state_dir: Path = Path("."),
        state_file: str = "state.json",
        cache_file: str = "cache.json",
        lock_file: str = "scholarposter.lock",
    ):
        self._dir = Path(state_dir)
        self._state_path = self._dir / state_file
        self._cache_path = self._dir / cache_file
        self._lock_path = self._dir / lock_file
        self._lock_fd: Optional[int] = None

    # -------------------------------------------------------------------------
    # State
    # -------------------------------------------------------------------------

    def load_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        with open(self._state_path) as f:
            return json.load(f)

    def _save_state(self, data: dict[str, Any]) -> None:
        self._atomic_write(self._state_path, data)

    def update_platform_state(self, platform: str, ps: PlatformState) -> None:
        if self._lock_fd is None:
            logger.warning("update_platform_state called without holding lock")
        state = self.load_state()
        entry = state.get(platform, {}).copy()  # preserve existing fields
        if ps.last_toot_id is not None:
            entry["last_toot_id"] = ps.last_toot_id
        if ps.last_status is not None:
            entry["last_status"] = ps.last_status
        if ps.last_posted_at is not None:
            entry["last_posted_at"] = ps.last_posted_at.isoformat()
        if ps.last_error is not None:
            entry["last_error"] = ps.last_error
        elif ps.last_status is not None and ps.last_status != "failed":
            # On non-failure, clear any stale error from prior run
            entry.pop("last_error", None)
        state[platform] = entry
        self._save_state(state)

    def get_since_id(self, platform: str) -> Optional[int]:
        state = self.load_state()
        return state.get(platform, {}).get("last_toot_id")

    # -------------------------------------------------------------------------
    # Cache
    # -------------------------------------------------------------------------

    def _load_cache(self) -> dict[str, Any]:
        if not self._cache_path.exists():
            return {}
        with open(self._cache_path) as f:
            return json.load(f)

    def _save_cache(self, data: dict[str, Any]) -> None:
        self._atomic_write(self._cache_path, data)

    def cache_get(self, key: str) -> Optional[dict[str, Any]]:
        cache = self._prune_cache()
        entry = cache.get(key)
        if entry is None:
            return None
        return entry["value"]

    def cache_set(self, key: str, value: dict[str, Any], ttl_days: int) -> None:
        cache = self._load_cache()
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
        cache[key] = {"value": value, "expires_at": expires_at.isoformat()}
        self._save_cache(cache)

    def _prune_cache(self) -> dict[str, Any]:
        cache = self._load_cache()
        now = datetime.now(timezone.utc)
        pruned = {k: v for k, v in cache.items()
                  if datetime.fromisoformat(v["expires_at"]) > now}
        if len(pruned) < len(cache):
            self._save_cache(pruned)
        return pruned

    # -------------------------------------------------------------------------
    # Bibliography
    # -------------------------------------------------------------------------

    def load_bibliography(self) -> list[dict]:
        """Load bibliography entries. Returns [] on missing/corrupt file."""
        bib_path = self._dir / "bibliography.json"
        if not bib_path.exists():
            return []
        try:
            with open(bib_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"bibliography.json corrupt, treating as empty: {e}")
            return []

    def _save_bibliography(self, data: list) -> None:
        self._atomic_write(self._dir / "bibliography.json", data)

    def append_bibliography(self, entry: "BibliographyEntry") -> None:
        """Append entry, dedup by DOI. Must be called while lock is held."""
        if self._lock_fd is None:
            logger.warning("append_bibliography called without holding lock")
        bib = self.load_bibliography()
        entry_dict = entry.model_dump(mode="json")
        for existing in bib:
            if existing.get("doi") == entry.doi:
                old_platforms = existing.get("platforms", [])
                existing["platforms"] = list(set(old_platforms + entry.platforms))
                break
        else:
            bib.append(entry_dict)
        self._save_bibliography(bib)

    # -------------------------------------------------------------------------
    # Locking
    # -------------------------------------------------------------------------

    def acquire_lock(self) -> bool:
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_WRONLY)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = fd
            return True
        except OSError:
            return False

    def release_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None

    @contextmanager
    def lock(self) -> Generator[None, None, None]:
        if not self.acquire_lock():
            raise RuntimeError("Could not acquire lock")
        try:
            yield
        finally:
            self.release_lock()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _atomic_write(self, path: Path, data: dict[str, Any] | list[Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.rename(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
