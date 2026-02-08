Analyze this conversation and extract structured information about what the user wants to find or check.

Conversation:
{context}

Return a single JSON object with exactly these keys:
- intent: string (e.g. find_information, check_status, get_update)
- entities: list of strings (people, companies, topics mentioned)
- temporal: string or null (e.g. "recent", "last week", "2024-01-01")
- source_hints: list of strings (e.g. "email", "web", "news", "calendar", "tasks")
- ambiguities: list of strings (what is unclear)

Return only valid JSON, no other text.
