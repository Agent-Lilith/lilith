"""Unified search result and response models for the search pipeline.

Uses SearchResultV1 from the MCP contract as the canonical result type.
"""

from typing import Any

from pydantic import BaseModel, Field

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass


class UniversalSearchResponse(BaseModel):
    """Final response from the search orchestrator."""

    results: list[SearchResultV1] = Field(default_factory=list, description="Ordered search results")
    errors: list[str] = Field(default_factory=list, description="Partial failures")
    notes: list[str] = Field(default_factory=list, description="Human-readable notes about the search (e.g. temporal filter status)")
    meta: dict[str, Any] = Field(
        default_factory=lambda: {
            "query": "",
            "sources_queried": [],
            "methods_used": [],
            "iterations": 0,
            "total_results": 0,
            "complexity": "simple",
            "timing_ms": {},
        },
        description="Pipeline metadata: query, sources, methods, iterations, timing",
    )
