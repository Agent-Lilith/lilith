"""Universal Search orchestrator: intent, plan, execute, refine, rerank."""

import asyncio
import json
import re
from typing import Any

from src.core.logger import logger
from src.core.prompts import load_search_prompt
from src.core.worker import current_llm_client
from src.llm.vllm_client import create_client
from src.observability import traceable
from src.orchestrators.search.interface import SearchTool
from src.orchestrators.search.models import SearchResult, SearchResultItem, UniversalSearchResponse


def _extract_json(text: str) -> str:
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


def _query_terms(text: str) -> set[str]:
    if not text or not text.strip():
        return set()
    normalized = re.sub(r"[^\w\s]", " ", text.lower())
    return {w for w in normalized.split() if len(w) > 1}


def _fallback_rerank(results: list[SearchResult], context: str) -> list[SearchResult]:
    query = _default_query_from_context(context)
    terms = _query_terms(query)
    if not terms:
        return sorted(results, key=lambda r: -r.relevance_score)

    def key(r: SearchResult) -> tuple[float, int]:
        text = (r.title or "") + " " + (r.content or "")
        overlap = sum(1 for t in terms if t in text.lower())
        return (-r.relevance_score, -overlap)

    return sorted(results, key=key)


class UniversalSearchOrchestrator:
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
        logger.set_tool_step("intent")
        try:
            raw = await self._generate(prompt, max_tokens=400)
            return _parse_json_object(
                raw,
                {
                    "intent": "find_information",
                    "entities": [],
                    "temporal": None,
                    "source_hints": [],
                    "scope": "multiple",
                    "ambiguities": [],
                },
            )
        finally:
            logger.set_tool_step(None)

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
        if intent.get("scope") == "single" and scores:
            selected = [max(scores, key=scores.get)]
        else:
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
        logger.set_tool_step("plan")
        try:
            raw = await self._generate(prompt, max_tokens=600)
            plan = _parse_json_array(raw, [])
        finally:
            logger.set_tool_step(None)
        if not plan:
            plan = [{"tool": t, "query": default_query, "filters": {}} for t in selected_tools]
        validated: list[dict[str, Any]] = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            if tool not in self._tools:
                continue
            raw_q = step.get("query")
            if raw_q is not None and str(raw_q).strip() == "":
                q = ""
            else:
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
        logger.set_tool_step("refine")
        try:
            raw = await self._generate(prompt, max_tokens=400)
            plan = _parse_json_array(raw, [])
        finally:
            logger.set_tool_step(None)
        validated: list[dict[str, Any]] = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            if tool not in self._tools:
                continue
            raw_q = step.get("query")
            if raw_q is not None and str(raw_q).strip() == "":
                q = ""
            else:
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
            entry: dict[str, Any] = {
                "index": i,
                "source": r.source,
                "title": r.title[:80],
                "preview": r.content[:280],
                "timestamp": r.timestamp,
            }
            if r.metadata:
                hints = {k: v for k, v in r.metadata.items() if k in ("url", "from", "to") and v}
                if hints:
                    entry["metadata"] = hints
            summary.append(entry)
        prompt = (
            self._prompt_rerank.replace("{context}", context)
            .replace("{results_summary}", json.dumps(summary, indent=2))
        )
        logger.set_tool_step("rerank")
        try:
            raw = await self._generate(prompt, max_tokens=200)
            indices = _parse_json_array(raw, [])
        finally:
            logger.set_tool_step(None)
        validated_indices: list[int] = []
        seen: set[int] = set()
        for x in indices:
            if isinstance(x, int) and 0 <= x < len(to_rank) and x not in seen:
                seen.add(x)
                validated_indices.append(x)
        if not validated_indices:
            logger.warning("rerank returned invalid or empty indices, using fallback sort")
            return _fallback_rerank(results, context)
        if len(validated_indices) < len(to_rank):
            logger.debug(f"rerank returned {len(validated_indices)}/{len(to_rank)} indices, appending missing")
        reranked: list[SearchResult] = [to_rank[idx] for idx in validated_indices]
        for i, r in enumerate(to_rank):
            if i not in seen:
                reranked.append(r)
        return reranked

    @traceable(name="universal_search", run_type="chain")
    async def search(
        self,
        conversation_context: str = "",
        user_message: str = "",
        max_results: int = 20,
        do_refinement: bool = True,
    ) -> UniversalSearchResponse:
        """
        Pipeline (refine does NOT re-run intent/plan):
          intent → plan → execute plan → [refine LLM → if extra steps, execute them] → rerank.
        """
        if user_message and conversation_context:
            context = (user_message.strip() + "\n\n" + conversation_context.strip()).strip()
        else:
            context = (user_message or conversation_context or "").strip()
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
                logger.debug("universal_search › refine returned 0 steps, no extra search")
                break
            logger.debug(f"universal_search › refine returned {len(extra_plan)} steps, executing")
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
