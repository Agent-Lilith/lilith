"""MCP search contract v1: shared types for capability discovery, unified search, and result payloads."""

from src.contracts.mcp_search_v1 import (
    FilterClause,
    FilterSpec,
    SearchCapabilities,
    SearchResultV1,
    UnifiedSearchRequest,
    UnifiedSearchResponse,
)

__all__ = [
    "FilterClause",
    "FilterSpec",
    "SearchCapabilities",
    "SearchResultV1",
    "UnifiedSearchRequest",
    "UnifiedSearchResponse",
]
