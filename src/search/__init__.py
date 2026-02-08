"""Universal Search: orchestrator and backends for web, email, and future sources."""

from src.search.models import SearchResult, UniversalSearchResponse, SearchResultItem
from src.search.interface import SearchTool
from src.search.orchestrator import UniversalSearchOrchestrator

__all__ = [
    "SearchResult",
    "SearchResultItem",
    "UniversalSearchResponse",
    "SearchTool",
    "UniversalSearchOrchestrator",
]
