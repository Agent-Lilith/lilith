from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.contracts.mcp_search_v1 import (
    CapabilityTier,
    FilterSpec,
    SearchCapabilities,
    SourceClass,
)
from src.orchestrators.search.router import RetrievalRouter


class TestRouterLogic:
    @pytest.fixture
    def capabilities(self):
        registry = MagicMock()

        # Mock personal sources
        registry.personal_sources.return_value = ["email", "calendar"]
        registry.all_sources.return_value = ["email", "calendar", "web"]

        # Mock get() behavior
        def get_caps(name):
            if name == "email":
                return SearchCapabilities(
                    source_name="email",
                    source_class=SourceClass.PERSONAL,
                    supported_methods=["structured", "fulltext"],
                    alias_hints=["gmail", "inbox"],
                    latency_tier=CapabilityTier.MEDIUM,
                    quality_tier=CapabilityTier.HIGH,
                    cost_tier=CapabilityTier.MEDIUM,
                    supported_filters=[
                        FilterSpec(
                            name="from_email", type="string", operators=["contains"]
                        ),
                        FilterSpec(
                            name="from_name", type="string", operators=["contains"]
                        ),
                    ],
                )
            if name == "calendar":
                return SearchCapabilities(
                    source_name="calendar",
                    source_class=SourceClass.PERSONAL,
                    supported_methods=["structured"],
                    alias_hints=["gcal"],
                    latency_tier=CapabilityTier.LOW,
                    quality_tier=CapabilityTier.HIGH,
                    cost_tier=CapabilityTier.LOW,
                    supported_filters=[
                        FilterSpec(name="date_after", type="date", operators=["gte"])
                    ],
                )
            if name == "web":
                return SearchCapabilities(
                    source_name="web",
                    source_class=SourceClass.WEB,
                    supported_methods=["vector"],
                    alias_hints=["internet"],
                    latency_tier=CapabilityTier.HIGH,
                    quality_tier=CapabilityTier.MEDIUM,
                    cost_tier=CapabilityTier.HIGH,
                    supported_filters=[],
                )
            return None

        registry.get.side_effect = get_caps
        return registry

    @pytest.fixture
    def router(self, capabilities):
        return RetrievalRouter(capabilities)

    def test_route_source_selection(self, router):
        """Test implicit vs explicit source routing."""
        # 1. Explicit hint "email"
        intent = {"source_hints": ["email"], "complexity": "simple"}
        plan = router.route(intent, "Check emails")
        sources = [d.source for d in plan.decisions]
        assert sources == ["email"]

        # 2. Key word "meeting" -> Calendar
        intent = {"source_hints": [], "complexity": "simple"}
        plan = router.route(intent, "Prepare for meeting")
        sources = [d.source for d in plan.decisions]
        assert "calendar" in sources

        # 3. Web explicit
        intent = {"source_hints": ["web"], "complexity": "simple"}
        plan = router.route(intent, "Search python 3.12")
        sources = [d.source for d in plan.decisions]
        assert sources == ["web"]

    def test_filter_extraction_email(self, router):
        """Test extraction of email sender filters (from_name and/or from_email)."""
        intent = {
            "entities": [{"role": "sender", "name": "Alice"}],
            "complexity": "simple",
        }
        plan = router.route(intent, "Email from Alice")

        email_decision = next(d for d in plan.decisions if d.source == "email")
        filters = email_decision.filters

        assert len(filters) == 1
        assert filters[0]["field"] == "from_name"
        assert filters[0]["value"] == "Alice"

    def test_filter_extraction_from_name_and_email(self, router):
        """When entity has both name and email, both filters are emitted."""
        intent = {
            "entities": [
                {"role": "sender", "name": "Alice", "email": "alice@example.com"}
            ],
            "complexity": "simple",
        }
        plan = router.route(intent, "Email from Alice")

        email_decision = next(d for d in plan.decisions if d.source == "email")
        filters = {f["field"]: f["value"] for f in email_decision.filters}
        assert filters.get("from_name") == "Alice"
        assert filters.get("from_email") == "alice@example.com"

    @patch("src.orchestrators.search.router.datetime")
    @patch("src.orchestrators.search.router.config")
    def test_temporal_resolution_mocked(self, mock_config, mock_datetime, router):
        """Test 'today' filter resolution."""
        mock_config.user_timezone = "UTC"
        fixed_now = datetime(2025, 5, 20, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Setup datetime mock to handle timezone arg or no arg
        def now_side_effect(tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now

        mock_datetime.now.side_effect = now_side_effect

        intent = {"temporal": "today", "complexity": "simple"}
        plan = router.route(intent, "What happened today?")

        # Calendar supports date_after
        cal_decision = next((d for d in plan.decisions if d.source == "calendar"), None)
        if cal_decision:
            f = next(
                (f for f in cal_decision.filters if f["field"] == "date_after"), None
            )
            assert f is not None
            assert f["value"] == "2025-05-20"

    def test_complexity_classification(self, router):
        # 1. Simple
        intent = {"complexity": "simple"}
        plan = router.route(intent, "find file")
        assert plan.complexity == "simple"

        # 2. Multi-source hints imply complex
        intent = {"complexity": "simple", "source_hints": ["email", "calendar"]}
        plan = router.route(intent, "cross-source lookup")
        assert plan.complexity == "complex"

        # 3. Multi-hop explicit
        intent = {"complexity": "multi_hop"}
        plan = router.route(intent, "anything")
        assert plan.complexity == "complex"

    def test_scored_source_matches_include_confidence_and_reasons(self, router):
        matches = router._match_sources_from_text(
            "Search web and email today",
            threshold=0.3,
            top_n=3,
        )
        assert matches
        assert len(matches) <= 3
        assert matches[0].confidence >= matches[-1].confidence
        assert all(m.reasons for m in matches)
        assert {m.source for m in matches}.issuperset({"web", "email"})

    def test_negative_evidence_penalizes_source_match(self, router):
        matches = router._match_sources_from_text(
            "Use web results, not email",
            threshold=0.3,
            top_n=3,
        )
        sources = {m.source for m in matches}
        assert "web" in sources
        assert "email" not in sources

    def test_alias_hints_prioritized_for_source_matching(self, router):
        matches = router._match_sources_from_text(
            "Find that gmail thread from Alice",
            threshold=0.3,
            top_n=1,
        )
        assert matches
        assert matches[0].source == "email"

    def test_fast_path_intent_builds_generic_multihop_plan(self, router):
        intent = router.infer_fast_path_intent(
            "Find latest calendar items and latest email from that person"
        )
        assert intent is not None
        assert intent["temporal"] == "latest"
        assert intent["complexity"] == "multi_hop"
        plan = intent["retrieval_plan"]
        assert isinstance(plan, list)
        assert len(plan) == 2
        assert plan[0]["sources"] == ["calendar"]
        assert plan[0]["entity_from_previous"] is False
        assert plan[1]["sources"] == ["email"]
        assert plan[1]["entity_from_previous"] is True

    def test_fast_path_intent_keeps_independent_sources_without_entity_chain(
        self, router
    ):
        intent = router.infer_fast_path_intent("Search web and email today for Python")
        assert intent is not None
        assert intent["temporal"] == "today"
        plan = intent["retrieval_plan"]
        assert isinstance(plan, list)
        assert len(plan) == 2
        assert plan[0]["entity_from_previous"] is False
        assert plan[1]["entity_from_previous"] is False
