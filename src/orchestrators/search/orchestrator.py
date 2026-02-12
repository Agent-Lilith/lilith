"""Universal Search orchestrator: capability-driven routing, hybrid retrieval, weighted fusion.

Pipeline:
  1. Intent extraction (LLM)
  2. Deterministic routing (router selects sources + methods based on capabilities)
  3. Complexity gate (simple -> skip LLM planning; complex -> LLM plan + confirm)
  4. Dispatch to MCP servers and direct backends
  5. Weighted fusion ranking
  6. Metric-driven refinement (deterministic triggers, optional LLM assist)
  7. Return ranked results
"""

import asyncio
import json
import re
import time
from typing import Any

from src.contracts.mcp_search_v1 import SearchResultV1
from src.core.logger import logger
from src.core.prompts import load_search_prompt
from src.core.worker import current_llm_client
from src.llm.vllm_client import create_client
from src.observability import traceable
from src.orchestrators.search.capabilities import CapabilityRegistry
from src.orchestrators.search.dispatcher import MCPSearchDispatcher
from src.orchestrators.search.fusion import WeightedFusionRanker
from src.orchestrators.search.interface import SearchBackend
from src.orchestrators.search.models import UniversalSearchResponse
from src.orchestrators.search.router import RetrievalRouter, RoutingDecision


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


def _default_query_from_context(context: str) -> str:
    if not context or not context.strip():
        return ""
    first_line = context.strip().split("\n")[0].strip()
    if not first_line:
        return ""
    for prefix in ("User:", "Assistant:", "user:", "assistant:"):
        if first_line.startswith(prefix):
            first_line = first_line[len(prefix) :].strip()
            break
    return first_line[:200] if first_line else ""


