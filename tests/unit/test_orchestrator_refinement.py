from unittest.mock import AsyncMock, MagicMock

import pytest

from src.contracts.mcp_search_v1 import RetrievalMethod, SearchMode, SearchResultV1
from src.orchestrators.search.constants import RefinementReason
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
        assert reason == RefinementReason.NO_RESULTS

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
        assert reason == RefinementReason.LOW_CONFIDENCE

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
            reason=RefinementReason.NO_RESULTS,
        )

        assert len(refined) == 1
        assert refined[0].source == "sourceA"
        assert refined[0].filters == []  # Filters dropped
        assert refined[0].methods == ["vector", "structured"]  # Broaden methods

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
            reason=RefinementReason.LOW_CONFIDENCE,
        )

        # Should try fulltext/vector if not used
        assert len(refined) == 1
        new_methods = refined[0].methods
        assert "fulltext" in new_methods or "vector" in new_methods

    @pytest.mark.asyncio
    async def test_search_meta_includes_refinement_trace(self, orchestrator):
        orchestrator._analyze_intent = MagicMock(
            return_value={"complexity": "simple", "source_hints": []}
        )
        orchestrator._router.score_sources_from_text = MagicMock(return_value=[])
        orchestrator._router.infer_fast_path_intent = MagicMock(return_value=None)
        deterministic = MagicMock()
        deterministic.should_use_deterministic = True
        deterministic.intent = {"complexity": "simple", "source_hints": []}
        deterministic.trace.return_value = {}
        orchestrator._intent_analyzer.analyze = MagicMock(return_value=deterministic)
        orchestrator._router.route = MagicMock(
            return_value=MagicMock(
                decisions=[
                    RoutingDecision(
                        source="sourceA",
                        methods=["vector"],
                        query="query",
                        filters=[],
                        mode=SearchMode.SEARCH,
                    )
                ],
                complexity="simple",
                used_default_sources=False,
                source_matches=[],
                policy_controls={
                    "latency_budget_tier": "low",
                    "cost_budget_tier": "medium",
                    "quality_preference_tier": "high",
                    "freshness_demand_days": 1,
                    "fanout_limit": 2,
                    "reasons": ["strict_freshness_implies_low_latency"],
                },
                source_policy_trace=[
                    {
                        "source": "sourceA",
                        "total_score": 0.77,
                        "reasons": ["prioritized_quality_preference_fit"],
                    }
                ],
            )
        )
        orchestrator._execute_routing = AsyncMock(
            return_value=([], [], None, [], None, None)
        )
        orchestrator._fusion.fuse_and_rank = MagicMock(return_value=[])

        response = await orchestrator.search(user_message="query")

        trace = response.meta.get("refinement_trace")
        assert isinstance(trace, list)
        assert trace
        assert trace[0]["reason"] == RefinementReason.NO_RESULTS
        assert trace[0]["action"] == "broaden_retry_all"
        assert trace[0]["circuit_breaker_open"] is False
        assert response.meta["routing_policy"]["fanout_limit"] == 2
        assert response.meta["source_policy_trace"][0]["source"] == "sourceA"

    @pytest.mark.asyncio
    async def test_search_refinement_circuit_breaker(self, orchestrator):
        orchestrator._analyze_intent = MagicMock(
            return_value={"complexity": "simple", "source_hints": []}
        )
        orchestrator._router.score_sources_from_text = MagicMock(return_value=[])
        orchestrator._router.infer_fast_path_intent = MagicMock(return_value=None)
        deterministic = MagicMock()
        deterministic.should_use_deterministic = True
        deterministic.intent = {"complexity": "simple", "source_hints": []}
        deterministic.trace.return_value = {}
        orchestrator._intent_analyzer.analyze = MagicMock(return_value=deterministic)
        orchestrator._router.route = MagicMock(
            return_value=MagicMock(
                decisions=[
                    RoutingDecision(
                        source="sourceA",
                        methods=["vector"],
                        query="query",
                        filters=[],
                        mode=SearchMode.SEARCH,
                    )
                ],
                complexity="simple",
                used_default_sources=False,
                source_matches=[],
                policy_controls={"fanout_limit": 2},
                source_policy_trace=[],
            )
        )
        orchestrator._execute_routing = AsyncMock(
            return_value=([], [], None, [], None, None)
        )
        orchestrator._fusion.fuse_and_rank = MagicMock(return_value=[])
        orchestrator._max_refinement_rounds = 3

        response = await orchestrator.search(user_message="query")

        trace = response.meta["refinement_trace"]
        assert len(trace) == 2
        assert trace[0]["reason"] == RefinementReason.NO_RESULTS
        assert trace[0]["circuit_breaker_open"] is False
        assert trace[1]["reason"] == RefinementReason.NO_RESULTS
        assert trace[1]["circuit_breaker_open"] is True
