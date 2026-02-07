## Description

Read the FULL content of a webpage (not just a snippet). Use this when you need what a page actually says — e.g. after search: take URLs from search results and call read_page for each link you need. If read_page fails for a URL, try a different URL from search or use the snippets; do not call read_page again for the same URL. Handles bot protection and can summarize by topic.

## Examples

```json
{"tool": "read_page", "url": "https://example.com/article", "topic": "main claims"}
```
