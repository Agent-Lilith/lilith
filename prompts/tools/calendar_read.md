## Description

Read the user's Google Calendar. Can list calendars (`action=list_calendars`), list events in a time range (`action=list_events`), or get a single event by its ID (`action=get_event`). Optional `calendar_id` (default is user's default calendar).

## Examples

```json
{"tool": "calendar_read", "action": "list_calendars"}
{"tool": "calendar_read", "action": "list_events", "range_preset": "next_7_days"}
{"tool": "calendar_read", "action": "get_event", "event_id": "<event_id>"}
```
