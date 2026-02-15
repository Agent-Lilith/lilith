from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.contracts.mcp_search_v1 import (
    CapabilityTier,
    FilterSpec,
    RetrievalMethod,
    SearchCapabilities,
    SourceClass,
)
from src.orchestrators.search.router import RetrievalRouter


@dataclass
class FakeCapabilities:
    _caps: dict[str, SearchCapabilities]

    def all_sources(self) -> list[str]:
        return list(self._caps.keys())

    def personal_sources(self) -> list[str]:
        return [
            name
            for name, caps in self._caps.items()
            if caps.source_class == SourceClass.PERSONAL
        ]

    def get(self, source_name: str) -> SearchCapabilities | None:
        return self._caps.get(source_name)

    def supports_group_by(self, source_name: str, field: str) -> bool:
        caps = self._caps.get(source_name)
        if not caps:
            return False
        return field in (caps.supported_group_by_fields or [])


FILTER_NAMES = ["from_name", "from_email", "date_after", "date_before", "to_email"]


@st.composite
def caps_strategy(draw: st.DrawFn) -> FakeCapabilities:
    source_count = draw(st.integers(min_value=1, max_value=4))
    caps_map: dict[str, SearchCapabilities] = {}

    for idx in range(source_count):
        source_name = f"source_{idx}"
        method_pool = draw(
            st.lists(
                st.sampled_from(["structured", "fulltext", "vector"]),
                min_size=1,
                max_size=3,
                unique=True,
            )
        )
        filter_pool = draw(
            st.lists(st.sampled_from(FILTER_NAMES), min_size=0, max_size=3, unique=True)
        )
        filters = [
            FilterSpec(name=name, type="string", operators=["contains"])
            for name in filter_pool
        ]
        caps_map[source_name] = SearchCapabilities(
            source_name=source_name,
            source_class=draw(st.sampled_from([SourceClass.PERSONAL, SourceClass.WEB])),
            supported_methods=[RetrievalMethod(m) for m in method_pool],
            supported_filters=filters,
            alias_hints=[source_name, source_name.replace("_", " ")],
            freshness_window_days=draw(st.integers(min_value=1, max_value=90)),
            latency_tier=draw(
                st.sampled_from(
                    [CapabilityTier.LOW, CapabilityTier.MEDIUM, CapabilityTier.HIGH]
                )
            ),
            quality_tier=draw(
                st.sampled_from(
                    [CapabilityTier.LOW, CapabilityTier.MEDIUM, CapabilityTier.HIGH]
                )
            ),
            cost_tier=draw(
                st.sampled_from(
                    [CapabilityTier.LOW, CapabilityTier.MEDIUM, CapabilityTier.HIGH]
                )
            ),
        )

    return FakeCapabilities(caps_map)


@given(
    caps=caps_strategy(),
    source_hints=st.lists(st.text(min_size=1, max_size=8), max_size=3),
)
@pytest.mark.property
def test_route_filters_are_supported_by_each_source(
    caps: FakeCapabilities,
    source_hints: list[str],
) -> None:
    router = RetrievalRouter(caps)  # type: ignore[arg-type]
    intent = {
        "complexity": "simple",
        "source_hints": source_hints,
        "entities": [{"role": "sender", "name": "Alice"}],
        "temporal": "today",
    }

    plan = router.route(intent, "emails from Alice today")

    for decision in plan.decisions:
        declared = caps.get(decision.source)
        assert declared is not None
        supported_fields = {f.name for f in declared.supported_filters}
        assert all(f["field"] in supported_fields for f in decision.filters)


@given(
    caps=caps_strategy(),
    query=st.text(min_size=1, max_size=50),
    threshold=st.floats(min_value=0.0, max_value=1.0),
)
@pytest.mark.property
def test_source_scoring_is_sorted_and_bounded(
    caps: FakeCapabilities,
    query: str,
    threshold: float,
) -> None:
    router = RetrievalRouter(caps)  # type: ignore[arg-type]
    matches = router.score_sources_from_text(query, threshold=threshold, top_n=5)

    confidences = [m.confidence for m in matches]
    assert all(0.0 <= c <= 1.0 for c in confidences)
    assert confidences == sorted(confidences, reverse=True)
    assert len(matches) <= 5
