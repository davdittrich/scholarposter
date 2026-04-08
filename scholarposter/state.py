"""State management, caching, and file locking for scholarposter."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta, date
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
        audit_file: Optional[Path] = None,
        bibliography_file: str = "bibliography.json",
        audit_rotation_max_bytes: int = 0,   # 0 = disabled
        audit_retention_days: int = 0,        # 0 = disabled
    ):
        self._dir = Path(state_dir)
        self._state_path = self._dir / state_file
        self._cache_path = self._dir / cache_file
        self._lock_path = self._dir / lock_file
        self._audit_path: Optional[Path] = audit_file
        self._bibliography_path = self._dir / bibliography_file
        self._lock_fd: Optional[int] = None
        self._audit_rotation_max_bytes = audit_rotation_max_bytes
        self._audit_retention_days = audit_retention_days

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
        entry = state.get(platform, {}).copy()
        for key, value in ps.model_dump(exclude_unset=True).items():
            if value is None:
                entry.pop(key, None)
            elif isinstance(value, (datetime, date)):
                entry[key] = value.isoformat()
            else:
                entry[key] = value
        # Clear stale error on non-failure status
        if ps.last_status is not None and ps.last_status != "failed" and ps.last_error is None:
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
        if not self._bibliography_path.exists():
            return []
        try:
            with open(self._bibliography_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"bibliography.json corrupt, treating as empty: {e}")
            return []

    def _save_bibliography(self, data: list) -> None:
        self._atomic_write(self._bibliography_path, data)

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
    # Audit log (FR-90)
    # -------------------------------------------------------------------------

    def append_audit(self, record: dict[str, Any]) -> None:
        """Append one JSON-lines record to audit.jsonl.

        Non-blocking: write failures are logged at WARNING and never propagate.
        Must be called while the process lock is held (warns if not).
        No-op when audit_file is None (audit disabled).
        """
        if self._audit_path is None:
            return
        if self._lock_fd is None:
            logger.warning("append_audit called without holding lock")
        try:
            line = json.dumps(record, default=str) + "\n"
            fd = os.open(
                str(self._audit_path),
                os.O_CREAT | os.O_APPEND | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            if (
                self._audit_rotation_max_bytes > 0
                and self._audit_path.exists()
                and self._audit_path.stat().st_size >= self._audit_rotation_max_bytes
            ):
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                stem = self._audit_path.stem
                suffix = self._audit_path.suffix
                archive = self._audit_path.with_name(f"{stem}.{stamp}{suffix}")
                counter = 0
                while archive.exists():
                    counter += 1
                    archive = self._audit_path.with_name(f"{stem}.{stamp}-{counter}{suffix}")
                os.rename(str(self._audit_path), str(archive))
                # Create a fresh empty audit file so callers can stat it
                fd2 = os.open(str(self._audit_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                os.close(fd2)
                logger.info("Audit log rotated to %s.", archive.name)
        except Exception as e:
            logger.warning(f"Audit write failed (non-blocking): {e}")

    def prune_audit(self) -> int:
        """Delete audit records older than audit_retention_days. Returns count pruned.

        No-op when audit_retention_days is 0 or audit_file is None.
        Must be called while the process lock is held.
        """
        if self._audit_retention_days == 0 or self._audit_path is None:
            return 0
        if not self._audit_path.exists():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._audit_retention_days)
        with open(self._audit_path) as f:
            records = [json.loads(line) for line in f if line.strip()]
        kept = [
            r for r in records
            if datetime.fromisoformat(r.get("timestamp", "1970-01-01T00:00:00+00:00")) >= cutoff
        ]
        pruned = len(records) - len(kept)
        if pruned > 0:
            self._atomic_write_jsonl(self._audit_path, kept)
            logger.info("Pruned %d audit record(s) older than %d days.", pruned, self._audit_retention_days)
        return pruned

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

    def _atomic_write_jsonl(self, path: Path, records: list[dict]) -> None:
        """Write records as JSON-lines atomically (temp file + os.rename, 0o600)."""
        tmp_path = path.with_name(path.name + ".tmp")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec, default=str) + "\n")
            os.rename(str(tmp_path), str(path))
        except BaseException:
            try:
                os.unlink(str(tmp_path))
            except OSError:
                pass
            raise
