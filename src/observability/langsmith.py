"""LangSmith tracing integration."""

from __future__ import annotations

import atexit
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from langsmith import Client as LangSmithClient

_ENABLED = os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true"

_LangSmithRunType = Literal[
    "tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"
]


class _NoOpRun:
    def end(self, outputs: dict[str, Any] | None = None) -> None:
        pass


class _NoOpTraceContext:
    def __enter__(self) -> _NoOpRun:
        return _NoOpRun()

    def __exit__(self, *args: Any) -> None:
        pass

    async def __aenter__(self) -> _NoOpRun:
        return _NoOpRun()

    async def __aexit__(self, *args: Any) -> None:
        pass


def _noop_trace(
    name: str,
    run_type: str = "chain",
    *,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> _NoOpTraceContext:
    del name, run_type, inputs, metadata, kwargs
    return _NoOpTraceContext()


def _noop_traceable(
    name: str | None = None,
    run_type: str = "chain",
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        return fn

    return decorator


def _noop_flush() -> None:
    pass


def _noop_get_client() -> LangSmithClient | None:
    return None


trace = _noop_trace
traceable = _noop_traceable
flush = _noop_flush
get_client = _noop_get_client

if _ENABLED:
    try:
        from langsmith import Client as LangSmithClient
        from langsmith import traceable as _ls_traceable
        from langsmith.run_helpers import trace as _ls_trace

        _project = os.getenv("LANGSMITH_PROJECT", "lilith")
        _client: LangSmithClient | None = None

        def get_client() -> LangSmithClient | None:
            global _client
            if _client is None:
                _client = LangSmithClient()
            return _client

        def trace(
            name: str,
            run_type: str = "chain",
            *,
            inputs: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None,
            project_name: str | None = None,
            **kwargs: Any,
        ):
            return _ls_trace(
                name,
                run_type=cast("_LangSmithRunType", run_type),
                inputs=inputs or {},
                metadata=metadata or {},
                project_name=project_name or _project,
                **kwargs,
            )

        def traceable(
            name: str | None = None,
            run_type: str = "chain",
            **kwargs: Any,
        ):
            return _ls_traceable(  # type: ignore[call-overload]
                name=name,
                run_type=run_type,
                **kwargs,
            )

        def flush() -> None:
            c = get_client()
            if c is not None:
                c.flush()

        atexit.register(flush)

    except ImportError:
        pass
