"""Tasks search backend: list_tasks via Google Tasks, same logic as TasksReadTool."""

import asyncio
from typing import Any

from src.orchestrators.search.interface import SearchTool
from src.orchestrators.search.models import SearchResult


class TasksSearchBackend(SearchTool):
    def __init__(self, google_service: Any):
        self._service = google_service

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if not self._service.is_connected:
            return []
        f = filters or {}
        list_id = (f.get("list_id") or "").strip()
        show_completed = f.get("show_completed")
        if show_completed is None:
            show_completed = True
        query_lower = (query or "").lower().split()

        def _sync_list_tasks() -> list[SearchResult]:
            lid = self._service.get_task_list_id(list_id or None)
            tasks_result = self._service.tasks.tasks().list(
                tasklist=lid,
                showCompleted=bool(show_completed),
            ).execute()
            raw = tasks_result.get("items", [])
            # Optional: filter by query words in title/notes
            if query_lower:
                filtered = [
                    t
                    for t in raw
                    if any(
                        q in ((t.get("title") or "") + " " + (t.get("notes") or "")).lower()
                        for q in query_lower
                    )
                ]
                if not filtered:
                    filtered = raw
            else:
                filtered = raw
            out: list[SearchResult] = []
            for i, task in enumerate(filtered[:top_k]):
                title = task.get("title") or "(No title)"
                notes = (task.get("notes") or "")[:200]
                content = f"{title} | due={task.get('due', '')} | status={task.get('status', 'needsAction')}"
                if notes:
                    content += f"\n{notes}"
                ts = task.get("due")
                score = 1.0 - (i * 0.04)
                if score < 0.3:
                    score = 0.3
                out.append(
                    SearchResult(
                        content=content,
                        source="tasks",
                        title=title,
                        timestamp=ts,
                        metadata={
                            "task_id": task.get("id"),
                            "list_id": lid,
                            "due": task.get("due"),
                            "status": task.get("status", "needsAction"),
                        },
                        relevance_score=score,
                    )
                )
            return out

        return await asyncio.to_thread(_sync_list_tasks)

    def get_source_name(self) -> str:
        return "tasks"

    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        query_lower = query.lower()
        strong = ["task", "tasks", "todo", "todos", "to-do", "to do", "reminder", "due"]
        if any(w in query_lower for w in strong):
            return 0.9
        return 0.3
