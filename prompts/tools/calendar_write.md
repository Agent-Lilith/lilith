## Description

Create, update, or delete a Google Calendar event. `action=create` requires `title`, `start`, and `end`. `action=update` and `action=delete` require an `event_id`. Update and delete actions require user confirmation. Optional `calendar_id` (omit for default).

## Examples

```json
{"tool": "calendar_write", "action": "create", "title": "Doctor appointment", "start": "2026-02-04T17:00:00", "end": "2026-02-04T18:00:00"}
{"tool": "calendar_write", "action": "update", "event_id": "<id_from_create>", "title": "New title"}
{"tool": "calendar_write", "action": "delete", "event_id": "<id_from_create>"}
```