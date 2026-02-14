"""Deterministic intent modules used before LLM fallback.

Composable extractors:
- temporal normalization
- named entity hint extraction
- query type detection (search/count/aggregate)
"""

import re
from dataclasses import dataclass, field
from typing import Any

from src.contracts.mcp_search_v1 import SearchMode
from src.orchestrators.search.constants import IntentComplexity
from src.orchestrators.search.router import SourceMatch


@dataclass
class ExtractorSignal[T]:
    """Value + confidence + trace reasons for one extractor."""

    value: T
    confidence: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class DeterministicIntentResult:
    """Output of deterministic intent analysis with confidence traces."""

    intent: dict[str, Any]
    aggregate_confidence: float
    extractor_confidence: dict[str, float]
    extractor_reasons: dict[str, list[str]]
    should_use_deterministic: bool

    def trace(self) -> dict[str, Any]:
        return {
            "aggregate_confidence": self.aggregate_confidence,
            "extractor_confidence": self.extractor_confidence,
            "extractor_reasons": self.extractor_reasons,
            "decision": "deterministic" if self.should_use_deterministic else "llm",
        }


class DeterministicIntentAnalyzer:
    """Runs deterministic intent extractors and confidence-gates LLM fallback."""

    _AGG_THRESHOLD = 0.55
    _SOURCE_THRESHOLD = 0.55

    def analyze(
        self,
        query: str,
        source_matches: list[SourceMatch],
        fast_path_intent: dict[str, Any] | None = None,
    ) -> DeterministicIntentResult:
        source_sig = self._extract_source_hints(source_matches)
        temporal_sig = self._extract_temporal(query)
        entity_sig = self._extract_entities(query)
        query_type_sig = self._extract_query_type(query)

        # Reuse fast-path retrieval planning when available.
        retrieval_plan = None
        complexity: IntentComplexity = IntentComplexity.SIMPLE
        if fast_path_intent and fast_path_intent.get("retrieval_plan"):
            retrieval_plan = fast_path_intent.get("retrieval_plan")
            complexity = IntentComplexity.MULTI_HOP
            source_sig.confidence = max(source_sig.confidence, 0.7)
            source_sig.reasons.append("fast_path_multihop_plan")

        aggregate_confidence = round(
            source_sig.confidence * 0.45
            + query_type_sig.confidence * 0.25
            + temporal_sig.confidence * 0.15
            + entity_sig.confidence * 0.15,
            3,
        )

        should_use_deterministic = bool(
            source_sig.confidence >= self._SOURCE_THRESHOLD
            or aggregate_confidence >= self._AGG_THRESHOLD
        )

        query_type = query_type_sig.value or {}
        intent: dict[str, Any] = {
            "intent": "find_information",
            "entities": entity_sig.value,
            "temporal": temporal_sig.value,
            "source_hints": source_sig.value,
            "complexity": complexity,
            "retrieval_plan": retrieval_plan,
            "search_mode": query_type.get("search_mode", SearchMode.SEARCH),
            "aggregate_group_by": query_type.get("aggregate_group_by"),
            "aggregate_top_n": query_type.get("aggregate_top_n", 10),
        }

        return DeterministicIntentResult(
            intent=intent,
            aggregate_confidence=aggregate_confidence,
            extractor_confidence={
                "source_hints": round(source_sig.confidence, 3),
                "temporal": round(temporal_sig.confidence, 3),
                "entities": round(entity_sig.confidence, 3),
                "query_type": round(query_type_sig.confidence, 3),
            },
            extractor_reasons={
                "source_hints": source_sig.reasons,
                "temporal": temporal_sig.reasons,
                "entities": entity_sig.reasons,
                "query_type": query_type_sig.reasons,
            },
            should_use_deterministic=should_use_deterministic,
        )

    def _extract_source_hints(
        self, matches: list[SourceMatch]
    ) -> ExtractorSignal[list[str]]:
        if not matches:
            return ExtractorSignal(
                value=[], confidence=0.0, reasons=["no_source_match"]
            )

        ordered = sorted(matches, key=lambda m: (-m.confidence, m.source))
        hints = [m.source for m in ordered]
        confidence = min(1.0, max(m.confidence for m in ordered))
        reasons = [f"{m.source}:{m.confidence:.2f}" for m in ordered[:3]]
        return ExtractorSignal(value=hints, confidence=confidence, reasons=reasons)

    def _extract_temporal(self, query: str) -> ExtractorSignal[str | None]:
        text = (query or "").lower().strip()
        patterns: list[tuple[str, str, float]] = [
            (r"\bmost recent\b", "most recent", 0.9),
            (r"\blatest\b", "latest", 0.9),
            (r"\brecently\b", "recently", 0.85),
            (r"\brecent\b", "recent", 0.8),
            (r"\btoday\b", "today", 1.0),
            (r"\byesterday\b", "yesterday", 1.0),
            (r"\bthis week\b", "this week", 0.95),
            (r"\blast week\b", "last week", 0.95),
            (r"\bthis month\b", "this month", 0.95),
            (r"\blast month\b", "last month", 0.95),
        ]
        for pat, normalized, conf in patterns:
            if re.search(pat, text):
                return ExtractorSignal(
                    value=normalized,
                    confidence=conf,
                    reasons=[f"matched:{normalized}"],
                )
        return ExtractorSignal(value=None, confidence=0.0, reasons=["no_temporal"])

    def _extract_entities(self, query: str) -> ExtractorSignal[list[dict[str, Any]]]:
        text = (query or "").strip()
        entities: list[dict[str, Any]] = []
        reasons: list[str] = []
        conf = 0.0

        # Sender hint: "from <name|email>".
        from_match = re.search(
            r"\bfrom\s+([A-Za-z][A-Za-z0-9._' -]{1,60}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        if from_match:
            raw = from_match.group(1).strip()
            entity: dict[str, Any] = {"role": "sender"}
            if "@" in raw:
                entity["email"] = raw
                conf = max(conf, 0.9)
                reasons.append("sender_email")
            else:
                entity["name"] = raw
                conf = max(conf, 0.75)
                reasons.append("sender_name")
            entities.append(entity)

        # Recipient hint: "to <name|email>".
        to_match = re.search(
            r"\bto\s+([A-Za-z][A-Za-z0-9._' -]{1,60}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        if to_match:
            raw = to_match.group(1).strip()
            entity = {"role": "recipient"}
            if "@" in raw:
                entity["email"] = raw
                conf = max(conf, 0.9)
                reasons.append("recipient_email")
            else:
                entity["name"] = raw
                conf = max(conf, 0.75)
                reasons.append("recipient_name")
            entities.append(entity)

        if not entities:
            reasons.append("no_entities")
        return ExtractorSignal(value=entities, confidence=conf, reasons=reasons)

    def _extract_query_type(self, query: str) -> ExtractorSignal[dict[str, Any]]:
        text = (query or "").lower().strip()
        if not text:
            return ExtractorSignal(
                value={"search_mode": SearchMode.SEARCH},
                confidence=0.0,
                reasons=["empty_query"],
            )

        if re.search(r"\b(how many|count|number of|total)\b", text):
            return ExtractorSignal(
                value={"search_mode": SearchMode.COUNT},
                confidence=0.95,
                reasons=["count_keyword"],
            )

        if re.search(r"\b(top|breakdown|grouped|group by|per)\b", text):
            return ExtractorSignal(
                value={"search_mode": SearchMode.AGGREGATE, "aggregate_top_n": 10},
                confidence=0.8,
                reasons=["aggregate_keyword"],
            )

        return ExtractorSignal(
            value={"search_mode": SearchMode.SEARCH},
            confidence=0.45,
            reasons=["default_search_mode"],
        )
