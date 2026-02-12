"""Deterministic retrieval router: selects sources and methods based on intent + capabilities.

This replaces the old keyword-matching can_handle_query pattern with
capability-driven routing that respects what each server actually supports.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.contracts.mcp_search_v1 import RetrievalMethod, SourceClass
from src.orchestrators.search.capabilities import CapabilityRegistry

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """A decision to search a specific source with specific methods and filters."""

    source: str
    methods: list[str]
    query: str
    filters: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RoutingPlan:
    """Complete routing plan for a query."""

    decisions: list[RoutingDecision]
    complexity: str  # "simple" or "complex"
    reasoning: str = ""


# Source hint keywords -> source names
_SOURCE_HINTS: dict[str, list[str]] = {
    "email": ["email"],
    "mail": ["email"],
    "inbox": ["email"],
    "sent": ["email"],
    "browser": ["browser_history", "browser_bookmarks"],
    "history": ["browser_history"],
    "visited": ["browser_history"],
    "bookmark": ["browser_bookmarks"],
    "saved": ["browser_bookmarks"],
    "calendar": ["calendar"],
    "event": ["calendar"],
    "meeting": ["calendar"],
    "schedule": ["calendar"],
    "task": ["tasks"],
    "todo": ["tasks"],
    "reminder": ["tasks"],
    "web": ["web"],
    "search": ["web"],
    "news": ["web"],
    "latest": ["web"],
}

# Filter-related patterns
_FILTER_PATTERNS: dict[str, re.Pattern] = {
    "from_email": re.compile(r"\bfrom\s+(\S+@\S+)", re.IGNORECASE),
    "domain": re.compile(r"\b(?:on|from|at)\s+([\w.-]+\.(?:com|org|net|io|dev|co|ai))\b", re.IGNORECASE),
    "date_after": re.compile(r"\b(?:after|since|from)\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE),
    "date_before": re.compile(r"\b(?:before|until|by)\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE),
}

# Temporal keywords that suggest structured search
_TEMPORAL_KEYWORDS = {"today", "yesterday", "this week", "last week", "this month", "last month", "recent", "recently"}

# Relationship keywords that suggest complex queries
_RELATIONSHIP_KEYWORDS = {"between", "related to", "about the same", "thread", "conversation", "regarding"}


class RetrievalRouter:
    """Routes queries to appropriate sources and methods based on capabilities."""

    def __init__(self, capabilities: CapabilityRegistry) -> None:
        self._capabilities = capabilities

    def route(self, intent: dict[str, Any], query: str) -> RoutingPlan:
        """Build a routing plan from intent analysis and query text.

        Args:
            intent: Structured intent from LLM analysis (entities, temporal, source_hints, complexity, etc.)
            query: Raw user query text.

        Returns:
            RoutingPlan with source/method decisions and complexity classification.
        """
        # 1. Determine complexity
        complexity = self._classify_complexity(intent, query)

        # 2. Select target sources
        target_sources = self._select_sources(intent, query)

        # 3. Extract filters from intent
        filters = self._extract_filters(intent, query)

        # 4. Select methods per source
        decisions: list[RoutingDecision] = []
        for source in target_sources:
            methods = self._select_methods(source, query, filters, intent)
            # Only include filters this source actually supports
            source_filters = self._filter_for_source(source, filters)
            decisions.append(RoutingDecision(
                source=source,
                methods=methods,
                query=query,
                filters=source_filters,
            ))

        reasoning = (
            f"Routed to {len(decisions)} source(s): "
            + ", ".join(f"{d.source}[{','.join(d.methods)}]" for d in decisions)
            + f" | complexity={complexity}"
        )
        logger.info("Router: %s", reasoning)

        return RoutingPlan(
            decisions=decisions,
            complexity=complexity,
            reasoning=reasoning,
        )

    def _classify_complexity(self, intent: dict[str, Any], query: str) -> str:
        """Classify query as simple or complex."""
        # Explicit complexity from intent
        if intent.get("complexity") == "multi_hop":
            return "complex"

        # Multiple source hints -> complex
        hints = intent.get("source_hints") or []
        if len(hints) > 2:
            return "complex"

        # Relationship keywords -> complex
        query_lower = query.lower()
        if any(kw in query_lower for kw in _RELATIONSHIP_KEYWORDS):
            return "complex"

        # Multiple entities with cross-reference -> complex
        entities = intent.get("entities") or []
        if len(entities) > 3:
            return "complex"

        return "simple"

    def _select_sources(self, intent: dict[str, Any], query: str) -> list[str]:
        """Determine which sources to query."""
        available = set(self._capabilities.all_sources())
        if not available:
            return []

        # Check for explicit source hints in intent
        hints = intent.get("source_hints") or []
        if hints:
            target = set()
            for hint in hints:
                hint_lower = str(hint).lower().strip()
                for keyword, sources in _SOURCE_HINTS.items():
                    if keyword in hint_lower:
                        target.update(s for s in sources if s in available)
            if target:
                return sorted(target)

        # Check for source keywords in query
        query_lower = query.lower()
        target = set()
        for keyword, sources in _SOURCE_HINTS.items():
            if keyword in query_lower:
                target.update(s for s in sources if s in available)
        if target:
            return sorted(target)

        # Default: query all personal sources (skip web unless explicitly requested)
        personal = self._capabilities.personal_sources()
        return sorted(personal) if personal else sorted(available)

    def _extract_filters(self, intent: dict[str, Any], query: str) -> list[dict[str, Any]]:
        """Extract filter clauses from intent and query patterns."""
        filters: list[dict[str, Any]] = []

        # From intent entities with roles
        entities = intent.get("entities") or []
        for entity in entities:
            if isinstance(entity, dict):
                role = entity.get("role", "")
                name = entity.get("name", "")
                if role == "sender" and name:
                    filters.append({"field": "from_email", "operator": "contains", "value": name})
                elif role == "recipient" and name:
                    filters.append({"field": "to_email", "operator": "contains", "value": name})

        # From intent temporal
        temporal = intent.get("temporal")
        if temporal:
            temporal_str = str(temporal).lower().strip()
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            from src.core.config import config

            tz_name = config.user_timezone or "UTC"
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
            now = datetime.now(tz)
            if temporal_str in ("today",):
                filters.append({"field": "date_after", "operator": "gte", "value": now.date().isoformat()})
            elif temporal_str in ("yesterday",):
                yesterday = (now - timedelta(days=1)).date()
                filters.append({"field": "date_after", "operator": "gte", "value": yesterday.isoformat()})
                filters.append({"field": "date_before", "operator": "lte", "value": yesterday.isoformat()})
            elif temporal_str in ("this week", "last week"):
                days = 7 if temporal_str == "this week" else 14
                filters.append({"field": "date_after", "operator": "gte", "value": (now - timedelta(days=days)).date().isoformat()})
            elif temporal_str in ("this month", "last month"):
                days = 30 if temporal_str == "this month" else 60
                filters.append({"field": "date_after", "operator": "gte", "value": (now - timedelta(days=days)).date().isoformat()})
            elif temporal_str in ("recent", "recently"):
                filters.append({"field": "date_after", "operator": "gte", "value": (now - timedelta(days=30)).date().isoformat()})

        # From regex patterns in query
        for field_name, pattern in _FILTER_PATTERNS.items():
            match = pattern.search(query)
            if match:
                value = match.group(1)
                op = "gte" if "after" in field_name else ("lte" if "before" in field_name else "contains")
                # Don't duplicate filters
                if not any(f["field"] == field_name for f in filters):
                    filters.append({"field": field_name, "operator": op, "value": value})

        return filters

    def _select_methods(
        self,
        source: str,
        query: str,
        filters: list[dict[str, Any]],
        intent: dict[str, Any],
    ) -> list[str]:
        """Select retrieval methods for a specific source."""
        methods = []
        caps = self._capabilities.get(source)
        if not caps:
            return ["vector"]  # safe fallback

        supported = [str(m) for m in caps.supported_methods]
        has_filters = bool(filters)
        has_query = bool(query and query.strip())

        # Structured-first: always include structured when we have filters
        if has_filters and "structured" in supported:
            methods.append("structured")

        # Fulltext: when we have a text query with concrete keywords
        if has_query and "fulltext" in supported:
            methods.append("fulltext")

        # Vector: when we have a semantic/conceptual query
        if has_query and "vector" in supported:
            methods.append("vector")

        # Fallback: if nothing selected, use whatever is available
        if not methods:
            if has_query and "vector" in supported:
                methods = ["vector"]
            elif "structured" in supported:
                methods = ["structured"]
            elif supported:
                methods = [supported[0]]

        return methods

    def _filter_for_source(self, source: str, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep only filters that this source actually supports."""
        caps = self._capabilities.get(source)
        if not caps:
            return []
        supported_names = {f.name for f in caps.supported_filters}
        return [f for f in filters if f["field"] in supported_names]
