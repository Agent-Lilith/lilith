import pytest
from unittest.mock import MagicMock
from src.orchestrators.search.orchestrator import UniversalSearchOrchestrator
from src.contracts.mcp_search_v1 import SearchResultV1
from src.orchestrators.search.router import RoutingDecision

class TestOrchestratorRefinement:
    @pytest.fixture
    def orchestrator(self):
        # We don't need real dependencies for unit testing _should_refine
        return UniversalSearchOrchestrator(
            capabilities=MagicMock(),
            dispatcher=MagicMock(),
            direct_backends=[],
        )

    def test_skip_refinement_explicit_filters_simple_query(self, orchestrator):
        """
        Test that we skip refinement when:
        - Results are empty
        - Query complexity is 'simple'
        - Explicit filters were used
        """
        results = []
        intent = {"complexity": "simple", "temporal": "yesterday"}
        # Routing decision with filters
        decisions = [
            RoutingDecision(
                source="browser_history",
                methods=["structured"],
                query="test",
                filters=[{"field": "date", "operator": "eq", "value": "2024-01-01"}]
            )
        ]

        should_refine, reason = orchestrator._should_refine(results, intent, decisions)
        
        assert should_refine is False
        assert reason == ""

    def test_do_refine_no_filters_simple_query(self, orchestrator):
        """
        Test that we DO refine when:
        - Results are empty
        - Query complexity is 'simple'
        - NO explicit filters used (just vector search maybe)
        """
        results = []
        intent = {"complexity": "simple"}
        # Routing decision without filters
        decisions = [
            RoutingDecision(
                source="browser_history",
                methods=["vector"],
                query="test",
                filters=[]
            )
        ]

        should_refine, reason = orchestrator._should_refine(results, intent, decisions)
        
        assert should_refine is True
        assert reason == "no_results"

    def test_do_refine_explicit_filters_complex_query(self, orchestrator):
        """
        Test that we DO refine when:
        - Results are empty
        - Query complexity is 'complex' (multi-hop)
        - Explicit filters used
        """
        results = []
        intent = {"complexity": "multi_hop", "temporal": "yesterday"}
        decisions = [
            RoutingDecision(
                source="browser_history",
                methods=["structured"],
                query="test",
                filters=[{"field": "date", "operator": "eq", "value": "2024-01-01"}]
            )
        ]

        should_refine, reason = orchestrator._should_refine(results, intent, decisions)
        
        assert should_refine is True
        assert reason == "no_results"
