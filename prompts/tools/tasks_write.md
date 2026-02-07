## Description

Create, update, or delete Google Tasks and task lists. All update and delete actions require user confirmation. Optional `list_id` (omit for default).
- For tasks: `action` can be `create` (requires `title`), `update` (requires `task_id`), or `delete` (requires `task_id`).
- For lists: `action` can be `create_list` (requires `title`), `update_list` (requires `list_id`, `title`), or `delete_list` (requires `list_id`).

## Examples

```json
{"tool": "tasks_write", "action": "create", "title": "Buy milk"}
{"tool": "tasks_write", "action": "update", "task_id": "<id>", "status": "completed"}
{"tool": "tasks_write", "action": "delete", "task_id": "<id>"}
{"tool": "tasks_write", "action": "create_list", "title": "Shopping"}
{"tool": "tasks_write", "action": "update_list", "list_id": "<id>", "title": "New name"}
{"tool": "tasks_write", "action": "delete_list", "list_id": "<id>"}
```