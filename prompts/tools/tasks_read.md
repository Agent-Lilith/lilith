## Description

Read the user's Google Tasks. Can list task lists (`action=list_lists`), list tasks within a list (`action=list_tasks`), or get a single task by its ID (`action=get_task`). Optional list_id (default is user's default list).

## Examples

```json
{"tool": "tasks_read", "action": "list_lists"}
{"tool": "tasks_read", "action": "list_tasks", "show_completed": "false"}
{"tool": "tasks_read", "action": "get_task", "task_id": "<task_id>"}
```
