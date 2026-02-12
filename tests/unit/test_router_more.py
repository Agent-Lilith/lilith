import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, date
from zoneinfo import ZoneInfo
from src.orchestrators.search.router import RetrievalRouter
from src.contracts.mcp_search_v1 import SearchCapabilities, SourceClass, FilterSpec

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
                    supported_filters=[FilterSpec(name="from_email", type="string", operators=["contains"])]
                )
            if name == "calendar":
                return SearchCapabilities(
                    source_name="calendar",
                    source_class=SourceClass.PERSONAL,
                    supported_methods=["structured"],
                    supported_filters=[FilterSpec(name="date_after", type="date", operators=["gte"])]
                )
            if name == "web":
                return SearchCapabilities(
                    source_name="web",
                    source_class=SourceClass.WEB,
                    supported_methods=["vector"],
                    supported_filters=[]
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
        """Test extraction of email-specific filters."""
        intent = {
            "entities": [{"role": "sender", "name": "alice@example.com"}],
            "complexity": "simple"
        }
        plan = router.route(intent, "Email from alice@example.com")
        
        email_decision = next(d for d in plan.decisions if d.source == "email")
        filters = email_decision.filters
        
        assert len(filters) == 1
        assert filters[0]["field"] == "from_email"
        assert filters[0]["value"] == "alice@example.com"

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
            f = next((f for f in cal_decision.filters if f["field"] == "date_after"), None)
            assert f is not None
            assert f["value"] == "2025-05-20"

    def test_complexity_classification(self, router):
        # 1. Simple
        intent = {"complexity": "simple"}
        plan = router.route(intent, "find file")
        assert plan.complexity == "simple"

        # 2. Complex keywords
        intent = {"complexity": "simple"}  # LLM said simple, but keyword overrides
        plan = router.route(intent, "relationship between X and Y")
        assert plan.complexity == "complex"

        # 3. Multi-hop explicit
        intent = {"complexity": "multi_hop"}
        plan = router.route(intent, "anything")
        assert plan.complexity == "complex"
