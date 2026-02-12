## Description

Use when the user wants to search or asks about: **sites they visited**, **latest/recent websites**, **browser history**, **saved bookmarks**, **whatsapp messages**, **whatsapp chats** — or general search across web, email, calendar, tasks. Call this tool; the system injects the conversation so the right sources are queried. **Results include unique IDs; use these IDs when calling other tools like `calendar_read` or `calendar_write` to reference specific items.** Optional: max_results to limit results.

## Examples

```json
{"tool": "universal_search"}
{"tool": "universal_search", "max_results": "15"}
```

When the user says e.g. "latest websites I visited" or "sites I had open", call `{"tool": "universal_search"}` — browser history is searched when configured.
