## Description

Use when the user wants to search (web, email, calendar, tasks). You only decide that search is needed and call this tool; you do not need to provide a query. The system injects the full conversation so the search runs on the same context you see. Optional: set max_results to limit how many results are returned.

## Examples

```json
{"tool": "universal_search"}
{"tool": "universal_search", "max_results": "15"}
```
