"""Discovery result cache — atomic JSON with TTL and file locking."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class DiscoveryCache:
    """Atomic read/write cache for OpenAlex resolution results.

    Stores {doi → {value dict, expires_at}} in a 0o600 JSON file.
    Uses LOCK_EX | LOCK_NB on write; on EAGAIN logs WARNING and continues
    (discovery still returns results without caching).
    """

    def __init__(self, cache_path: Path) -> None:
        self._path = Path(cache_path)

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """Return cached value for key, or None if missing/expired."""
        data = self._read()
        entry = data.get(key)
        if entry is None:
            return None
        try:
            expires = datetime.fromisoformat(entry["expires_at"])
        except (KeyError, ValueError):
            return None
        if datetime.now(timezone.utc) >= expires:
            return None
        return entry.get("value")

    def set(self, key: str, value: dict[str, Any], ttl_hours: int) -> None:
        """Store value under key with TTL. No-op on lock contention."""
        data = self._read()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        data = self._prune(data)
        data[key] = {
            "value": value,
            "expires_at": expires_at.isoformat(),
        }
        self._write_atomic(data)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _prune(self, data: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            k: v for k, v in data.items()
            if datetime.fromisoformat(v.get("expires_at", "1970-01-01T00:00:00+00:00")) > now
        }

    def _write_atomic(self, data: dict[str, Any]) -> None:
        """Write atomically via temp file; 0o600 permissions."""
        tmp_path = self._path.with_suffix(".json.tmp")
        lock_path = self._path.with_suffix(".json.lock")
        lock_fd: Optional[int] = None

        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logger.warning("DiscoveryCache: lock contention; skipping cache write")
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
                return
        except OSError as e:
            logger.warning(f"DiscoveryCache: could not open lock file: {e}")
            return

        try:
            content = json.dumps(data, indent=2, default=str).encode("utf-8")
            fd = os.open(
                str(tmp_path),
                os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                0o600,
            )
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
            os.replace(str(tmp_path), str(self._path))
            # Set permissions on the final file (os.replace may inherit tmp perms)
            os.chmod(str(self._path), 0o600)
        except OSError as e:
            logger.warning(f"DiscoveryCache: write failed: {e}")
            try:
                os.unlink(str(tmp_path))
            except OSError:
                pass
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except OSError:
                pass
