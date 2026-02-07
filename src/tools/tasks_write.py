"""Tasks write: create (immediate), update/delete (confirmation)."""

import json
from src.core.prompts import get_tool_description, get_tool_examples
from src.core.config import config
from src.core.logger import logger
from src.services.google_service import GoogleService
from src.tools.base import Tool, ToolResult, format_confirm_required

def _parse_due(s: str) -> str:
    """Return RFC 3339 due string for Tasks API, or empty."""
    s = (s or "").strip()
    if not s:
        return ""
    tz = config.user_timezone or "UTC"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s}T00:00:00.000Z"
    if "T" in s:
        if s.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", s):
            return s if "Z" in s or "." in s else s.replace("Z", ".000Z")
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(tz))
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except Exception:
            return s
    return f"{s}T00:00:00.000Z" if len(s) == 10 else ""


class TasksWriteTool(Tool):
    def __init__(self, google_service: GoogleService) -> None:
        self._service = google_service
        self._pending: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "tasks_write"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "action": "One of: create, update, delete, create_list, update_list, delete_list",
            "task_id": "Required for task update and delete",
            "list_id": "Required for update_list and delete_list; optional for task create/update/delete (default list).",
            "title": "Task or list title (required for create, create_list, update_list)",
            "notes": "Optional task notes",
            "due": "Optional due date (ISO or YYYY-MM-DD)",
            "status": "For task update: needsAction or completed",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(
        self,
        action: str,
        task_id: str = "",
        list_id: str = "",
        title: str = "",
        notes: str = "",
        due: str = "",
        status: str = "",
        confirm_pending_id: str = "",
    ) -> ToolResult:
        if confirm_pending_id and confirm_pending_id.strip():
            import asyncio
            return await asyncio.to_thread(self.execute_pending, confirm_pending_id.strip())
        
        all_args = {
            "action": action, "task_id": task_id, "list_id": list_id,
            "title": title, "notes": notes, "due": due, "status": status
        }
        log_args = {k: v for k, v in all_args.items() if v}
        logger.tool_execute(self.name, log_args)
        
        import asyncio
        return await asyncio.to_thread(
            self._sync_execute, action, task_id, list_id, title, notes, due, status
        )

    def _sync_execute(
        self,
        action: str,
        task_id: str = "",
        list_id: str = "",
        title: str = "",
        notes: str = "",
        due: str = "",
        status: str = "",
    ) -> ToolResult:
        if not self._service.is_connected:
            return ToolResult.fail("Tasks not connected. User should run: python -m src.main google-auth")
        
        try:
            if action == "create":
                if not title:
                    return ToolResult.fail("create requires title.")
                lid = self._service.get_task_list_id(list_id)
                body = {"title": title.strip()}
                if notes: body["notes"] = notes.strip()
                if due: body["due"] = _parse_due(due)
                task = self._service.tasks.tasks().insert(tasklist=lid, body=body).execute()
                tid = task.get("id", "")
                payload = json.dumps({"id": tid, "title": task.get("title", title)})
                out = f"Task created: {task.get('title', title)}. Use task_id for update/delete. {payload}"
                return ToolResult.ok(out)

            if action == "update":
                if not task_id:
                    return ToolResult.fail("update requires task_id.")
                body: dict[str, Any] = {}
                if title: body["title"] = title.strip()
                if notes is not None: body["notes"] = notes.strip()
                if due: body["due"] = _parse_due(due)
                if status and status.lower() in ("needsaction", "completed"): body["status"] = status.lower()
                if not body:
                    return ToolResult.fail("update requires at least one field (title, notes, due, status).")
                
                lid = self._service.get_task_list_id(list_id)
                existing = self._service.tasks.tasks().get(tasklist=lid, task=task_id.strip()).execute()
                summary_title = (existing or {}).get("title", "task")
                pending_id = str(uuid.uuid4())
                self._pending[pending_id] = {"action": "update", "list_id": lid, "task_id": task_id.strip(), "body": body}
                summary_msg = f"Update task '{summary_title}' with the requested changes?"
                out = f"I need your confirmation to update this task. {format_confirm_required(self.name, pending_id, summary_msg)}"
                return ToolResult.ok(out)

            if action == "delete":
                if not task_id:
                    return ToolResult.fail("delete requires task_id.")
                lid = self._service.get_task_list_id(list_id)
                existing = self._service.tasks.tasks().get(tasklist=lid, task=task_id.strip()).execute()
                summary_title = (existing or {}).get("title", "this task")
                pending_id = str(uuid.uuid4())
                self._pending[pending_id] = {"action": "delete", "list_id": lid, "task_id": task_id.strip(), "body": None}
                summary_msg = f"Delete task '{summary_title}'?"
                out = f"I need your confirmation to delete this task. {format_confirm_required(self.name, pending_id, summary_msg)}"
                return ToolResult.ok(out)

            if action == "create_list":
                if not title:
                    return ToolResult.fail("create_list requires title.")
                lst = self._service.tasks.tasklists().insert(body={"title": title.strip()}).execute()
                lid_out = lst.get("id", "")
                out = f"Task list created: {lst.get('title', title)}. list_id={lid_out}"
                return ToolResult.ok(out)

            if action == "update_list":
                if not list_id: return ToolResult.fail("update_list requires list_id.")
                if not title: return ToolResult.fail("update_list requires title.")
                existing = self._service.tasks.tasklists().get(tasklist=list_id.strip()).execute()
                summary_title = (existing or {}).get("title", "list")
                pending_id = str(uuid.uuid4())
                self._pending[pending_id] = {"action": "update_list", "list_id": list_id.strip(), "title": title.strip()}
                summary_msg = f"Rename task list to '{title.strip()}' (was '{summary_title}')?"
                out = f"I need your confirmation to update this task list. {format_confirm_required(self.name, pending_id, summary_msg)}"
                return ToolResult.ok(out)

            if action == "delete_list":
                if not list_id: return ToolResult.fail("delete_list requires list_id.")
                existing = self._service.tasks.tasklists().get(tasklist=list_id.strip()).execute()
                summary_title = (existing or {}).get("title", "this list")
                pending_id = str(uuid.uuid4())
                self._pending[pending_id] = {"action": "delete_list", "list_id": list_id.strip()}
                summary_msg = f"Delete task list '{summary_title}' and all its tasks?"
                out = f"I need your confirmation to delete this task list. {format_confirm_required(self.name, pending_id, summary_msg)}"
                return ToolResult.ok(out)

            return ToolResult.fail(f"Unknown action: {action}.")
        except Exception as e:
            logger.error(f"Tasks write failed: {e}", e)
            return ToolResult.fail(str(e))

    def execute_pending(self, pending_id: str) -> ToolResult:
        if pending_id not in self._pending:
            return ToolResult.fail("Confirmation expired or invalid.")
        
        payload = self._pending.pop(pending_id)
        action = payload["action"]
        
        if not self._service.is_connected:
            self._pending[pending_id] = payload
            return ToolResult.fail("Tasks not connected.")
        
        try:
            if action == "update":
                task = self._service.tasks.tasks().patch(tasklist=payload["list_id"], task=payload["task_id"], body=payload["body"]).execute()
                out = f"Task updated: {task.get('title', payload['task_id'])}"
            elif action == "delete":
                self._service.tasks.tasks().delete(tasklist=payload["list_id"], task=payload["task_id"]).execute()
                out = "Task deleted."
            elif action == "update_list":
                lst = self._service.tasks.tasklists().patch(tasklist=payload["list_id"], body={"title": payload["title"]}).execute()
                out = f"Task list renamed to: {lst.get('title', payload['title'])}"
            elif action == "delete_list":
                self._service.tasks.tasklists().delete(tasklist=payload["list_id"]).execute()
                out = "Task list deleted."
            else:
                self._pending[pending_id] = payload
                return ToolResult.fail(f"Unknown pending action: {action}")
            
            logger.tool_result(self.name, len(out), True)
            return ToolResult.ok(out)
        except Exception as e:
            logger.error(f"Tasks execute_pending failed: {e}", e)
            self._pending[pending_id] = payload
            return ToolResult.fail(str(e))
