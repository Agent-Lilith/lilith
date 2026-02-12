"""Tasks read: list task lists, list tasks, get task by id."""

from src.core.logger import logger
from src.core.prompts import get_tool_description, get_tool_examples
from src.services.google_service import GoogleService
from src.tools.base import Tool, ToolResult


def _format_task_summary(task: dict) -> str:
    title = task.get("title") or "(No title)"
    tid = task.get("id", "")
    status = task.get("status", "needsAction")
    due = task.get("due", "")
    return f"{title} | due={due} | status={status} | id={tid}"


def _format_task_full(task: dict) -> str:
    lines = [
        f"Title: {task.get('title') or '(No title)'}",
        f"ID: {task.get('id', '')}",
        f"Status: {task.get('status', 'needsAction')}",
        f"Due: {task.get('due', '')}",
    ]
    if task.get("notes"):
        lines.append(f"Notes: {task['notes']}")
    return "\n".join(lines)


class TasksReadTool(Tool):
    def __init__(self, google_service: GoogleService):
        self._service = google_service

    @property
    def name(self) -> str:
        return "tasks_read"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "action": "One of: list_lists, list_tasks, get_task",
            "list_id": "Optional. Task list id; omit for default.",
            "task_id": "For get_task only: the task id",
            "show_completed": "For list_tasks: true or false (default true)",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(self, **kwargs: object) -> ToolResult:
        action = str(kwargs.get("action", ""))
        list_id = str(kwargs.get("list_id", ""))
        task_id = str(kwargs.get("task_id", ""))
        show_completed = str(kwargs.get("show_completed", "true"))
        logger.tool_execute(
            self.name,
            {
                "action": action,
                "list_id": list_id,
                "task_id": task_id,
                "show_completed": show_completed,
            },
        )
        import asyncio

        return await asyncio.to_thread(
            self._sync_execute, action, list_id, task_id, show_completed
        )

    def _sync_execute(
        self,
        action: str,
        list_id: str = "",
        task_id: str = "",
        show_completed: str = "true",
    ) -> ToolResult:
        if not self._service.is_connected:
            return ToolResult.fail(
                "Tasks not connected. User should run: python -m src.main google-auth"
            )

        try:
            if action == "list_lists":
                result = self._service.tasks.tasklists().list(maxResults=100).execute()
                items = result.get("items", [])
                if not items:
                    out = "No task lists found."
                else:
                    lines = []
                    for lst in items:
                        marks = []
                        if lst.get("id") == self._service.default_task_list_id:
                            marks.append("default")
                        suffix = f" [{', '.join(marks)}]" if marks else ""
                        lines.append(
                            f"- {lst.get('title', 'No name')} — id: {lst.get('id')}{suffix}"
                        )
                    out = "\n".join(lines)
                return ToolResult.ok(out)

            if action == "list_tasks":
                lid = self._service.get_task_list_id(list_id)
                show = show_completed.strip().lower() not in ("false", "0", "no")

                tasks_result = (
                    self._service.tasks.tasks()
                    .list(tasklist=lid, showCompleted=show)
                    .execute()
                )
                tasks = tasks_result.get("items", [])

                if not tasks:
                    out = "No tasks in that list."
                else:
                    out = "\n".join(_format_task_summary(t) for t in tasks)
                return ToolResult.ok(out)

            if action == "get_task":
                if not task_id:
                    return ToolResult.fail("get_task requires task_id")

                lid = self._service.get_task_list_id(list_id)
                task = (
                    self._service.tasks.tasks()
                    .get(tasklist=lid, task=task_id.strip())
                    .execute()
                )

                if not task:
                    return ToolResult.fail("Task not found.")

                out = _format_task_full(task)
                return ToolResult.ok(out)

            return ToolResult.fail(
                f"Unknown action: {action}. Use list_lists, list_tasks, or get_task."
            )
        except Exception as e:
            logger.error(f"Tasks read failed: {e}", e)
            return ToolResult.fail(str(e))
