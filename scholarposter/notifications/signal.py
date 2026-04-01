"""Signal notification backend via signal-cli REST API."""
from __future__ import annotations

import httpx
from loguru import logger

from scholarposter.notifications.base import BaseNotifier


class SignalNotifier(BaseNotifier):
    def __init__(self, api_url: str, phone_number: str, recipients: list[str]):
        self._api_url = api_url.rstrip("/")
        self._phone_number = phone_number
        self._recipients = recipients

    def notify(self, platform: str, toot_id: str, error: str) -> None:
        message = self.format_message(platform, toot_id, error)
        try:
            httpx.post(
                f"{self._api_url}/v2/send",
                json={
                    "message": message,
                    "number": self._phone_number,
                    "recipients": self._recipients,
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Signal notification failed: {e}")
