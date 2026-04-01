"""ntfy.sh push notification backend."""
from __future__ import annotations

from datetime import datetime, timezone
import httpx
from loguru import logger
from scholarposter.notifications.base import BaseNotifier


class NtfyNotifier(BaseNotifier):
    def __init__(self, topic: str, server: str = "https://ntfy.sh"):
        self._topic = topic
        self._server = server

    def notify(self, platform: str, toot_id: str, error: str) -> None:
        url = f"{self._server}/{self._topic}"
        try:
            httpx.post(
                url,
                content=f"[{datetime.now(timezone.utc).isoformat()}] Cross-post to {platform} failed for toot {toot_id}: {error}".encode(),
                headers={
                    "Title": "scholarposter failure",
                    "Priority": "high",
                    "Tags": "warning,scholarposter",
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"ntfy notification failed: {e}")
