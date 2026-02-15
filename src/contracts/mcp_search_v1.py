"""MCP Search Contract v1.

Defines the canonical types for:
  - Capability discovery (SearchCapabilities, FilterSpec)
  - Unified search request (UnifiedSearchRequest, FilterClause)
  - Standardized result payload (SearchResultV1, UnifiedSearchResponse)

MCP servers implement search_capabilities() and unified_search().
The agent reads capabilities at bootstrap and routes queries accordingly.

Contract v1.2: Added capability intelligence hints (aliases, freshness, tiers).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Retrieval methods
# ---------------------------------------------------------------------------


class RetrievalMethod(StrEnum):
    STRUCTURED = "structured"
    FULLTEXT = "fulltext"
    VECTOR = "vector"
    GRAPH = "graph"  # reserved for Phase 2


# ---------------------------------------------------------------------------
# Search modes
# ---------------------------------------------------------------------------


class SearchMode(StrEnum):
    """Operation mode for unified_search."""

    SEARCH = "search"  # Return ranked document list (default)
    COUNT = "count"  # Return total matching count only
    AGGREGATE = "aggregate"  # Group by field, return top groups with counts


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


class SourceClass(StrEnum):
    PERSONAL = "personal"
    WEB = "web"


class CapabilityTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EntityValueParser(StrEnum):
    STRING = "string"
    EMAIL_FROM_HEADER = "email_from_header"


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


class EntityExtractionRule(BaseModel):
    """Metadata-to-filter extraction rule for cross-step entity propagation."""

    target_field: str = Field(
        description="Filter field produced from metadata, e.g. from_name"
    )
    metadata_key: str = Field(
        description="Metadata key to read from search result metadata"
    )
    parser: EntityValueParser = Field(
        default=EntityValueParser.STRING,
        description="How to parse metadata value: string | email_from_header",
    )


class SearchCapabilities(BaseModel):
    """Returned by each MCP server's search_capabilities tool."""

    schema_version: str = Field(default="1.2")
    source_name: str = Field(
        description="Canonical source identifier, e.g. 'email', 'browser_history'"
    )
    source_class: SourceClass = Field(default=SourceClass.PERSONAL)
    supported_methods: list[RetrievalMethod] = Field(
        description="Which retrieval methods this server supports"
    )
    supported_filters: list[FilterSpec] = Field(default_factory=list)
    supported_modes: list[str] = Field(
        default_factory=lambda: ["search"],
        description="Supported modes: search, count, aggregate",
    )
    supported_group_by_fields: list[str] = Field(
        default_factory=list,
        description="Fields available for aggregate group_by (e.g. from_email, contact_id)",
    )
    max_limit: int = Field(default=50)
    default_limit: int = Field(default=10)
    sort_fields: list[str] = Field(default_factory=list)
    default_ranking: str = Field(default="vector")
    display_label: str | None = Field(
        default=None,
        description="Short user-facing label for the agent, e.g. 'WhatsApp messages'. If absent, a fallback is derived from source_name.",
    )
    alias_hints: list[str] = Field(
        default_factory=list,
        description="Optional source aliases/synonyms used for source matching (e.g. ['wa', 'gmail']).",
    )
    freshness_window_days: int | None = Field(
        default=None,
        ge=1,
        description="Optional freshness hint for this source; lower values imply faster staleness.",
    )
    latency_tier: CapabilityTier = Field(
        description="Expected latency profile for planning/routing: low|medium|high."
    )
    quality_tier: CapabilityTier = Field(
        description="Expected answer quality profile for planning/routing: low|medium|high."
    )
    cost_tier: CapabilityTier = Field(
        description="Expected compute/usage cost profile for planning/routing: low|medium|high."
    )
    request_routing_args: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional source-specific unified_search args for shared MCP endpoints.",
    )
    entity_extraction_rules: list[EntityExtractionRule] = Field(
        default_factory=list,
        description="Optional metadata parsing rules used for multi-hop entity extraction.",
    )

    @field_validator("alias_hints")
    @classmethod
    def _validate_alias_hints(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            alias = str(raw).strip().lower()
            if not alias:
                raise ValueError("alias_hints must not contain empty values")
            if alias in seen:
                continue
            seen.add(alias)
            normalized.append(alias)
        return normalized


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
    mode: str = Field(
        default="search",
        description="search | count | aggregate. count returns total_available only; aggregate uses group_by.",
    )
    sort_field: str | None = Field(
        default=None,
        description="Sort field (must be in server's sort_fields). Overrides default ranking.",
    )
    sort_order: str = Field(
        default="desc",
        description="asc | desc. Used when sort_field is set.",
    )
    group_by: str | None = Field(
        default=None,
        description="Field to group by for aggregate mode (e.g. from_email, contact_push_name).",
    )
    aggregate_top_n: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Max groups to return in aggregate mode.",
    )


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


class AggregateGroup(BaseModel):
    """One group from aggregate mode."""

    group_value: str = Field(description="Value of the group_by field")
    count: int = Field(description="Number of items in this group")
    label: str | None = Field(
        default=None,
        description="Human-readable label (e.g. contact name instead of JID)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra fields (e.g. contact_push_name, from_email)",
    )


class UnifiedSearchResponse(BaseModel):
    """Returned by each MCP server's unified_search tool."""

    results: list[SearchResultV1] = Field(default_factory=list)
    total_available: int | None = Field(
        default=None,
        description="True total matching count. In search mode may be len(results) if unknown.",
    )
    mode: str = Field(
        default="search",
        description="Mode that was executed: search, count, or aggregate.",
    )
    count: int | None = Field(
        default=None,
        description="For count mode: total matching documents.",
    )
    aggregates: list[AggregateGroup] = Field(
        default_factory=list,
        description="For aggregate mode: top groups with counts.",
    )
    methods_executed: list[str] = Field(default_factory=list)
    timing_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Per-method execution time in ms",
    )
    error: str | None = Field(default=None)
