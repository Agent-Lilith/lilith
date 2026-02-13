## Description

Use this tool **when the user might be asking about their data or their activity** (messages, email, calendar, history, etc.), or when in doubt. You do not need to know the exact source—call the tool and the system will route to the right ones; the current conversation is injected automatically (no query parameter needed). **The search layer can skip itself** (return "no match" or minimal results) when the query doesn't apply, so **prefer calling over concluding you lack access.** Optional: `max_results` to limit results. Results may include IDs for use with other tools (e.g. `calendar_read`, `email_get`). **For multi-step questions** (e.g. "latest person I talked to on X, then latest email from them"): call universal_search again for the next step; the conversation context is sent each time, so the second call will see what you learned and can search the right source (e.g. email). There is no separate email-search tool—always use universal_search for any search.

## Examples

```json
{"tool": "universal_search"}
{"tool": "universal_search", "max_results": "15"}
```