class UniversalSearchOrchestrator:
    """Capability-driven search orchestrator with hybrid retrieval and weighted fusion."""

    def __init__(
        self,
        capabilities: CapabilityRegistry,
        dispatcher: MCPSearchDispatcher,
        direct_backends: list[SearchBackend],
        max_refinement_rounds: int = 1,
    ):
        self._capabilities = capabilities
        self._dispatcher = dispatcher
        self._direct_backends = {b.get_source_name(): b for b in direct_backends}
        self._router = RetrievalRouter(capabilities)
        self._fusion = WeightedFusionRanker()
        self._max_refinement_rounds = max(0, max_refinement_rounds)
        self._prompt_intent = load_search_prompt("intent")
        self._prompt_plan = load_search_prompt("plan")
        self._prompt_refine = load_search_prompt("refine")

    def _get_llm(self):
        client = current_llm_client.get()
        if client is not None:
            return client
        return create_client()

    async def _generate(
        self, prompt: str, max_tokens: int = 800, temperature: float = 0.2
    ) -> str:
        client = self._get_llm()
        stop = getattr(getattr(client, "formatter", None), "stop_tokens", None) or [
            "<|eot_id|>",
            "<|end_of_text|>",
        ]
        response = await client.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            stream=False,
        )
        return (getattr(response, "text", None) or str(response)).strip()

    @traceable(name="search_intent_analysis", run_type="chain")
    async def _analyze_intent(self, context: str) -> dict[str, Any]:
        """Extract structured intent from conversation context."""
        prompt = self._prompt_intent.replace("{context}", context)
        logger.set_tool_step("intent")
        try:
            raw = await self._generate(prompt, max_tokens=500)
            return _parse_json_object(
                raw,
                {
                    "intent": "find_information",
                    "entities": [],
                    "temporal": None,
                    "source_hints": [],
                    "complexity": "simple",
                    "retrieval_hints": [],
                    "ambiguities": [],
                },
            )
        finally:
            logger.set_tool_step(None)

    @traceable(name="search_execute_routing", run_type="chain")
    async def _execute_routing(
        self,
        decisions: list[RoutingDecision],
    ) -> tuple[list[SearchResultV1], list[str]]:
        """Execute all routing decisions in parallel."""
        tasks = []
        for decision in decisions:
            tasks.append(self._execute_one(decision))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[SearchResultV1] = []
        all_errors: list[str] = []
        for i, result in enumerate(results_list):
            if isinstance(result, Exception):
                all_errors.append(f"{decisions[i].source}: {result!s}")
                logger.warning("Search failed for %s: %s", decisions[i].source, result)
                continue
            if isinstance(result, tuple):
                results, errors = result
                all_results.extend(results)
                all_errors.extend(errors)

        return all_results, all_errors

    async def _execute_one(
        self,
        decision: RoutingDecision,
    ) -> tuple[list[SearchResultV1], list[str]]:
        """Execute a single routing decision against either MCP or direct backend."""
        source = decision.source
        errors: list[str] = []

        # Try MCP dispatcher first
        if self._dispatcher.has_source(source):
            try:
                results = await self._dispatcher.search(
                    source=source,
                    query=decision.query,
                    methods=decision.methods,
                    filters=decision.filters,
                    top_k=10,
                )
                logger.debug(
                    "MCP search %s: %s results, methods=%s",
                    source,
                    len(results),
                    decision.methods,
                )
                return results, errors
            except Exception as e:
                errors.append(f"{source}: MCP call failed: {e!s}")
                logger.warning("MCP search failed for %s: %s", source, e)
                return [], errors

        # Try direct backend
        backend = self._direct_backends.get(source)
        if backend:
            try:
                results = await backend.search(
                    query=decision.query,
                    methods=decision.methods,
                    filters=decision.filters,
                    top_k=10,
                )
                logger.debug(
                    "Direct search %s: %s results, methods=%s",
                    source,
                    len(results),
                    decision.methods,
                )
                return results, errors
            except Exception as e:
                errors.append(f"{source}: {e!s}")
                logger.warning("Direct search failed for %s: %s", source, e)
                return [], errors

        errors.append(f"No handler for source '{source}'")
        return [], errors

    def _should_refine(
        self,
        results: list[SearchResultV1],
        intent: dict[str, Any],
        routing_plan_decisions: list[RoutingDecision],
    ) -> tuple[bool, str]:
        """Deterministic refinement triggers. Returns (should_refine, reason)."""
        if not results:
            # Explicit filters + simple query = no refinement, just report empty.
            has_filters = any(d.filters for d in routing_plan_decisions)
            complexity = intent.get("complexity", "simple")
            if has_filters and complexity in ("simple", None):
                return False, ""
            return True, "no_results"

        # Trigger 1: Too few results (low recall)
        if len(results) < 3:
            return True, "low_coverage"

        # Trigger 2: Low confidence scores (avg score < 0.7)
        # Note: SearchResultV1 stores dict of scores, we take the max score for that result
        scores = []
        for r in results:
            if r.scores:
                scores.append(max(r.scores.values()))
            else:
                scores.append(0.0)
        avg_score = sum(scores) / len(scores) if scores else 0.0
        if avg_score < 0.7:
            return True, "low_confidence"

        # Trigger 3: Only one source returned results (missing cross-source context)
        # Check if intent implies multiple sources (e.g. source_hints has >1 item)
        sources = {r.source for r in results}
        hints = intent.get("source_hints", [])
        if len(sources) == 1 and len(hints) > 1:
            return True, "single_source"

        # Trigger 4: Results contradict each other (placeholder for now)
        # if self._detect_contradictions(results):
        #    return True, "contradiction"

        return False, ""

    @traceable(name="search_refinement", run_type="chain")
    async def _refine(
        self,
        context: str,
        intent: dict[str, Any],
        results: list[SearchResultV1],
        previous_decisions: list[RoutingDecision],
        reason: str,
    ) -> list[RoutingDecision]:
        """Generate refinement decisions. Uses deterministic adjustments + optional LLM."""
        refined: list[RoutingDecision] = []

        if reason == "no_results":
            # Broaden: retry all sources with vector + structured search and no filters
            for d in previous_decisions:
                # Include structured to get latest items even if query doesn't match semantically
                methods = ["vector"]
                if self._capabilities.can_handle(d.source, "structured"):
                    methods.append("structured")

                refined.append(
                    RoutingDecision(
                        source=d.source,
                        methods=methods,
                        query=d.query,
                        filters=[],  # drop all filters
                    )
                )

        elif reason == "low_source_coverage":
            # Retry missing sources
            actual_sources = {r.source for r in results}
            for d in previous_decisions:
                if d.source not in actual_sources:
                    refined.append(
                        RoutingDecision(
                            source=d.source,
                            methods=d.methods,
                            query=d.query,
                            filters=[],  # relax filters
                        )
                    )

        elif reason == "low_confidence":
            # Try different methods
            for d in previous_decisions:
                new_methods = []
                if "fulltext" not in d.methods and self._capabilities.can_handle(
                    d.source, "fulltext"
                ):
                    new_methods.append("fulltext")
                if "vector" not in d.methods and self._capabilities.can_handle(
                    d.source, "vector"
                ):
                    new_methods.append("vector")
                if new_methods:
                    refined.append(
                        RoutingDecision(
                            source=d.source,
                            methods=new_methods,
                            query=d.query,
                            filters=d.filters,
                        )
                    )

        logger.info(
            "Refinement (reason=%s): %s additional decisions",
            reason,
            len(refined),
        )
        return refined[:4]  # Cap at 4 refinement steps

    @traceable(name="universal_search", run_type="chain")
    async def search(
        self,
        conversation_context: str = "",
        user_message: str = "",
        max_results: int = 20,
        do_refinement: bool = True,
    ) -> UniversalSearchResponse:
        """Main search pipeline.

        1. Build context
        2. Analyze intent (LLM)
        3. Route (deterministic, capability-driven)
        4. Complexity gate (simple = skip LLM plan, complex = use plan)
        5. Execute searches in parallel
        6. Weighted fusion ranking
        7. Metric-driven refinement (if needed)
        8. Return results
        """
        pipeline_start = time.monotonic()

        # 1. Build context
        if user_message and conversation_context:
            context = (
                user_message.strip() + "\n\n" + conversation_context.strip()
            ).strip()
        else:
            context = (user_message or conversation_context or "").strip()

        if not context:
            return UniversalSearchResponse(
                results=[],
                errors=["No context provided for search."],
                meta={
                    "query": "",
                    "sources_queried": [],
                    "methods_used": [],
                    "iterations": 0,
                    "total_results": 0,
                    "complexity": "simple",
                    "timing_ms": {},
                },
            )

        query = _default_query_from_context(context)
        errors: list[str] = []
        timing_ms: dict[str, float] = {}

        # 2. Intent analysis (LLM)
        t0 = time.monotonic()
        intent = await self._analyze_intent(context)
        timing_ms["intent"] = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "Intent: %s | entities=%s | temporal=%s | hints=%s | complexity=%s",
            intent.get("intent"),
            intent.get("entities"),
            intent.get("temporal"),
            intent.get("source_hints"),
            intent.get("complexity"),
        )

        # 3. Route (deterministic)
        t0 = time.monotonic()
        routing_plan = self._router.route(intent, query)
        timing_ms["routing"] = round((time.monotonic() - t0) * 1000, 1)

        if not routing_plan.decisions:
            return UniversalSearchResponse(
                results=[],
                errors=["No search backends available for this query"],
                meta={
                    "query": query,
                    "sources_queried": [],
                    "methods_used": [],
                    "iterations": 0,
                    "total_results": 0,
                    "complexity": routing_plan.complexity,
                    "timing_ms": timing_ms,
                },
            )

        # 4. Complexity gate
        # Simple queries: execute routing directly, no LLM plan
        # Complex queries: could use LLM planner, but for now we trust the router
        # (LLM planning is optional enhancement for complex multi-hop queries)

        # 5. Execute searches
        t0 = time.monotonic()
        all_results, exec_errors = await self._execute_routing(routing_plan.decisions)
        timing_ms["execution"] = round((time.monotonic() - t0) * 1000, 1)
        errors.extend(exec_errors)

        # 6. Determine if query is personal
        is_personal = self._is_personal_query(intent)

        # 7. Refinement loop
        iterations = 1
        notes: list[str] = []
        if do_refinement:
            for _ in range(self._max_refinement_rounds):
                should_refine, reason = self._should_refine(
                    all_results,
                    intent,
                    routing_plan.decisions,
                )
                if not should_refine:
                    # Note when we have explicit filters with no results
                    if not all_results:
                        has_filters = any(d.filters for d in routing_plan.decisions)
                        if has_filters:
                            notes.append("No data found for the requested criteria.")
                    break

                iterations += 1
                t0 = time.monotonic()
                refined_decisions = await self._refine(
                    context,
                    intent,
                    all_results,
                    routing_plan.decisions,
                    reason,
                )
                if not refined_decisions:
                    break

                refined_results, refined_errors = await self._execute_routing(
                    refined_decisions
                )
                all_results.extend(refined_results)
                errors.extend(refined_errors)
                timing_ms["refinement"] = round((time.monotonic() - t0) * 1000, 1)

        # 8. Fusion ranking
        t0 = time.monotonic()
        ranked = self._fusion.fuse_and_rank(
            all_results,
            is_personal_query=is_personal,
            max_results=max_results,
        )
        timing_ms["fusion"] = round((time.monotonic() - t0) * 1000, 1)

        # Collect metadata
        sources_queried = list({d.source for d in routing_plan.decisions})
        methods_used = list({m for d in routing_plan.decisions for m in d.methods})
        timing_ms["total"] = round((time.monotonic() - pipeline_start) * 1000, 1)

        logger.info(
            "Search complete: %s results | sources=%s | methods=%s | iterations=%s | total=%.0fms",
            len(ranked),
            sources_queried,
            methods_used,
            iterations,
            timing_ms["total"],
        )

        return UniversalSearchResponse(
            results=ranked,
            errors=errors,
            notes=notes,
            meta={
                "query": query,
                "sources_queried": sources_queried,
                "methods_used": methods_used,
                "iterations": iterations,
                "total_results": len(ranked),
                "complexity": routing_plan.complexity,
                "timing_ms": timing_ms,
            },
        )

    def _is_personal_query(self, intent: dict[str, Any]) -> bool:
        """Determine if this is a personal data query (vs web/general knowledge)."""
        hints = intent.get("source_hints") or []
        hints_str = " ".join(str(h).lower() for h in hints)

        # Explicitly web
        if any(w in hints_str for w in ("web", "news", "search")):
            return False

        # Explicitly personal
        if any(
            w in hints_str
            for w in (
                "email",
                "calendar",
                "tasks",
                "browser_history",
                "browser_bookmarks",
                "history",
                "bookmark",
            )
        ):
            return True

        # Default: personal (assistant is primarily a personal data tool)
        return True
