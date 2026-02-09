Rank these search results by relevance to the user's request.

Conversation:
{context}

Results:
{results_summary}

Return a JSON array of indices in order of relevance (most relevant first), e.g. [2, 0, 5, 1].
- Include every index from 0 to N-1 exactly once; put less relevant items at the end rather than omitting them.
- Consider relevance to the query, recency when the request implies it (e.g. "latest", "recent"), and source usefulness (e.g. prefer "email" for emails from someone, "web" for latest news).
Return only the JSON array of integers.
