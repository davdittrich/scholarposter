"""Email notification backend via SMTP."""
from __future__ import annotations

import os
import smtplib
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
        msg.set_content(self.format_message(platform, toot_id, error))
        try:
            if self._smtp_port == 465:
                with smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, timeout=10) as server:
                    self._authenticate_and_send(server, msg)
            else:
                with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                    server.ehlo()
                    if server.has_extn("starttls"):
                        server.starttls()
                        server.ehlo()  # re-ehlo after STARTTLS per SMTP spec
                    self._authenticate_and_send(server, msg)
        except Exception as e:
            logger.warning(f"Email notification failed: {e}")

    def _authenticate_and_send(self, server, msg: EmailMessage) -> None:
        user = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_PASSWORD")
        if user and password:
            server.login(user, password)
        server.send_message(msg)
