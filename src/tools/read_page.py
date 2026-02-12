"""Deep read webpage: httpx → curl_cffi → Crawl4AI → FlareSolverr (only when needed)."""

import asyncio
import html as html_module
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from curl_cffi.requests import AsyncSession as CurlCffiSession

from src.core.config import config
from src.core.logger import logger
from src.core.prompts import get_tool_description, get_tool_examples
from src.core.worker import get_worker
from src.tools.base import Tool, ToolResult

_read_page_cache: dict[str, tuple[str, float]] = {}
_READ_PAGE_CACHE_TTL_S = 300
_READ_PAGE_CACHE_MAX = 50
_WORKER_ERROR_PREFIX = "Error: No valid content to process."
_READ_PAGES_MAX_URLS = 10
_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_HTTPX_TIMEOUT = 15.0
_CRAWL4AI_TIMEOUT = 60.0
_FLARESOLVERR_TIMEOUT = 70.0
_MAX_CONTENT_LEN = 6000

_CF_INDICATORS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "challenge-running",
    "ray id",
    "attention required! | cloudflare",
    "verifying you are human",
)

_JS_SKELETON = re.compile(
    r"<div\s+id=[\"'](?:root|app|__next|main)[\"'][^>]*>\s*</div>", re.I
)
_JS_INITIAL_STATE = re.compile(r"<script[^>]*>[\s\S]*?__INITIAL_STATE__\s*=", re.I)
_JS_NOSCRIPT = re.compile(r"<noscript[^>]*>[\s\S]*?enable\s+javascript", re.I)


def _is_cloudflare_block(
    html_content: str | None, status_code: int | None = None
) -> bool:
    if status_code == 403:
        return True
    if not html_content:
        return False
    lower = html_content.lower()
    return any(ind in lower for ind in _CF_INDICATORS)


def _has_real_content(html_content: str | None, min_text_len: int = 200) -> bool:
    if not html_content or not html_content.strip():
        return False
    if _is_cloudflare_block(html_content):
        return False
    text = _html_to_text(html_content)
    return len(text.strip()) >= min_text_len


def _needs_js_rendering(html_content: str | None) -> bool:
    if not html_content or len(html_content) < 50:
        return False
    if _JS_SKELETON.search(html_content):
        return True
    if _JS_INITIAL_STATE.search(html_content):
        return True
    if _JS_NOSCRIPT.search(html_content):
        return True
    text = _html_to_text(html_content)
    if len(html_content) > 2000 and len(text.strip()) < 300:
        return True
    return False


def _html_to_text(html_content: str, max_chars: int = _MAX_CONTENT_LEN) -> str:
    if not html_content:
        return ""
    s = re.sub(r"<script[\s\S]*?</script>", " ", html_content, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = html_module.unescape(s).strip()
    return s[:max_chars] if max_chars else s


async def _try_httpx(url: str) -> tuple[str | None, str]:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_HTTPX_TIMEOUT
        ) as client:
            r = await client.get(url, headers={"User-Agent": _DEFAULT_UA})
            html = (r.text or "") if r.status_code == 200 else ""
            if r.status_code == 200 and _has_real_content(html):
                return (html, "ok")
            if r.status_code == 403 or _is_cloudflare_block(html, r.status_code):
                return (html or None, "cloudflare")
            if r.status_code == 200 and _needs_js_rendering(html):
                return (html, "js")
            return (html or None, "empty")
    except Exception as e:
        logger.debug(f"httpx failed for {url}: {e}")
        return (None, "empty")


async def _try_curl_cffi(url: str) -> tuple[str | None, str]:
    """Browser impersonation. Returns (html or None, 'ok'|'cloudflare'|'empty')."""
    try:
        async with CurlCffiSession() as session:
            r = await session.get(url, impersonate="chrome", timeout=_HTTPX_TIMEOUT)
            html = (r.text or "") if r.status_code == 200 else ""
            if r.status_code == 200 and _has_real_content(html):
                return (html, "ok")
            if r.status_code == 403 or _is_cloudflare_block(html, r.status_code):
                return (html or None, "cloudflare")
            return (html or None, "empty")
    except Exception as e:
        logger.debug(f"curl_cffi failed for {url}: {e}")
        return (None, "empty")


def _c4ai_text_from(obj: dict) -> str | None:
    """Extract markdown or html from Crawl4AI result."""
    md_obj = obj.get("markdown")
    if isinstance(md_obj, dict):
        out = md_obj.get("raw_markdown") or md_obj.get("fit_markdown")
    elif isinstance(md_obj, str) and md_obj.strip():
        out = md_obj
    else:
        out = None
    if out and "Verifying you are human" not in out and "Just a moment" not in out:
        return out
    return obj.get("html") or obj.get("cleaned_html") or obj.get("fit_html") or None


