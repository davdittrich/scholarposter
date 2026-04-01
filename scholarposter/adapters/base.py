"""Base adapter ABC for scholarposter platform adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod

from scholarposter.models import PostResult, UnifiedPost


class BaseAdapter(ABC):
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the platform identifier string."""

    @abstractmethod
    def post(self, unified_post: UnifiedPost, dry_run: bool = False) -> PostResult:
        """Post to the platform. Returns PostResult."""
