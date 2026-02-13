## Description

Search across your personal data (email, WhatsApp, calendar, browser history, tasks) and the web.

- **No query parameter needed** -- conversation context is injected automatically.
- **Routing is automatic** -- the system picks the right data sources.
- **Prefer calling over skipping** -- the search layer returns "no match" when nothing applies, so try it before concluding you lack access.
- **For multi-step lookups**, call universal_search again for the next step; conversation context carries forward so it knows what you already found.
- There is no separate email-search tool -- always use universal_search for any search.
- Optional: `max_results` to cap output. Results may include IDs for use with other tools (e.g. `email_get`, `calendar_read`).

## Examples

```json
{"tool": "universal_search"}
{"tool": "universal_search", "max_results": "15"}
```
