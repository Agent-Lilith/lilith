from src.orchestrators.search.dispatcher import MCPSearchDispatcher


def test_dispatcher_applies_capability_request_routing_args():
    dispatcher = MCPSearchDispatcher()

    captured: dict[str, dict] = {}

    async def mcp_call(_: str, args: dict) -> dict:
        captured["args"] = dict(args)
        return {"success": True, "results": [], "mode": "search"}

    dispatcher.register_mcp(
        connection_key="browser",
        source_names=["browser_history", "browser_bookmarks"],
        mcp_call=mcp_call,
        request_routing_args={
            "browser_history": {"search_history": True, "search_bookmarks": False},
            "browser_bookmarks": {"search_history": False, "search_bookmarks": True},
        },
    )

    import asyncio

    asyncio.run(
        dispatcher.search(
            source="browser_history",
            query="python",
            methods=["vector"],
            filters=[],
        )
    )

    assert captured["args"]["search_history"] is True
    assert captured["args"]["search_bookmarks"] is False
    assert captured["args"]["query"] == "python"
