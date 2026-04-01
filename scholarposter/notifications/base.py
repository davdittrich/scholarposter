"""Base notification ABC."""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class BaseNotifier(ABC):
    def format_message(self, platform: str, toot_id: str, error: str) -> str:
        """Return a consistently formatted failure message."""
        return (
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"Cross-post to {platform} failed for toot {toot_id}: {error}"
        )

    @abstractmethod
    def notify(self, platform: str, toot_id: str, error: str) -> None:
        """Send a failure notification."""
