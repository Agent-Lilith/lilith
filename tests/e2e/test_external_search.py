import logging

import pytest

from src.core.agent import Agent

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_external_search_summarization(agent: Agent):
    """
    Test summarization of broad external-like queries.
    This simulates a "complex" query that might trigger the larger LLM
    if the /external flag was used, or just tests the Universal Search
    orchestration's ability to handle "summarize" intent.
    """
    # We use a query that implies "web" or broad knowledge to trigger that path
    query = "Summarize the latest features of Python 3.12 based on what I might have visited."

    tool_calls = []

    async def capture_events(event_type: str, data: dict):
        if event_type == "tool_call":
            tool_calls.append(data)

    result = await agent.chat(query, on_event=capture_events)

    # Assertions
    # 1. Check response isn't empty
    assert result.response
    assert len(result.response) > 50

    # 2. Check Universal Search was called
    # (Since we haven't mocked web history, it might just return what it knows or say no data,
    # but the tool should be invoked).
    search_calls = [t for t in tool_calls if t.get("name") == "universal_search"]
    assert len(search_calls) >= 1

    # 3. Check for reasonable output
    # Even if "no data", it should be polite.
    if "no data" in result.response.lower():
        logger.info("Correctly identified no local data for Python 3.12")
    else:
        # If it hallucinated or found something (if real web search enabled),
        # ensure it mentions Python.
        assert "python" in result.response.lower()
