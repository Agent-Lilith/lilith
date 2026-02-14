"""Deterministic retrieval router built from runtime capabilities.

The router avoids source-specific keyword maps and derives source matching from
registered capability metadata (source_name/display_label/filter names).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.core.config import config
from src.orchestrators.search.capabilities import CapabilityRegistry

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """A decision to search a specific source with specific methods and filters."""

    source: str
    methods: list[str]
    query: str
    filters: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "search"  # search | count | aggregate
    sort_field: str | None = None
    sort_order: str = "desc"
    group_by: str | None = None
    aggregate_top_n: int = 10


@dataclass
class RoutingPlan:
    """Complete routing plan for a query."""

    decisions: list[RoutingDecision]
    complexity: str  # "simple" or "complex"
    reasoning: str = ""
    used_default_sources: bool = False


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


class RetrievalRouter:
    """Routes queries to appropriate sources and methods based on capabilities."""

    def __init__(self, capabilities: CapabilityRegistry) -> None:
        self._capabilities = capabilities

    def infer_fast_path_intent(self, query: str) -> dict[str, Any] | None:
        """Build a lightweight intent when source hints are obvious from runtime capabilities."""
        text = (query or "").strip()
        if not text or len(text) > 200:
            return None

        hints = self._match_sources_from_text(text)
        if not hints:
            return None

        ordered_hints = self._order_sources_by_mention(text, hints)
        temporal = self._extract_temporal_from_text(text)
        retrieval_plan = self._build_fast_path_retrieval_plan(
            text=text,
            ordered_sources=ordered_hints,
        )

        return {
            "intent": "find_information",
            "entities": [],
            "temporal": temporal,
            "source_hints": ordered_hints,
            "complexity": "multi_hop" if retrieval_plan else "simple",
            "retrieval_plan": retrieval_plan,
        }

    def route(self, intent: dict[str, Any], query: str) -> RoutingPlan:
        """Build a routing plan from intent analysis and query text."""
        complexity = self._classify_complexity(intent)
        target_sources, used_default_sources = self._select_sources(intent, query)
        filters = self._extract_filters(intent)
        mode, group_by, aggregate_top_n = self._extract_mode_and_group_by(
            intent, target_sources
        )

        decisions: list[RoutingDecision] = []
        for source in target_sources:
            methods = self._select_methods(source, query, filters, intent)
            source_filters = self._filter_for_source(source, filters)
            src_group_by = None
            if group_by and self._capabilities.supports_group_by(source, group_by):
                src_group_by = group_by
            decisions.append(
                RoutingDecision(
                    source=source,
                    methods=methods,
                    query=query,
                    filters=source_filters,
                    mode=mode,
                    group_by=src_group_by,
                    aggregate_top_n=aggregate_top_n,
                )
            )

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
            used_default_sources=used_default_sources,
        )

    def decisions_for_sources(
        self,
        sources: list[str],
        query: str,
        intent: dict[str, Any],
        extra_filters: list[dict[str, Any]] | None = None,
    ) -> list[RoutingDecision]:
        """Build routing decisions for an explicit source list. Used by multi-hop step execution."""
        filters = self._extract_filters(intent)
        if extra_filters:
            filters = filters + extra_filters
        mode, group_by, aggregate_top_n = self._extract_mode_and_group_by(
            intent, sources
        )
        available = set(self._capabilities.all_sources())
        decisions: list[RoutingDecision] = []
        for source in sources:
            if source not in available:
                logger.warning("Router: skipping unknown source %s", source)
                continue
            methods = self._select_methods(source, query, filters, intent)
            source_filters = self._filter_for_source(source, filters)
            src_group_by = None
            if group_by and self._capabilities.supports_group_by(source, group_by):
                src_group_by = group_by
            decisions.append(
                RoutingDecision(
                    source=source,
                    methods=methods,
                    query=query,
                    filters=source_filters,
                    mode=mode,
                    group_by=src_group_by,
                    aggregate_top_n=aggregate_top_n,
                )
            )
        return decisions

    def _extract_mode_and_group_by(
        self, intent: dict[str, Any], sources: list[str]
    ) -> tuple[str, str | None, int]:
        """Extract search mode/group_by from intent, then validate against capabilities."""
        mode = str(intent.get("search_mode") or "search").strip().lower()
        if mode not in ("search", "count", "aggregate"):
            mode = "search"

        if mode != "aggregate":
            return mode, None, 10

        top_n = int(intent.get("aggregate_top_n", 10) or 10)
        top_n = min(100, max(1, top_n))
        requested_group_by = str(intent.get("aggregate_group_by") or "").strip()

        if requested_group_by:
            for src in sources:
                if self._capabilities.supports_group_by(src, requested_group_by):
                    return "aggregate", requested_group_by, top_n

        for src in sources:
            caps = self._capabilities.get(src)
            if not caps:
                continue
            fields = getattr(caps, "supported_group_by_fields", None) or []
            if fields:
                return "aggregate", str(fields[0]), top_n

        return "search", None, 10

    def _classify_complexity(self, intent: dict[str, Any]) -> str:
        """Classify query as simple/complex using intent structure."""
        if intent.get("complexity") == "multi_hop":
            return "complex"
        plan = intent.get("retrieval_plan") or []
        if isinstance(plan, list) and len(plan) > 1:
            return "complex"
        hints = intent.get("source_hints") or []
        if len(hints) > 1:
            return "complex"
        entities = intent.get("entities") or []
        if len(entities) > 3:
            return "complex"
        return "simple"

    def _select_sources(
        self, intent: dict[str, Any], query: str
    ) -> tuple[list[str], bool]:
        """Determine which sources to query. Returns (sources, used_default_sources)."""
        available = set(self._capabilities.all_sources())
        if not available:
            return [], False

        hints = intent.get("source_hints") or []
        hinted = self._resolve_sources_from_hints(hints)
        if hinted:
            return sorted(hinted), False

        query_target = self._match_sources_from_text(query)
        if query_target:
            return sorted(query_target), False

        personal = self._capabilities.personal_sources()
        sources = sorted(personal) if personal else sorted(available)
        return sources, True

    def _resolve_sources_from_hints(self, hints: list[Any]) -> set[str]:
        available = set(self._capabilities.all_sources())
        target: set[str] = set()
        aliases = self._build_source_aliases()
        for raw_hint in hints:
            hint = str(raw_hint or "").strip().lower()
            if not hint:
                continue
            if hint in available:
                target.add(hint)
                continue
            for source, source_aliases in aliases.items():
                if hint in source_aliases or any(
                    hint in alias or alias in hint for alias in source_aliases
                ):
                    target.add(source)
        return target

    def _build_source_aliases(self) -> dict[str, set[str]]:
        aliases: dict[str, set[str]] = {}
        for source in self._capabilities.all_sources():
            caps = self._capabilities.get(source)
            alias_set: set[str] = set()
            alias_set.add(source.lower())
            alias_set.add(source.lower().replace("_", " "))
            for token in _tokenize(source):
                if len(token) >= 3:
                    alias_set.add(token)
            if caps and getattr(caps, "display_label", None):
                label = str(caps.display_label).lower().strip()
                if label:
                    alias_set.add(label)
                    for token in _tokenize(label):
                        if len(token) >= 3:
                            alias_set.add(token)
            aliases[source] = {a for a in alias_set if a}
        return aliases

    def _match_sources_from_text(self, text: str) -> list[str]:
        text_norm = (text or "").lower().strip()
        if not text_norm:
            return []
        aliases = self._build_source_aliases()
        matched: set[str] = set()
        for source, source_aliases in aliases.items():
            for alias in source_aliases:
                if " " in alias:
                    if alias in text_norm:
                        matched.add(source)
                        break
                elif re.search(rf"\b{re.escape(alias)}\b", text_norm):
                    matched.add(source)
                    break
        return sorted(matched)

    def _order_sources_by_mention(
        self, text: str, candidate_sources: list[str]
    ) -> list[str]:
        """Order sources by first textual mention; keeps capability-driven alias matching."""
        text_norm = (text or "").lower().strip()
        if not text_norm:
            return sorted(candidate_sources)

        aliases = self._build_source_aliases()
        positions: dict[str, int] = {}
        for source in candidate_sources:
            source_aliases = aliases.get(source, set())
            best_pos: int | None = None
            for alias in source_aliases:
                if not alias:
                    continue
                pos = text_norm.find(alias)
                if pos >= 0 and (best_pos is None or pos < best_pos):
                    best_pos = pos
            if best_pos is not None:
                positions[source] = best_pos

        # Mentioned sources first (in mention order), then stable lexical fallback.
        return sorted(
            candidate_sources,
            key=lambda s: (0 if s in positions else 1, positions.get(s, 0), s),
        )

    def _extract_temporal_from_text(self, text: str) -> str | None:
        """Cheap temporal extraction for fast-path so 'latest/today/...' still use structured mode."""
        t = (text or "").lower()
        patterns = [
            (r"\bmost recent\b", "most recent"),
            (r"\blatest\b", "latest"),
            (r"\brecently\b", "recently"),
            (r"\brecent\b", "recent"),
            (r"\btoday\b", "today"),
            (r"\byesterday\b", "yesterday"),
            (r"\bthis week\b", "this week"),
            (r"\blast week\b", "last week"),
            (r"\bthis month\b", "this month"),
            (r"\blast month\b", "last month"),
        ]
        for pattern, value in patterns:
            if re.search(pattern, t):
                return value
        return None

    def _build_fast_path_retrieval_plan(
        self, text: str, ordered_sources: list[str]
    ) -> list[dict[str, Any]] | None:
        """Build a generic multi-hop plan when query references multiple explicit sources."""
        if len(ordered_sources) < 2:
            return None

        cross_step_ref = bool(
            re.search(
                r"\b(that|those|them|they)\b|\b(same person|same contact|from that|from them|by that|by them)\b",
                (text or "").lower(),
            )
        )
        plan: list[dict[str, Any]] = []
        for idx, source in enumerate(ordered_sources, start=1):
            plan.append(
                {
                    "step": f"step_{idx}",
                    "sources": [source],
                    "query_focus": "",
                    "entity_from_previous": idx > 1 and cross_step_ref,
                }
            )
        return plan

    def _extract_filters(self, intent: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract filter clauses from intent entities and temporal constraints."""
        filters: list[dict[str, Any]] = []

        entities = intent.get("entities") or []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            role = entity.get("role", "")
            name = (entity.get("name") or "").strip()
            email = (entity.get("email") or "").strip()
            if role == "sender":
                if name:
                    filters.append(
                        {"field": "from_name", "operator": "contains", "value": name}
                    )
                if email:
                    filters.append(
                        {"field": "from_email", "operator": "contains", "value": email}
                    )
            elif role == "recipient" and name:
                filters.append(
                    {"field": "to_email", "operator": "contains", "value": name}
                )

        temporal = intent.get("temporal")
        if temporal:
            temporal_str = str(temporal).lower().strip()
            tz_name = config.user_timezone or "UTC"
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
            now = datetime.now(tz)
            if temporal_str in ("today",):
                filters.append(
                    {
                        "field": "date_after",
                        "operator": "gte",
                        "value": now.date().isoformat(),
                    }
                )
            elif temporal_str in ("yesterday",):
                yesterday = (now - timedelta(days=1)).date()
                filters.append(
                    {
                        "field": "date_after",
                        "operator": "gte",
                        "value": yesterday.isoformat(),
                    }
                )
                filters.append(
                    {
                        "field": "date_before",
                        "operator": "lte",
                        "value": yesterday.isoformat(),
                    }
                )
            elif temporal_str in ("this week", "last week"):
                days = 7 if temporal_str == "this week" else 14
                filters.append(
                    {
                        "field": "date_after",
                        "operator": "gte",
                        "value": (now - timedelta(days=days)).date().isoformat(),
                    }
                )
            elif temporal_str in ("this month", "last month"):
                days = 30 if temporal_str == "this month" else 60
                filters.append(
                    {
                        "field": "date_after",
                        "operator": "gte",
                        "value": (now - timedelta(days=days)).date().isoformat(),
                    }
                )
            elif temporal_str in ("recent", "recently", "latest", "most recent"):
                filters.append(
                    {
                        "field": "date_after",
                        "operator": "gte",
                        "value": (now - timedelta(days=30)).date().isoformat(),
                    }
                )

        return filters

    def _select_methods(
        self,
        source: str,
        query: str,
        filters: list[dict[str, Any]],
        intent: dict[str, Any],
    ) -> list[str]:
        """Select retrieval methods for a specific source."""
        methods: list[str] = []
        caps = self._capabilities.get(source)
        if not caps:
            return ["vector"]

        supported = [str(m) for m in caps.supported_methods]
        has_filters = bool(filters)
        has_query = bool(query and query.strip())
        has_temporal = bool(intent.get("temporal"))

        if (has_filters or has_temporal) and "structured" in supported:
            methods.append("structured")
        if has_query and "fulltext" in supported:
            methods.append("fulltext")
        if has_query and "vector" in supported:
            methods.append("vector")

        if not methods:
            if has_query and "vector" in supported:
                methods = ["vector"]
            elif "structured" in supported:
                methods = ["structured"]
            elif supported:
                methods = [supported[0]]
        return methods

    def _filter_for_source(
        self, source: str, filters: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Keep only filters that this source actually supports."""
        caps = self._capabilities.get(source)
        if not caps:
            return []
        supported_names = {f.name for f in caps.supported_filters}
        return [f for f in filters if f["field"] in supported_names]
