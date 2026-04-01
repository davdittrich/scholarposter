"""Email notification backend via SMTP."""
from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from loguru import logger

from scholarposter.notifications.base import BaseNotifier


class EmailNotifier(BaseNotifier):
    def __init__(self, smtp_host: str, smtp_port: int, from_addr: str, to_addr: str):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._from_addr = from_addr
        self._to_addr = to_addr

    def notify(self, platform: str, toot_id: str, error: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"scholarposter failure: {platform}"
        msg["From"] = self._from_addr
        msg["To"] = self._to_addr
        msg.set_content(
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"Cross-post to {platform} failed for toot {toot_id}: {error}"
        )
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                server.ehlo()
                if self._smtp_port == 587:
                    server.starttls()
                user = os.environ.get("SMTP_USER")
                password = os.environ.get("SMTP_PASSWORD")
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
        except Exception as e:
            logger.warning(f"Email notification failed: {e}")
