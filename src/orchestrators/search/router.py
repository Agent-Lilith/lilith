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

from src.contracts.mcp_search_v1 import SearchMode
from src.core.config import config
from src.orchestrators.search.capabilities import CapabilityRegistry
from src.orchestrators.search.constants import IntentComplexity, RoutingComplexity

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """A decision to search a specific source with specific methods and filters."""

    source: str
    methods: list[str]
    query: str
    filters: list[dict[str, Any]] = field(default_factory=list)
    mode: SearchMode = SearchMode.SEARCH
    sort_field: str | None = None
    sort_order: str = "desc"
    group_by: str | None = None
    aggregate_top_n: int = 10


@dataclass
class RoutingPlan:
    """Complete routing plan for a query."""

    decisions: list[RoutingDecision]
    complexity: RoutingComplexity
    reasoning: str = ""
    used_default_sources: bool = False
    source_matches: list["SourceMatch"] = field(default_factory=list)


@dataclass
class SourceMatch:
    """Scored source hint match with lightweight explanations."""

    source: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


class RetrievalRouter:
    """Routes queries to appropriate sources and methods based on capabilities."""

    _MATCH_THRESHOLD = 0.3
    _MATCH_TOP_N = 3

    def __init__(self, capabilities: CapabilityRegistry) -> None:
        self._capabilities = capabilities

    def score_sources_from_text(
        self,
        text: str,
        threshold: float = 0.0,
        top_n: int | None = None,
    ) -> list[SourceMatch]:
        """Public source scoring API for deterministic intent modules."""
        return self._match_sources_from_text(text, threshold=threshold, top_n=top_n)

    def infer_fast_path_intent(self, query: str) -> dict[str, Any] | None:
        """Build a lightweight intent when source hints are obvious from runtime capabilities."""
        text = (query or "").strip()
        if not text or len(text) > 200:
            return None

        hint_matches = self._match_sources_from_text(
            text,
            threshold=self._MATCH_THRESHOLD,
            top_n=self._MATCH_TOP_N,
        )
        hints = [m.source for m in hint_matches]
        if not hints:
            return None

        ordered_hints = [m.source for m in hint_matches]
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
            "complexity": (
                IntentComplexity.MULTI_HOP
                if retrieval_plan
                else IntentComplexity.SIMPLE
            ),
            "retrieval_plan": retrieval_plan,
        }

    def route(self, intent: dict[str, Any], query: str) -> RoutingPlan:
        """Build a routing plan from intent analysis and query text."""
        complexity = self._classify_complexity(intent)
        target_sources, used_default_sources, source_matches = self._select_sources(
            intent, query
        )
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
            source_matches=source_matches,
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
    ) -> tuple[SearchMode, str | None, int]:
        """Extract search mode/group_by from intent, then validate against capabilities."""
        mode_raw = str(intent.get("search_mode") or SearchMode.SEARCH).strip().lower()
        try:
            mode = SearchMode(mode_raw)
        except ValueError:
            mode = SearchMode.SEARCH

        if mode != SearchMode.AGGREGATE:
            return mode, None, 10

        top_n = int(intent.get("aggregate_top_n", 10) or 10)
        top_n = min(100, max(1, top_n))
        requested_group_by = str(intent.get("aggregate_group_by") or "").strip()

        if requested_group_by:
            for src in sources:
                if self._capabilities.supports_group_by(src, requested_group_by):
                    return SearchMode.AGGREGATE, requested_group_by, top_n

        for src in sources:
            caps = self._capabilities.get(src)
            if not caps:
                continue
            fields = getattr(caps, "supported_group_by_fields", None) or []
            if fields:
                return SearchMode.AGGREGATE, str(fields[0]), top_n

        return SearchMode.SEARCH, None, 10

    def _classify_complexity(self, intent: dict[str, Any]) -> RoutingComplexity:
        """Classify query as simple/complex using intent structure."""
        if intent.get("complexity") == IntentComplexity.MULTI_HOP:
            return RoutingComplexity.COMPLEX
        plan = intent.get("retrieval_plan") or []
        if isinstance(plan, list) and len(plan) > 1:
            return RoutingComplexity.COMPLEX
        hints = intent.get("source_hints") or []
        if len(hints) > 1:
            return RoutingComplexity.COMPLEX
        entities = intent.get("entities") or []
        if len(entities) > 3:
            return RoutingComplexity.COMPLEX
        return RoutingComplexity.SIMPLE

    def _select_sources(
        self, intent: dict[str, Any], query: str
    ) -> tuple[list[str], bool, list[SourceMatch]]:
        """Determine which sources to query.

        Returns: (sources, used_default_sources, source_matches)
        """
        available = set(self._capabilities.all_sources())
        if not available:
            return [], False, []

        hints = intent.get("source_hints") or []
        if hints:
            hint_text = " ".join(str(h or "") for h in hints)
            hint_matches = self._match_sources_from_text(
                hint_text,
                threshold=self._MATCH_THRESHOLD,
                top_n=self._MATCH_TOP_N,
            )
            hinted = [m.source for m in hint_matches]
            if hinted:
                return hinted, False, hint_matches

        query_matches = self._match_sources_from_text(
            query,
            threshold=self._MATCH_THRESHOLD,
            top_n=self._MATCH_TOP_N,
        )
        query_target = [m.source for m in query_matches]
        if query_target:
            return query_target, False, query_matches

        personal = self._capabilities.personal_sources()
        sources = sorted(personal) if personal else sorted(available)
        return sources, True, []

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

    def _match_sources_from_text(
        self,
        text: str,
        threshold: float = 0.0,
        top_n: int | None = None,
    ) -> list[SourceMatch]:
        text_norm = (text or "").lower().strip()
        if not text_norm:
            return []
        aliases = self._build_source_aliases()
        query_tokens = set(_tokenize(text_norm))
        scored: list[tuple[float, int, SourceMatch]] = []

        for source, source_aliases in aliases.items():
            score = 0.0
            reasons: list[str] = []
            first_pos: int | None = None

            for alias in source_aliases:
                if not alias:
                    continue
                alias_norm = alias.lower().strip()
                if not alias_norm:
                    continue

                found = False
                if " " in alias_norm:
                    pos = text_norm.find(alias_norm)
                    if pos >= 0:
                        found = True
                else:
                    m = re.search(rf"\b{re.escape(alias_norm)}\b", text_norm)
                    pos = m.start() if m else -1
                    if m:
                        found = True

                if found:
                    phrase_bonus = 0.35
                    score += phrase_bonus
                    if " " in alias_norm:
                        reasons.append(f"phrase_match:{alias_norm}")
                    else:
                        reasons.append(f"token_match:{alias_norm}")
                    if first_pos is None or pos < first_pos:
                        first_pos = pos

                if text_norm == alias_norm:
                    score += 0.5
                    reasons.append(f"exact_alias:{alias_norm}")

                neg_pattern = (
                    rf"\b(?:not|without|except|excluding|instead of)\s+"
                    rf"{re.escape(alias_norm)}\b"
                )
                if re.search(neg_pattern, text_norm):
                    score -= 0.7
                    reasons.append(f"negative_evidence:{alias_norm}")

            source_tokens = {
                t for a in source_aliases for t in _tokenize(a) if len(t) >= 3
            }
            overlap = query_tokens & source_tokens
            if source_tokens and overlap:
                overlap_ratio = len(overlap) / len(source_tokens)
                token_score = min(0.35, overlap_ratio * 0.35)
                score += token_score
                reasons.append(
                    "token_overlap:" + ",".join(sorted(overlap)) + f":{token_score:.2f}"
                )

            if first_pos is not None:
                pos_bonus = 0.2 * (1.0 - min(first_pos, 200) / 200.0)
                score += max(0.0, pos_bonus)
                reasons.append(f"position_bonus:{pos_bonus:.2f}")

            confidence = max(0.0, min(1.0, score))
            if confidence <= 0:
                continue
            scored.append(
                (
                    confidence,
                    first_pos if first_pos is not None else 9999,
                    SourceMatch(
                        source=source,
                        confidence=round(confidence, 3),
                        reasons=reasons[:6],
                    ),
                )
            )

        scored.sort(key=lambda x: (-x[0], x[1], x[2].source))
        matches = [m for _, _, m in scored]
        if threshold > 0:
            matches = [m for m in matches if m.confidence >= threshold]
        if top_n is not None and top_n > 0:
            matches = matches[:top_n]
        return matches

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
