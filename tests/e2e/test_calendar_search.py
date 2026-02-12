import pytest
import logging
from src.core.agent import Agent

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_calendar_lifecycle(agent: Agent):
    """
    Test the full lifecycle of a calendar event:
    1. Create a meeting
    2. Search to verify it exists
    3. Update the meeting
    4. Search to verify update
    5. Delete the meeting
    6. Search to verify deletion
    """
    unique_client = "TestClient_X_99" # Unique name to avoid collisions
    
    # 1. CREATE
    logger.info("Step 1: Creating meeting...")
    create_query = f"Schedule a meeting with '{unique_client}' for next Friday at 2pm to 3pm."
    result = await agent.chat(create_query)
    assert "confirm" in result.response.lower() or "scheduled" in result.response.lower() or "created" in result.response.lower()
    
    if result.pending_confirm:
        logger.info("Confirming creation...")
        result = await agent.chat("yes")
        assert "scheduled" in result.response.lower() or "created" in result.response.lower()

    # 2. SEARCH (Verify Creation)
    logger.info("Step 2: Searching for meeting...")
    search_query = f"When is my meeting with {unique_client} next Friday?"
    result = await agent.chat(search_query)
    assert unique_client in result.response
    assert "Friday" in result.response or "2" in result.response

    # 3. UPDATE
    logger.info("Step 3: Updating meeting...")
    update_query = f"Move the meeting with {unique_client} next Friday to 3pm."
    result = await agent.chat(update_query)
    if result.pending_confirm:
        result = await agent.chat("yes")
    assert "updated" in result.response.lower() or "moved" in result.response.lower()

    # 4. VERIFY UPDATE
    logger.info("Step 4: Verifying update...")
    verify_query = f"Check schedule for next Friday regarding {unique_client}"
    result = await agent.chat(verify_query)
    assert "3" in result.response or "15:00" in result.response
    
    # 5. DELETE
    # Clear history to stay under 6k context limit.
    agent.clear_history()
    logger.info("Step 5: Deleting meeting...")
    # Explicitly mention 'next Friday' to help the agent find the correct event after a clear.
    delete_query = f"Cancel the meeting with {unique_client} next Friday"
    result = await agent.chat(delete_query)
    if result.pending_confirm:
        result = await agent.chat("yes")
    assert "cancelled" in result.response.lower() or "deleted" in result.response.lower()

    # 6. VERIFY DELETION
    logger.info("Step 6: Verifying deletion...")
    final_query = f"Do I have any meetings with {unique_client} next Friday?"
    result = await agent.chat(final_query)
    negatives = ["no", "none", "don't have", "do not have", "free", "clear"]
    assert any(n in result.response.lower() for n in negatives)

