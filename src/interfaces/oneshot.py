"""One-shot interface: run a single query, print response, exit."""

from __future__ import annotations

import asyncio

from src.core.agent import Agent
from src.core.config import config
from src.llm.openrouter_client import OpenRouterClient


async def run_oneshot(query: str, use_external: bool = False) -> int:
    text = (query or "").strip()
    if not text:
        print("Error: query must not be empty")
        return 2

    if use_external and (
        not config.openrouter_api_key or not config.openrouter_api_key.strip()
    ):
        print("Error: OPENROUTER_API_KEY is required for --external")
        return 2

    agent = await Agent.create()
    openrouter_client: OpenRouterClient | None = None
    try:
        if use_external:
            openrouter_client = OpenRouterClient()
        result = await agent.chat(text, llm_client_override=openrouter_client)
        print(result.response)
        return 0
    finally:
        await agent.close()
        if openrouter_client is not None:
            await openrouter_client.close()


def main(query: str, use_external: bool = False) -> int:
    return asyncio.run(run_oneshot(query=query, use_external=use_external))
