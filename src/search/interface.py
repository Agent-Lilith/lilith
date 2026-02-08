"""Standard interface for search backends used by the orchestrator."""

from abc import ABC, abstractmethod
from typing import Any

from src.search.models import SearchResult


class SearchTool(ABC):
    """Base for all search backends (web, email, future git/files)."""

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Execute search and return unified results."""
        pass

    @abstractmethod
    def get_source_name(self) -> str:
        """Return backend name (e.g. 'web', 'email')."""
        pass

    @abstractmethod
    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        """Return confidence in [0, 1] that this backend is relevant for the query."""
        pass
