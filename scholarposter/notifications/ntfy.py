"""ntfy.sh push notification backend."""
from __future__ import annotations

import httpx
from loguru import logger
from scholarposter.notifications.base import BaseNotifier


class NtfyNotifier(BaseNotifier):
    def __init__(self, topic: str, server: str = "https://ntfy.sh"):
        self._topic = topic
        self._server = server

    def notify(self, platform: str, toot_id: str, error: str) -> None:
        url = f"{self._server}/{self._topic}"
        message = self.format_message(platform, toot_id, error)
        try:
            httpx.post(
                url,
                content=message.encode(),
                headers={
                    "Title": "scholarposter failure",
                    "Priority": "high",
                    "Tags": "warning,scholarposter",
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"ntfy notification failed: {e}")
