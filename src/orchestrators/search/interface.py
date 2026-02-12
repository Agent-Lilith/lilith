"""Standard interface for search backends used by the orchestrator.

All backends (MCP-based and direct API) implement SearchBackend and return SearchResultV1.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass


class SearchBackend(ABC):
    """Base class for all search backends."""

    @abstractmethod
    async def search(
        self,
        query: str,
        methods: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        top_k: int = 10,
    ) -> list[SearchResultV1]:
        """Execute search and return standardized results."""

    @abstractmethod
    def get_source_name(self) -> str:
        """Canonical source identifier."""

    @abstractmethod
    def get_source_class(self) -> SourceClass:
        """Whether this is a personal or web source."""

    @abstractmethod
    def get_supported_methods(self) -> list[str]:
        """Which retrieval methods this backend supports."""

    @abstractmethod
    def get_supported_filters(self) -> list[dict[str, Any]]:
        """Filter specifications this backend supports."""
