## Description

Read FULL content of MULTIPLE webpages at once (in parallel). Use after search when the user wants to 'visit all results' or 'open all links': pass the list of URLs from search (comma- or newline-separated). Counts as ONE step. Max 10 URLs per call. Same topic applied to all. Prefer this over calling read_page many times.

## Examples

```json
{"tool": "read_pages", "urls": "https://a.com/1, https://b.com/2", "topic": "key points"}
```
