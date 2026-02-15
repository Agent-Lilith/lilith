from collections.abc import AsyncIterator

import pytest_asyncio

from src.core.agent import Agent


@pytest_asyncio.fixture
async def agent() -> AsyncIterator[Agent]:
    """Real agent fixture for e2e/integration suites only."""
    instance = await Agent.create()
    try:
        yield instance
    finally:
        await instance.close()
