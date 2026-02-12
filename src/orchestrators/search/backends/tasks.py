"""Tasks search backend: Google Tasks API. Returns SearchResultV1."""

import asyncio
from typing import Any

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass
from src.orchestrators.search.interface import SearchBackend


class TasksSearchBackend(SearchBackend):
    def __init__(self, google_service: Any):
        self._service = google_service

    async def search(
        self,
        query: str,
        methods: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        top_k: int = 10,
    ) -> list[SearchResultV1]:
        if not self._service.is_connected:
            return []

        f_dict = {
            fc["field"]: fc["value"]
            for fc in (filters or [])
            if "field" in fc and "value" in fc
        }
        list_id = f_dict.get("list_id", "")
        show_completed = f_dict.get("show_completed", True)
        query_lower = (query or "").lower().split()

        def _sync_list_tasks() -> list[SearchResultV1]:
            lid = self._service.get_task_list_id(list_id or None)
            tasks_result = (
                self._service.tasks.tasks()
                .list(
                    tasklist=lid,
                    showCompleted=bool(show_completed),
                )
                .execute()
            )
            raw = tasks_result.get("items", [])

            if query_lower:
                filtered = [
                    t
                    for t in raw
                    if any(
                        q
                        in (
                            (t.get("title") or "") + " " + (t.get("notes") or "")
                        ).lower()
                        for q in query_lower
                    )
                ]
                if not filtered:
                    filtered = raw
            else:
                filtered = raw

            results: list[SearchResultV1] = []
            for i, task in enumerate(filtered[:top_k]):
                title = task.get("title") or "(No title)"
                notes = (task.get("notes") or "")[:200]
                status = task.get("status", "needsAction")
                due = task.get("due", "")
                content = f"{title} | due={due} | status={status}"
                if notes:
                    content += f"\n{notes}"
                score = max(0.3, 1.0 - (i * 0.04))

                results.append(
                    SearchResultV1(
                        id=task.get("id", f"task_{i}"),
                        source="tasks",
                        source_class=SourceClass.PERSONAL,
                        title=title,
                        snippet=content,
                        timestamp=due if due else None,
                        scores={"structured": score},
                        methods_used=["structured"],
                        metadata={
                            "task_id": task.get("id"),
                            "list_id": lid,
                            "due": due,
                            "status": status,
                            "notes": notes,
                        },
                        provenance=f"task: {title[:50]}",
                    )
                )
            return results

        return await asyncio.to_thread(_sync_list_tasks)

    def get_source_name(self) -> str:
        return "tasks"

    def get_source_class(self) -> SourceClass:
        return SourceClass.PERSONAL

    def get_supported_methods(self) -> list[str]:
        return ["structured"]

    def get_supported_filters(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_id",
                "type": "string",
                "operators": ["eq"],
                "description": "Google Tasks list ID",
            },
            {
                "name": "show_completed",
                "type": "boolean",
                "operators": ["eq"],
                "description": "Include completed tasks",
            },
        ]
