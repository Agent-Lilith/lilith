from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.orchestrators.search.router import RetrievalRouter


class TestRouterFilters:
    @pytest.fixture
    def router(self):
        return RetrievalRouter(capabilities=MagicMock())

    @patch("src.orchestrators.search.router.datetime")
    @patch("src.orchestrators.search.router.config")
    def test_extract_yesterday_filter(self, mock_config, mock_datetime, router):
        """Test that 'yesterday' extracts correct date range in local timezone."""
        # Setup mock time: 2024-01-02 10:00:00 UTC
        # If user is in UTC, yesterday is 2024-01-01

        mock_config.user_timezone = "UTC"
        # Mock now() to return a specific datetime
        # We need to mock datetime.now(tz)
        fixed_now = datetime(2024, 1, 2, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
        mock_datetime.now.return_value = fixed_now

        # We need side_effect to handle tz argument if called with it
        def now_side_effect(tz=None):
            if tz:
                return fixed_now.astimezone(tz)
            return fixed_now

        mock_datetime.now.side_effect = now_side_effect

        filters = router._extract_filters({"temporal": "yesterday"})

        # Expect date_after and date_before for 2024-01-01
        assert len(filters) == 2

        f1 = next(f for f in filters if f["field"] == "date_after")
        f2 = next(f for f in filters if f["field"] == "date_before")

        assert f1["value"] == "2024-01-01"
        assert f2["value"] == "2024-01-01"

    @patch("src.orchestrators.search.router.datetime")
    @patch("src.orchestrators.search.router.config")
    def test_extract_today_filter(self, mock_config, mock_datetime, router):
        """Test that 'today' extracts correct date range."""
        mock_config.user_timezone = "UTC"
        fixed_now = datetime(2024, 1, 2, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
        mock_datetime.now.return_value = fixed_now

        filters = router._extract_filters({"temporal": "today"})

        # Expect date_after >= 2024-01-02
        assert len(filters) == 1
        f1 = filters[0]
        assert f1["field"] == "date_after"
        assert f1["value"] == "2024-01-02"
