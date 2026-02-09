"""Standard interface for search backends used by the orchestrator."""

from abc import ABC, abstractmethod
from typing import Any

from src.orchestrators.search.models import SearchResult


class SearchTool(ABC):
    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        pass

    @abstractmethod
    def get_source_name(self) -> str:
        pass

    @abstractmethod
    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        pass
