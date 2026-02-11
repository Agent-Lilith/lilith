Create a search plan to answer the user's request.

Conversation:
{context}

Intent: {intent}
Available backends: {selected_tools}

Rules:
- Do not fill "query" when not needed. If the intent is only time/scope (e.g. "recently visited", "history", "last week"), use "query": "" and put the time in "filters". Do not copy the user's phrase into the query. Use "query" only for actual search terms (e.g. "Python tutorials", "TechCorp").
- Temporal intent goes in filters for all backends, not in the query.
- Filter keys: browser = time_range, date_after, date_before, domain, folder. email/calendar = date_after (and similar). Use time_range for relative ranges (e.g. "last_week", "last_month") when supported.

For each backend, output 2-3 search steps (different query phrasings or filters).
Consider: direct keywords, broader concepts, entities, and time filters if temporal is set.

Return a JSON array of steps. Each step is an object with: "tool", "query", and optional "filters" (object).
Example:
[
  {"tool": "email", "query": "job application TechCorp", "filters": {"date_after": "2024-01-01"}},
  {"tool": "web", "query": "TechCorp company"},
  {"tool": "browser", "query": "", "filters": {"time_range": "last_week"}}
]

Return only the JSON array, no other text.
