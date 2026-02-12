import logging

import pytest

from src.core.agent import Agent

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_search_yesterday_efficiency(agent: Agent):
    """
    Test: 'What websites did I visit yesterday?' using real DB.
    Expectation:
      - Should NOT loop 6 times.
      - Should call universal_search ONCE (or max twice if refined).
      - Should return either results or a clear 'no data' message.
    """
    query = "What websites did I visit yesterday?"
    logger.info(f"Asking: {query}")

    tool_calls = []

    async def capture_events(event_type: str, data: dict):
        if event_type == "tool_call":
            tool_calls.append(data)

    # Run the chat
    result = await agent.chat(query, on_event=capture_events)
    final_answer = result.response

    # 2. Assertions
    search_calls = [t for t in tool_calls if t.get("name") == "universal_search"]
    count = len(search_calls)

    logger.info(f"Tool calls: {count}")
    logger.info(f"Final answer: {final_answer}")

    assert count <= 2, f"Too many search calls! Expected <= 2, got {count}"
    assert count >= 1, "Should have searched at least once"

    valid_phrases = ["yesterday", "no data", "no results", "visited", "websites"]
    assert any(p in final_answer.lower() for p in valid_phrases), (
        f"Answer didn't look relevant: {final_answer}"
    )


@pytest.mark.asyncio
async def test_search_last_week_results(agent: Agent):
    """
    Test: 'What websites did I visit last week?'
    Expectation: Should find results (since user said they have data).
    """
    query = "What websites did I visit last week?"

    tool_calls = []

    async def capture_events(event_type: str, data: dict):
        if event_type == "tool_call":
            tool_calls.append(data)

    result = await agent.chat(query, on_event=capture_events)
    final_answer = result.response

    # Assert calls
    search_calls = [t for t in tool_calls if t.get("name") == "universal_search"]
    assert len(search_calls) >= 1

    assert "visited" in final_answer.lower() or "websites" in final_answer.lower()
