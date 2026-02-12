import pytest
import asyncio
import os
from src.core.config import config
from src.core.agent import Agent
from src.core.bootstrap import setup_tools

# Force using the user's real database / config
# We assume the environment is already set up correctly in the shell executing the tests

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

import pytest_asyncio

@pytest_asyncio.fixture
async def agent():
    """Initialize a real Agent with real tools."""
    # Agent.create() handles setup_tools() and config validation
    return await Agent.create()
