Given the search results below, extract the main person or contact that should be used for a follow-up search (e.g. "email from this person", "calendar with this person").

## Search results

{results}

## Task

Return exactly one line in the format: `name (email)` if an email is visible, or just `name` if only a name is available.
If multiple people appear, return the person who **sent** the most recent message.
If you cannot determine any name, return `NONE`.

No quotes, no explanation, no markdown.

## Examples

Input: [1] source=whatsapp title='Chat with Alex' snippet=Hey, are you free for lunch?
Output: Alex

Input: [1] source=email title='Project update' provenance=From: Maria Chen <maria@acme.com> snippet=Here are the latest numbers...
Output: Maria Chen (maria@acme.com)

Input: [1] source=email title='Newsletter' provenance=From: noreply@news.com snippet=Weekly digest...
Output: NONE
