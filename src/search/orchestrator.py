"""Universal Search orchestrator: intent, plan, execute, refine, rerank."""

import asyncio
import json
import re
from typing import Any

from src.core.logger import logger
from src.core.prompts import load_search_prompt
from src.core.worker import current_llm_client
from src.llm.vllm_client import create_client
from src.search.interface import SearchTool
from src.search.models import SearchResult, SearchResultItem, UniversalSearchResponse


def _extract_json(text: str) -> str:
    """Take first ```json ... ``` block or bare JSON from text."""
    text = (text or "").strip()
    if not text:
        return "{}"
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_json_object(text: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        cleaned = _extract_json(text)
        cleaned = re.sub(r",\s*}", "}", cleaned)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return default


def _parse_json_array(text: str, default: list[Any]) -> list[Any]:
    try:
        cleaned = _extract_json(text)
        cleaned = re.sub(r",\s*}", "}", cleaned)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        out = json.loads(cleaned)
        return out if isinstance(out, list) else default
    except (json.JSONDecodeError, TypeError):
        return default


def _default_query_from_context(context: str) -> str:
    """One-line summary for meta.query; strip role prefixes so it stays human-readable."""
    if not context or not context.strip():
        return "search"
    first_line = context.strip().split("\n")[0].strip()
    if not first_line:
        return "search"
    for prefix in ("User:", "Assistant:", "user:", "assistant:"):
        if first_line.startswith(prefix):
            first_line = first_line[len(prefix):].strip()
            break
    return first_line[:200] if first_line else "search"


class UniversalSearchOrchestrator:
    """Runs intent analysis, tool selection, search plan, execution, optional refinement, and rerank."""

    def __init__(self, tools: list[SearchTool], max_refinement_rounds: int = 1):
        self._tools = {t.get_source_name(): t for t in tools}
        self._max_refinement_rounds = max(0, max_refinement_rounds)
        self._prompt_intent = load_search_prompt("intent")
        self._prompt_plan = load_search_prompt("plan")
        self._prompt_refine = load_search_prompt("refine")
        self._prompt_rerank = load_search_prompt("rerank")

    def _get_llm(self):
        client = current_llm_client.get()
        if client is not None:
            return client
        return create_client()

    async def _generate(self, prompt: str, max_tokens: int = 800, temperature: float = 0.2) -> str:
        client = self._get_llm()
        stop = getattr(getattr(client, "formatter", None), "stop_tokens", None) or ["<|eot_id|>", "<|end_of_text|>"]
        response = await client.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            stream=False,
        )
        return (getattr(response, "text", None) or str(response)).strip()

    async def _analyze_intent(self, context: str) -> dict[str, Any]:
        prompt = self._prompt_intent.replace("{context}", context)
        raw = await self._generate(prompt, max_tokens=400)
        return _parse_json_object(
            raw,
            {
                "intent": "find_information",
                "entities": [],
                "temporal": None,
                "source_hints": [],
                "ambiguities": [],
            },
        )

    def _select_tools(self, context: str, intent: dict[str, Any]) -> list[str]:
        scores: dict[str, float] = {}
        for name, tool in self._tools.items():
            scores[name] = tool.can_handle_query(context, intent)
        source_hints = (intent.get("source_hints") or []) or []
        hints_str = " ".join(str(h).lower() for h in source_hints)
        if "news" in hints_str or "web" in hints_str:
            for name in ("calendar", "tasks", "email"):
                if name in scores:
                    scores[name] = 0.0
        selected = [n for n, s in scores.items() if s > 0.6]
        if not selected:
            ordered = sorted(scores.items(), key=lambda x: -x[1])
            selected = [n for n, _ in ordered[:2]]
        logger.debug(f"Universal search selected tools: {selected} (scores: {scores})")
        return selected

    async def _create_search_plan(
        self,
        context: str,
        intent: dict[str, Any],
        selected_tools: list[str],
    ) -> list[dict[str, Any]]:
        default_query = _default_query_from_context(context)
        prompt = (
            self._prompt_plan.replace("{context}", context)
            .replace("{intent}", json.dumps(intent, indent=2))
            .replace("{selected_tools}", ", ".join(selected_tools))
        )
        raw = await self._generate(prompt, max_tokens=600)
        plan = _parse_json_array(raw, [])
        if not plan:
            plan = [{"tool": t, "query": default_query, "filters": {}} for t in selected_tools]
        validated: list[dict[str, Any]] = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            if tool not in self._tools:
                continue
            q = str(step.get("query", default_query)).strip() or default_query
            validated.append({
                "tool": tool,
                "query": q,
                "filters": step.get("filters") if isinstance(step.get("filters"), dict) else {},
            })
        if not validated:
            validated = [{"tool": t, "query": default_query, "filters": {}} for t in selected_tools]
        logger.debug(f"Search plan: {len(validated)} steps")
        return validated

    async def _execute_step(self, step: dict[str, Any]) -> tuple[list[SearchResult], list[str]]:
        tool_name = step["tool"]
        query = step["query"]
        filters = step.get("filters") or {}
        tool = self._tools.get(tool_name)
        if not tool:
            return [], [f"Unknown tool: {tool_name}"]
        try:
            results = await tool.search(query, top_k=10, filters=filters)
            return results, []
        except Exception as e:
            logger.warning(f"Search step failed {tool_name}: {e}")
            return [], [f"{tool_name}: {e!s}"]

    async def _execute_plan(self, plan: list[dict[str, Any]]) -> tuple[list[SearchResult], list[str]]:
        tasks = [self._execute_step(step) for step in plan]
        out = await asyncio.gather(*tasks, return_exceptions=True)
        all_results: list[SearchResult] = []
        all_errors: list[str] = []
        for i, result in enumerate(out):
            if isinstance(result, Exception):
                all_errors.append(f"Step {i + 1}: {result!s}")
                continue
            results, errors = result
            all_results.extend(results)
            all_errors.extend(errors)
        return all_results, all_errors

    async def _refine_plan(
        self,
        context: str,
        intent: dict[str, Any],
        results_so_far: list[SearchResult],
        previous_steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        default_query = _default_query_from_context(context)
        summary_lines = []
        for i, r in enumerate(results_so_far[:15]):
            summary_lines.append(f"{i}: [{r.source}] {r.title[:60]} | {r.content[:120]}...")
        results_summary = "\n".join(summary_lines) if summary_lines else "No results yet."
        prompt = (
            self._prompt_refine.replace("{context}", context)
            .replace("{intent}", json.dumps(intent, indent=2))
            .replace("{results_summary}", results_summary)
            .replace("{previous_steps}", json.dumps(previous_steps[:10]))
        )
        raw = await self._generate(prompt, max_tokens=400)
        plan = _parse_json_array(raw, [])
        validated: list[dict[str, Any]] = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            if tool not in self._tools:
                continue
            q = str(step.get("query", default_query)).strip() or default_query
            validated.append({
                "tool": tool,
                "query": q,
                "filters": step.get("filters") if isinstance(step.get("filters"), dict) else {},
            })
        return validated[:4]

    async def _rerank(self, results: list[SearchResult], context: str) -> list[SearchResult]:
        if not results:
            return []
        if len(results) <= 1:
            return results
        to_rank = results[:30]
        summary = []
        for i, r in enumerate(to_rank):
            summary.append({
                "index": i,
                "source": r.source,
                "title": r.title[:80],
                "preview": r.content[:180],
                "timestamp": r.timestamp,
            })
        prompt = (
            self._prompt_rerank.replace("{context}", context)
            .replace("{results_summary}", json.dumps(summary, indent=2))
        )
        raw = await self._generate(prompt, max_tokens=200)
        indices = _parse_json_array(raw, [])
        if not indices:
            return sorted(results, key=lambda x: -x.relevance_score)
        seen: set[int] = set()
        reranked: list[SearchResult] = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(to_rank) and idx not in seen:
                seen.add(idx)
                reranked.append(to_rank[idx])
        for i, r in enumerate(to_rank):
            if i not in seen:
                reranked.append(r)
        return reranked

    async def search(
        self,
        conversation_context: str = "",
        user_message: str = "",
        max_results: int = 20,
        do_refinement: bool = True,
    ) -> UniversalSearchResponse:
        """
        Run search using full context (same information the agent has).
        conversation_context: recent messages (injected by agent loop).
        user_message: last human message (injected). Used if conversation_context is empty.
        """
        context = (conversation_context or user_message or "").strip()
        if not context:
            return UniversalSearchResponse(
                results=[],
                errors=["No context provided for search."],
                meta={
                    "query": "",
                    "sources_queried": [],
                    "iterations": 0,
                    "total_results": 0,
                },
            )

        errors: list[str] = []
        sources_queried: list[str] = []
        all_steps: list[dict[str, Any]] = []
        all_results: list[SearchResult] = []

        intent = await self._analyze_intent(context)
        selected = self._select_tools(context, intent)
        if not selected:
            return UniversalSearchResponse(
                results=[],
                errors=["No search backends available"],
                meta={
                    "query": _default_query_from_context(context),
                    "sources_queried": [],
                    "iterations": 0,
                    "total_results": 0,
                },
            )

        plan = await self._create_search_plan(context, intent, selected)
        all_steps.extend(plan)
        results_batch, errs = await self._execute_plan(plan)
        all_results.extend(results_batch)
        errors.extend(errs)
        for s in plan:
            if s["tool"] not in sources_queried:
                sources_queried.append(s["tool"])

        refinement_rounds = 0
        while do_refinement and refinement_rounds < self._max_refinement_rounds:
            refinement_rounds += 1
            extra_plan = await self._refine_plan(context, intent, all_results, all_steps)
            if not extra_plan:
                break
            all_steps.extend(extra_plan)
            ref_results, ref_errors = await self._execute_plan(extra_plan)
            all_results.extend(ref_results)
            errors.extend(ref_errors)

        if not all_results:
            return UniversalSearchResponse(
                results=[],
                errors=errors,
                meta={
                    "query": _default_query_from_context(context),
                    "sources_queried": list(dict.fromkeys(sources_queried)),
                    "iterations": 1 + refinement_rounds,
                    "total_results": 0,
                },
            )

        reranked = await self._rerank(all_results, context)
        capped = reranked[:max_results]
        items = [r.to_item() for r in capped]

        return UniversalSearchResponse(
            results=items,
            errors=errors,
            meta={
                "query": _default_query_from_context(context),
                "sources_queried": list(dict.fromkeys(sources_queried)),
                "iterations": 1 + refinement_rounds,
                "total_results": len(items),
            },
        )
