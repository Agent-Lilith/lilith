Create a search plan to answer the user's request.

Conversation:
{context}

Intent: {intent}
Available backends: {selected_tools}

For each backend, output 2-3 search steps (different query phrasings or filters).
Consider: direct keywords, broader concepts, entities, and time filters if temporal is set.

Return a JSON array of steps. Each step is an object with: "tool", "query", and optional "filters" (object).
Example:
[
  {"tool": "email", "query": "job application TechCorp", "filters": {"date_after": "2024-01-01"}},
  {"tool": "web", "query": "TechCorp company"}
]

Return only the JSON array, no other text.
