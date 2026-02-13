## Description

Get a full email thread by Gmail thread ID. Returns thread_id, subject, message_count, and messages array. Use after universal_search when the user wants the full conversation (use thread_id from results).

## Examples

```json
{"tool": "email_get_thread", "thread_id": "<thread_id>"}
```
