"""Unified search result and response models for Universal Search."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SearchResultItem(BaseModel):
    """One item in the structured search response (JSON-serializable)."""

    source: str = Field(description="Backend name: web, email, etc.")
    title: str = Field(default="", description="Display title")
    content: str = Field(default="", description="Snippet or main text")
    timestamp: str | None = Field(default=None, description="ISO8601 or null")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Source-specific (url, email_id, from, to, ...)")
    relevance_score: float = Field(default=0.0, ge=0, le=1, description="0-1 relevance")


class SearchResult:
    """Internal unified result from a backend (before serialization)."""

    __slots__ = ("content", "source", "title", "timestamp", "metadata", "relevance_score")

    def __init__(
        self,
        content: str,
        source: str,
        title: str = "",
        timestamp: datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
        relevance_score: float = 0.0,
    ):
        self.content = content
        self.source = source
        self.title = title
        if isinstance(timestamp, datetime):
            self.timestamp = timestamp.isoformat()
        else:
            self.timestamp = timestamp
        self.metadata = metadata or {}
        self.relevance_score = relevance_score

    def to_item(self) -> SearchResultItem:
        ts = self.timestamp
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat() if ts else None
        return SearchResultItem(
            source=self.source,
            title=self.title,
            content=self.content,
            timestamp=ts,
            metadata=self.metadata,
            relevance_score=self.relevance_score,
        )


class UniversalSearchResponse(BaseModel):
    """Structured JSON returned by the universal_search tool."""

    results: list[SearchResultItem] = Field(default_factory=list, description="Ordered search results")
    errors: list[str] = Field(default_factory=list, description="Partial failures (e.g. one source failed)")
    meta: dict[str, Any] = Field(
        default_factory=lambda: {
            "query": "",
            "sources_queried": [],
            "iterations": 0,
            "total_results": 0,
        },
        description="Query, sources used, iteration count, total count",
    )