async def _try_crawl4ai(
    url: str,
    cookies: dict | None = None,
    user_agent: str | None = None,
) -> tuple[str | None, bool]:
    """Returns (markdown_or_html or None, hit_cloudflare)."""
    payload: dict[str, Any] = {
        "urls": [url],
        "browser_config": {
            "user_agent": user_agent or _DEFAULT_UA,
        },
        "crawler_config": {"extracted_content_type": "markdown"},
    }
    if cookies:
        payload["browser_config"]["cookies"] = [
            {"name": k, "value": v, "url": url} for k, v in cookies.items()
        ]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                config.crawl4ai_url + "/crawl",
                json=payload,
                timeout=_CRAWL4AI_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.error(f"Crawl4AI failed for {url}: {e}")
        return (None, False)

    results = data.get("results", [])
    res = results[0] if results and isinstance(results, list) else data
    raw = _c4ai_text_from(res) if isinstance(res, dict) else None
    if not raw or not raw.strip():
        return (None, False)
    hit_cf = "Just a moment" in raw or "Verifying you are human" in raw
    if hit_cf:
        return (None, True)
    return (raw[:_MAX_CONTENT_LEN], False)


async def _try_flaresolverr(url: str) -> tuple[str | None, dict, str | None]:
    """Returns (html or None, cookies_dict, user_agent). Use only when Cloudflare detected."""
    try:
        async with httpx.AsyncClient() as client:
            payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
            r = await client.post(
                config.flaresolverr_url + "/v1",
                json=payload,
                timeout=_FLARESOLVERR_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.debug(f"FlareSolverr failed for {url}: {e}")
        return (None, {}, None)

    if data.get("status") != "ok":
        return (None, {}, None)
    solution = data.get("solution", {})
    cookies = {
        c["name"]: c["value"]
        for c in solution.get("cookies", [])
        if isinstance(c, dict) and "name" in c and "value" in c
    }
    user_agent = solution.get("userAgent")
    html = solution.get("response") or solution.get("html") or ""
    if not html.strip() or _is_cloudflare_block(html):
        return (None, cookies, user_agent)
    return (html, cookies, user_agent)


async def _fetched_then_summarize(
    t0: float, url: str, topic: str, text: str
) -> ToolResult:
    logger.tool_page_fetched(time.monotonic() - t0)
    return await _summarize_and_return(url, topic, text)


async def _fetch_one_url(url: str, topic: str) -> ToolResult:
    """Fetch via httpx → curl_cffi → Crawl4AI → FlareSolverr as needed; then summarize."""
    url = url.strip().rstrip("/")
    t0 = time.monotonic()
    try:
        html, reason = await _try_httpx(url)
        if reason == "ok" and html:
            content = _html_to_text(html, _MAX_CONTENT_LEN)
            return await _fetched_then_summarize(t0, url, topic, content)

        html2, reason2 = await _try_curl_cffi(url)
        if reason2 == "ok" and html2:
            content = _html_to_text(html2, _MAX_CONTENT_LEN)
            return await _fetched_then_summarize(t0, url, topic, content)
        if reason2 == "cloudflare":
            pass
        else:
            markdown, hit_cf = await _try_crawl4ai(url)
            if markdown and markdown.strip():
                return await _fetched_then_summarize(t0, url, topic, markdown)
            if hit_cf:
                pass
            else:
                return ToolResult.fail(f"Could not extract content from {url}.")

        fs_html, cookies, user_agent = await _try_flaresolverr(url)
        if fs_html and _has_real_content(fs_html, min_text_len=100):
            content = _html_to_text(fs_html, _MAX_CONTENT_LEN)
            return await _fetched_then_summarize(t0, url, topic, content)

        if cookies or user_agent:
            markdown, _ = await _try_crawl4ai(
                url, cookies=cookies or None, user_agent=user_agent
            )
            if markdown and markdown.strip():
                return await _fetched_then_summarize(t0, url, topic, markdown)
        if fs_html and _has_real_content(fs_html, min_text_len=100):
            content = _html_to_text(fs_html, _MAX_CONTENT_LEN)
            return await _fetched_then_summarize(t0, url, topic, content)

        return ToolResult.fail(
            f"Could not extract content from {url}. (Cloudflare or blocking; consider a paid proxy service.)"
        )
    except Exception as e:
        logger.error(f"Fetch failed for {url}: {e}")
        return ToolResult.fail(f"Crawl failed for {url}: {str(e)}")


async def _summarize_and_return(url: str, topic: str, content: str) -> ToolResult:
    if not content or not content.strip():
        return ToolResult.fail(f"No valid content to process for {url}.")
    worker = get_worker()
    summary = await worker.process(
        task_description=f"Extract and summarize info from {url}",
        data=content,
        instruction=f"Focus on this topic: {topic}. Keep it under 500 words. Use bullet points.",
    )
    summary = (summary or "").strip()
    if not summary or summary.startswith(_WORKER_ERROR_PREFIX):
        return ToolResult.fail(f"No valid content to process for {url}.")
    return ToolResult.ok(summary)


class ReadPageTool(Tool):
    @property
    def name(self) -> str:
        return "read_page"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "url": "The URL of the webpage to read (e.g. from search results)",
            "topic": "Optional: What specific information should I look for on this page?",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(self, **kwargs: object) -> ToolResult:
        url = str(kwargs.get("url", ""))
        topic = str(kwargs.get("topic", "Summarize the key information"))
        logger.tool_execute(self.name, {"url": url, "topic": topic})
        url_key = url.strip().rstrip("/")
        now = time.time()
        if url_key in _read_page_cache:
            cached_text, cached_at = _read_page_cache[url_key]
            if (
                (now - cached_at) < _READ_PAGE_CACHE_TTL_S
                and cached_text
                and not cached_text.strip().startswith(_WORKER_ERROR_PREFIX)
            ):
                logger.tool_result(self.name, len(cached_text), True)
                return ToolResult.ok(f"(cached) {cached_text}")
        result = await _fetch_one_url(url, topic)
        if result.success and result.output:
            if len(_read_page_cache) >= _READ_PAGE_CACHE_MAX:
                oldest = min(_read_page_cache, key=lambda k: _read_page_cache[k][1])
                del _read_page_cache[oldest]
            _read_page_cache[url_key] = (result.output, now)
            logger.tool_result(self.name, len(result.output), True)
            return result
        logger.tool_result(self.name, 0, False, error_reason=result.error)
        return result


def _parse_urls(urls_str: str) -> list[str]:
    """Split urls by comma or newline, strip, dedupe order, return non-empty."""
    if not urls_str or not urls_str.strip():
        return []
    parts = re.split(r"[\n,]+", urls_str)
    seen = set()
    out = []
    for u in parts:
        u = u.strip().rstrip("/")
        if u and u not in seen and u.startswith("http"):
            seen.add(u)
            out.append(u)
    return out


class ReadPagesTool(Tool):
    """Batch read multiple URLs in parallel (counts as one agent iteration)."""

    @property
    def name(self) -> str:
        return "read_pages"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "urls": "Comma- or newline-separated list of URLs (e.g. from search results). Max 10.",
            "topic": "Optional: What to extract from each page (same for all).",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(self, **kwargs: object) -> ToolResult:
        urls = str(kwargs.get("urls", ""))
        topic = str(kwargs.get("topic", "Summarize the key information"))
        url_list = _parse_urls(urls)
        if not url_list:
            msg = "read_pages requires at least one valid URL in urls."
            logger.tool_result(self.name, 0, False, error_reason=msg)
            return ToolResult.fail(msg)
        if len(url_list) > _READ_PAGES_MAX_URLS:
            url_list = url_list[:_READ_PAGES_MAX_URLS]
            logger.debug(f"read_pages: limited to first {_READ_PAGES_MAX_URLS} URLs")
        logger.tool_execute(self.name, {"urls_count": len(url_list), "topic": topic})
        total = len(url_list)

        def _url_hint(url: str) -> str:
            try:
                netloc = urlparse(url.strip()).netloc
                if netloc:
                    return netloc
            except Exception:
                pass
            return url[:40] if len(url) > 40 else url

        async def _fetch_with_index(i: int, url: str, topic_str: str) -> ToolResult:
            logger.set_page_index(i + 1, total)
            logger.set_page_hint(_url_hint(url))
            return await _fetch_one_url(url, topic_str)

        results = await asyncio.gather(
            *[_fetch_with_index(i, u, topic) for i, u in enumerate(url_list)]
        )
        parts = []
        for u, res in zip(url_list, results):
            short_url = u if len(u) <= 80 else u[:77] + "..."
            if res.success and res.output:
                parts.append(f"--- {short_url} ---\n{res.output}")
            else:
                parts.append(f"--- {short_url} ---\nError: {res.error or 'no content'}")
        combined = "\n\n".join(parts)
        logger.tool_result(self.name, len(combined), True)
        return ToolResult.ok(combined)
