"""Universal Search: orchestrator and backends for web, email, and future sources."""

from src.orchestrators.search.models import SearchResult, UniversalSearchResponse, SearchResultItem
from src.orchestrators.search.interface import SearchTool
from src.orchestrators.search.orchestrator import UniversalSearchOrchestrator

__all__ = [
    "SearchResult",
    "SearchResultItem",
    "SearchTool",
    "UniversalSearchOrchestrator",
    "UniversalSearchResponse",
]
