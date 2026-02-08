## Description

Summarize one or more emails. Provide either thread_id (to summarize a whole thread) or email_ids (comma-separated or JSON array of message IDs). Returns a plain-text human-readable summary.

## Examples

```json
{"tool": "emails_summarize", "thread_id": "<thread_id>"}
{"tool": "emails_summarize", "email_ids": "msg1,msg2,msg3"}
```
