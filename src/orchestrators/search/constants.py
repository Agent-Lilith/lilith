"""Shared typed constants for search orchestration control flow."""

from dataclasses import dataclass
from enum import StrEnum


class IntentComplexity(StrEnum):
    """Complexity values produced by intent analysis."""

    SIMPLE = "simple"
    MULTI_HOP = "multi_hop"


class RoutingComplexity(StrEnum):
    """Complexity values used by routing plans."""

    SIMPLE = "simple"
    COMPLEX = "complex"


class RefinementReason(StrEnum):
    """Deterministic reasons that can trigger refinement."""

    NO_RESULTS = "no_results"
    LOW_SOURCE_COVERAGE = "low_source_coverage"
    LOW_CONFIDENCE = "low_confidence"
    SINGLE_SOURCE = "single_source"


class RefinementActionKind(StrEnum):
    """Typed refinement strategy kinds."""

    BROADEN_RETRY_ALL = "broaden_retry_all"
    RETRY_MISSING_SOURCES = "retry_missing_sources"
    DIVERSIFY_METHODS = "diversify_methods"
    BACKFILL_SINGLE_SOURCE = "backfill_single_source"


@dataclass(frozen=True)
class RefinementAction:
    """Policy entry for a refinement reason."""

    kind: RefinementActionKind
    max_triggers_per_search: int
    max_decisions: int = 4


REFINEMENT_ACTIONS: dict[RefinementReason, RefinementAction] = {
    RefinementReason.NO_RESULTS: RefinementAction(
        kind=RefinementActionKind.BROADEN_RETRY_ALL,
        max_triggers_per_search=1,
    ),
    RefinementReason.LOW_SOURCE_COVERAGE: RefinementAction(
        kind=RefinementActionKind.RETRY_MISSING_SOURCES,
        max_triggers_per_search=1,
    ),
    RefinementReason.LOW_CONFIDENCE: RefinementAction(
        kind=RefinementActionKind.DIVERSIFY_METHODS,
        max_triggers_per_search=1,
    ),
    RefinementReason.SINGLE_SOURCE: RefinementAction(
        kind=RefinementActionKind.BACKFILL_SINGLE_SOURCE,
        max_triggers_per_search=1,
    ),
}
