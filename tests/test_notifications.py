"""Tests for scholarposter.notifications"""
import pytest
import respx
import httpx
from unittest.mock import patch, MagicMock
from scholarposter.notifications.ntfy import NtfyNotifier
from scholarposter.notifications.signal import SignalNotifier
from scholarposter.notifications.email import EmailNotifier


class TestNtfyNotifier:
    @respx.mock
    def test_sends_notification(self):
        respx.post("https://ntfy.sh/test-topic").mock(
            return_value=httpx.Response(200)
        )
        notifier = NtfyNotifier(topic="test-topic")
        notifier.notify("bluesky", "113456789", "API error")
        assert respx.calls.call_count == 1

    @respx.mock
    def test_timestamp_in_notification_body(self):
        route = respx.post("https://ntfy.sh/test-topic").mock(
            return_value=httpx.Response(200)
        )
        notifier = NtfyNotifier(topic="test-topic")
        notifier.notify("bluesky", "113456789", "API error")
        body = respx.calls.last.request.content.decode()
        # FR-45: must contain ISO timestamp
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", body), (
            f"No ISO timestamp found in notification body: {body!r}"
        )
        assert "bluesky" in body
        assert "113456789" in body

    @respx.mock
    def test_failure_does_not_raise(self):
        respx.post("https://ntfy.sh/test-topic").mock(
            side_effect=httpx.ConnectError("refused")
        )
        notifier = NtfyNotifier(topic="test-topic")
        # Should not raise
        notifier.notify("bluesky", "123", "error")

    def test_custom_server(self):
        notifier = NtfyNotifier(topic="mytopic", server="https://ntfy.example.com")
        assert notifier._server == "https://ntfy.example.com"


class TestSignalNotifier:
    @respx.mock
    def test_sends_notification(self):
        respx.post("http://localhost:8080/v2/send").mock(
            return_value=httpx.Response(200)
        )
        notifier = SignalNotifier(
            api_url="http://localhost:8080",
            phone_number="+1234567890",
            recipients=["+0987654321"],
        )
        notifier.notify("bluesky", "113456789", "API error")
        assert respx.calls.call_count == 1
        import json
        body = json.loads(respx.calls.last.request.content.decode())
        assert "bluesky" in body["message"]
        assert body["number"] == "+1234567890"
        assert body["recipients"] == ["+0987654321"]

    @respx.mock
    def test_failure_does_not_raise(self):
        respx.post("http://localhost:8080/v2/send").mock(
            side_effect=httpx.ConnectError("refused")
        )
        notifier = SignalNotifier(
            api_url="http://localhost:8080",
            phone_number="+1234567890",
            recipients=["+0987654321"],
        )
        # Should not raise
        notifier.notify("bluesky", "123", "error")

    @respx.mock
    def test_timestamp_in_message(self):
        respx.post("http://localhost:8080/v2/send").mock(
            return_value=httpx.Response(200)
        )
        notifier = SignalNotifier(
            api_url="http://localhost:8080",
            phone_number="+1234567890",
            recipients=["+0987654321"],
        )
        notifier.notify("bluesky", "123", "error")
        import json, re
        body = json.loads(respx.calls.last.request.content.decode())
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", body["message"])


class TestEmailNotifier:
    def test_sends_message(self):
        with patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_server.has_extn.return_value = False
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(
                smtp_host="smtp.test", smtp_port=587,
                from_addr="a@b.com", to_addr="c@d.com",
            )
            notifier.notify("bluesky", "123", "API error")
            mock_server.send_message.assert_called_once()

    def test_failure_does_not_raise(self):
        with patch("scholarposter.notifications.email.smtplib.SMTP",
                   side_effect=ConnectionRefusedError("refused")):
            notifier = EmailNotifier(
                smtp_host="smtp.test", smtp_port=587,
                from_addr="a@b.com", to_addr="c@d.com",
            )
            # Should not raise
            notifier.notify("bluesky", "123", "error")

    def test_port_465_uses_smtp_ssl(self):
        """Port 465 must use SMTP_SSL, not plain SMTP."""
        with patch("scholarposter.notifications.email.smtplib.SMTP_SSL") as mock_ssl_cls, \
             patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_ssl_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(
                smtp_host="smtp.test", smtp_port=465,
                from_addr="a@b.com", to_addr="c@d.com",
            )
            notifier.notify("bluesky", "123", "error")
            mock_ssl_cls.assert_called_once_with("smtp.test", 465, timeout=10)
            mock_smtp_cls.assert_not_called()
            mock_server.send_message.assert_called_once()

    def test_port_587_uses_smtp_with_starttls_when_advertised(self):
        """Port 587 with STARTTLS advertised: use SMTP, call starttls()."""
        with patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_server.has_extn.return_value = True
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(
                smtp_host="smtp.test", smtp_port=587,
                from_addr="a@b.com", to_addr="c@d.com",
            )
            notifier.notify("bluesky", "123", "error")
            mock_server.has_extn.assert_called_with("starttls")
            mock_server.starttls.assert_called_once()
            mock_server.send_message.assert_called_once()

    def test_port_25_skips_starttls_when_not_advertised(self):
        """Port 25 without STARTTLS advertised: use SMTP, skip starttls()."""
        with patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_server.has_extn.return_value = False
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(
                smtp_host="smtp.test", smtp_port=25,
                from_addr="a@b.com", to_addr="c@d.com",
            )
            notifier.notify("bluesky", "123", "error")
            mock_server.has_extn.assert_called_with("starttls")
            mock_server.starttls.assert_not_called()
            mock_server.send_message.assert_called_once()

    def test_ehlo_called_twice_on_starttls_path(self):
        """ehlo() must be called before and after starttls() per SMTP spec."""
        with patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_server.has_extn.return_value = True
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(
                smtp_host="smtp.test", smtp_port=587,
                from_addr="a@b.com", to_addr="c@d.com",
            )
            notifier.notify("bluesky", "123", "error")
            assert mock_server.ehlo.call_count == 2, (
                f"Expected ehlo() called twice (before+after STARTTLS), "
                f"got {mock_server.ehlo.call_count}"
            )
