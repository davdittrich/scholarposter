"""Base notification ABC."""
from __future__ import annotations
from abc import ABC, abstractmethod


class BaseNotifier(ABC):
    @abstractmethod
    def notify(self, platform: str, toot_id: str, error: str) -> None:
        """Send a failure notification."""
