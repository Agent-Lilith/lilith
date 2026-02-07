"""Web search via SearXNG."""

import httpx
from src.core.config import config
from src.core.logger import logger
from src.core.prompts import get_tool_description, get_tool_examples
from src.tools.base import Tool, ToolResult

class SearchTool(Tool):
    @property
    def name(self) -> str:
        return "search"
    
    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "query": "The search query string"
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(self, query: str) -> ToolResult:
        logger.tool_execute(self.name, {"query": query})
        
        try:
            search_url = config.searxng_url
            if not search_url.endswith("/search"):
                search_url = search_url.rstrip("/") + "/search"

            params = {
                "q": query,
                "format": "json",
                "language": "en-US",
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    search_url,
                    params=params,
                    timeout=10.0,
                    follow_redirects=True
                )
                response.raise_for_status()
                
                data = response.json()
            results = data.get("results", [])
            
            if not results:
                logger.tool_result(self.name, 0, True)
                return ToolResult.ok("No results found for that query.")
            header = "Snippets below — NOT full articles. To use a page's content, call read_page with its URL.\n"
            formatted_results = []
            for i, res in enumerate(results[:10]):
                title = res.get("title", "No Title")
                content = res.get("content", "No content available")
                url = res.get("url", "#")
                formatted_results.append(
                    f"[{i+1}] {title}\n    Snippet: {content}\n    URL (for read_page): {url}"
                )
            output = header + "\n\n".join(formatted_results)
            
            logger.tool_result(self.name, len(output), True)
            return ToolResult.ok(output)
            
        except Exception as e:
            logger.error(f"Search tool failed: {str(e)}", e)
            return ToolResult.fail(f"Search failed: {str(e)}")
