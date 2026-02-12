from unittest.mock import MagicMock

import pytest

from src.contracts.mcp_search_v1 import RetrievalMethod, SearchResultV1
from src.orchestrators.search.orchestrator import UniversalSearchOrchestrator
from src.orchestrators.search.router import RoutingDecision


class TestOrchestratorRefinement:
    @pytest.fixture
    def orchestrator(self):
        caps = MagicMock()
        # Mock capability checks for refinement
        caps.can_handle.return_value = True

        return UniversalSearchOrchestrator(
            capabilities=caps,
            dispatcher=MagicMock(),
            direct_backends=[],
            max_refinement_rounds=1,
        )

    def test_should_refine_triggers(self, orchestrator):
        # 1. No results + No filters -> True (Refine)
        results = []
        intent = {"complexity": "simple"}
        decisions = [RoutingDecision("test", ["vector"], "query", [])]

        should, reason = orchestrator._should_refine(results, intent, decisions)
        assert should is True
        assert reason == "no_results"

        # 2. No results + Explicit filters -> False (Don't broaden user intent)
        decisions_filtered = [
            RoutingDecision("test", ["vector"], "query", [{"field": "date"}])
        ]
        should, reason = orchestrator._should_refine(
            results, intent, decisions_filtered
        )
        assert should is False

        # 3. Low confidence -> True
        low_conf_results = [
            SearchResultV1(
                id=f"test-{i}",
                source="test",
                content="meh",
                method=RetrievalMethod.VECTOR,
                scores={"relevance": 0.1},
            )
            for i in range(3)
        ]
        should, reason = orchestrator._should_refine(
            low_conf_results, intent, decisions
        )
        assert should is True
        assert reason == "low_confidence"

    @pytest.mark.asyncio
    async def test_refine_actions(self, orchestrator):
        # 1. Broaden (no_results)
        decisions = [
            RoutingDecision("sourceA", ["structured"], "query", [{"field": "date"}])
        ]

        refined = await orchestrator._refine(
            context="ctx",
            intent={},
            results=[],
            previous_decisions=decisions,
            reason="no_results",
        )

        assert len(refined) == 1
        assert refined[0].source == "sourceA"
        assert refined[0].filters == []  # Filters dropped
        assert refined[0].methods == ["vector"]  # Fallback to vector

        # 2. Switch Method (low_confidence)
        # Mock that sourceA supports 'fulltext'
        orchestrator._capabilities.can_handle.side_effect = lambda src, meth: (
            meth in ["fulltext", "vector"]
        )

        decisions = [RoutingDecision("sourceA", ["structured"], "query", [])]

        refined = await orchestrator._refine(
            context="ctx",
            intent={},
            results=[],
            previous_decisions=decisions,
            reason="low_confidence",
        )

        # Should try fulltext/vector if not used
        assert len(refined) == 1
        new_methods = refined[0].methods
        assert "fulltext" in new_methods or "vector" in new_methods
