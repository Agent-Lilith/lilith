"""Shared typed constants for search orchestration control flow."""

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
