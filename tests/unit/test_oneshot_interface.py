from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.interfaces.oneshot import run_oneshot


@pytest.mark.asyncio
async def test_run_oneshot_prints_single_response(monkeypatch, capsys):
    fake_agent = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(response="final answer")),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.interfaces.oneshot.Agent.create", AsyncMock(return_value=fake_agent)
    )

    code = await run_oneshot("hello")

    out = capsys.readouterr().out
    assert code == 0
    assert "final answer" in out
    fake_agent.chat.assert_awaited_once()
    fake_agent.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_oneshot_rejects_empty_query(capsys):
    code = await run_oneshot("   ")
    out = capsys.readouterr().out
    assert code == 2
    assert "must not be empty" in out
