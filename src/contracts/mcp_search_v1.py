"""MCP Search Contract v1.

Defines the canonical types for:
  - Capability discovery (SearchCapabilities, FilterSpec)
  - Unified search request (UnifiedSearchRequest, FilterClause)
  - Standardized result payload (SearchResultV1, UnifiedSearchResponse)

MCP servers implement search_capabilities() and unified_search().
The agent reads capabilities at bootstrap and routes queries accordingly.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Retrieval methods
# ---------------------------------------------------------------------------


class RetrievalMethod(StrEnum):
    STRUCTURED = "structured"
    FULLTEXT = "fulltext"
    VECTOR = "vector"
    GRAPH = "graph"  # reserved for Phase 2


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


class SourceClass(StrEnum):
    PERSONAL = "personal"
    WEB = "web"


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


class FilterSpec(BaseModel):
    """Describes one filterable field a server supports."""

    name: str = Field(description="Filter field name, e.g. 'from_email', 'date_after'")
    type: str = Field(
        description="Data type: 'string', 'date', 'boolean', 'integer', 'string[]'"
    )
    operators: list[str] = Field(
        description="Supported operators: 'eq', 'contains', 'gte', 'lte', 'in'"
    )
    description: str = Field(default="")


class SearchCapabilities(BaseModel):
    """Returned by each MCP server's search_capabilities tool."""

    schema_version: str = Field(default="1.0")
    source_name: str = Field(
        description="Canonical source identifier, e.g. 'email', 'browser_history'"
    )
    source_class: SourceClass = Field(default=SourceClass.PERSONAL)
    supported_methods: list[RetrievalMethod] = Field(
        description="Which retrieval methods this server supports"
    )
    supported_filters: list[FilterSpec] = Field(default_factory=list)
    max_limit: int = Field(default=50)
    default_limit: int = Field(default=10)
    sort_fields: list[str] = Field(default_factory=list)
    default_ranking: str = Field(default="vector")


# ---------------------------------------------------------------------------
# Unified search request
# ---------------------------------------------------------------------------


class FilterClause(BaseModel):
    """A single filter condition sent to a server."""

    field: str
    operator: str  # "eq", "contains", "gte", "lte", "in"
    value: Any


class UnifiedSearchRequest(BaseModel):
    """Accepted by each MCP server's unified_search tool."""

    query: str = Field(
        default="", description="Semantic or keyword query (empty for structured-only)"
    )
    methods: list[RetrievalMethod] | None = Field(
        default=None,
        description="Retrieval methods to use. None = server auto-selects.",
    )
    filters: list[FilterClause] | None = Field(default=None)
    top_k: int = Field(default=10, ge=1, le=100)
    include_scores: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Standardized result payload
# ---------------------------------------------------------------------------


class SearchResultV1(BaseModel):
    """One search result, with per-method scores and provenance."""

    id: str = Field(description="Unique result identifier within source")
    source: str = Field(description="Source name matching capabilities source_name")
    source_class: SourceClass = Field(default=SourceClass.PERSONAL)
    title: str = Field(default="")
    snippet: str = Field(default="")
    timestamp: str | None = Field(default=None, description="ISO 8601")
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="Per-method scores, e.g. {'structured': 0.9, 'fulltext': 0.7, 'vector': 0.85}",
    )
    methods_used: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific metadata (url, email_id, thread_id, from, to, domain, etc.)",
    )
    provenance: str | None = Field(
        default=None,
        description="Human-readable origin, e.g. 'email from john@example.com on 2026-01-15'",
    )

    @property
    def final_score(self) -> float:
        """Weighted aggregate score across all methods."""
        if not self.scores:
            return 0.0
        weights: dict[str, float] = {
            RetrievalMethod.STRUCTURED.value: 1.0,
            RetrievalMethod.FULLTEXT.value: 0.85,
            RetrievalMethod.VECTOR.value: 0.7,
            RetrievalMethod.GRAPH.value: 0.9,
        }
        total_weight = 0.0
        total_score = 0.0
        for method, score in self.scores.items():
            w = weights.get(method, 0.5)
            total_weight += w
            total_score += score * w
        return total_score / total_weight if total_weight > 0 else 0.0


class UnifiedSearchResponse(BaseModel):
    """Returned by each MCP server's unified_search tool."""

    results: list[SearchResultV1] = Field(default_factory=list)
    total_available: int | None = Field(default=None)
    methods_executed: list[str] = Field(default_factory=list)
    timing_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Per-method execution time in ms",
    )
    error: str | None = Field(default=None)
