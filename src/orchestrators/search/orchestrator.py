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

from src.contracts.mcp_search_v1 import AggregateGroup, SearchMode, SearchResultV1
from src.core.logger import logger
from src.core.prompts import load_search_prompt
from src.core.worker import current_llm_client
from src.llm.vllm_client import create_client
from src.observability import traceable
from src.orchestrators.search.capabilities import CapabilityRegistry
from src.orchestrators.search.constants import (
    IntentComplexity,
    RefinementReason,
    RoutingComplexity,
)
from src.orchestrators.search.dispatcher import DispatcherResult, MCPSearchDispatcher
from src.orchestrators.search.entity_extraction import extract_entity
from src.orchestrators.search.fusion import WeightedFusionRanker
from src.orchestrators.search.intent_modules import DeterministicIntentAnalyzer
from src.orchestrators.search.interface import SearchBackend
from src.orchestrators.search.models import UniversalSearchResponse
from src.orchestrators.search.router import RetrievalRouter, RoutingDecision


def _validate_retrieval_plan(
    plan: Any, available_sources: set[str]
) -> list[dict[str, Any]] | None:
    """Return plan if valid (2+ steps, each source in available_sources). Else None."""
    if not plan or not isinstance(plan, list) or len(plan) < 2:
        return None
    for step in plan:
        if not isinstance(step, dict):
            return None
        sources = step.get("sources")
        if not sources or not isinstance(sources, list):
            return None
        for s in sources:
            if s not in available_sources:
                return None
    return plan


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


def _intent_complexity(value: Any) -> IntentComplexity:
    """Normalize arbitrary intent complexity payload to known enum values."""
    try:
        return IntentComplexity(str(value or IntentComplexity.SIMPLE).strip().lower())
    except ValueError:
        return IntentComplexity.SIMPLE


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
        self._intent_analyzer = DeterministicIntentAnalyzer()
        self._fusion = WeightedFusionRanker()
        self._max_refinement_rounds = max(0, max_refinement_rounds)
        self._prompt_intent = load_search_prompt("intent")
        self._prompt_plan = load_search_prompt("plan")
        self._prompt_refine = load_search_prompt("refine")

    def _cap_broad_decisions(
        self, decisions: list[RoutingDecision], max_sources: int = 3
    ) -> list[RoutingDecision]:
        """Cap broad default routing to control latency while still searching."""
        if not decisions:
            return []
        personal = set(self._capabilities.personal_sources())
        ordered = sorted(
            decisions,
            key=lambda d: (0 if d.source in personal else 1, d.source),
        )
        capped: list[RoutingDecision] = []
        for d in ordered[: max(1, max_sources)]:
            preferred_methods: list[str] = []
            if "structured" in d.methods:
                preferred_methods.append("structured")
            if "fulltext" in d.methods:
                preferred_methods.append("fulltext")
            elif "vector" in d.methods:
                preferred_methods.append("vector")
            if not preferred_methods:
                preferred_methods = d.methods[:2]
            capped.append(
                RoutingDecision(
                    source=d.source,
                    methods=preferred_methods or ["vector"],
                    query=d.query,
                    filters=d.filters,
                    mode=d.mode,
                    sort_field=d.sort_field,
                    sort_order=d.sort_order,
                    group_by=d.group_by,
                    aggregate_top_n=d.aggregate_top_n,
                )
            )
        return capped

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
        logger.set_prompt_role("intent")
        try:
            raw = await self._generate(prompt, max_tokens=500)
            return _parse_json_object(
                raw,
                {
                    "intent": "find_information",
                    "entities": [],
                    "temporal": None,
                    "source_hints": [],
                    "complexity": IntentComplexity.SIMPLE,
                    "retrieval_plan": None,
                },
            )
        finally:
            logger.set_tool_step(None)
            logger.set_prompt_role(None)

    @traceable(name="search_execute_routing", run_type="chain")
    async def _execute_routing(
        self,
        decisions: list[RoutingDecision],
    ) -> tuple[
        list[SearchResultV1],
        list[str],
        int | None,
        list[AggregateGroup],
        str | None,
        str | None,
    ]:
        """Execute all routing decisions in parallel.

        Returns: (results, errors, count, aggregates, count_source, aggregates_source)
        """
        tasks = []
        for decision in decisions:
            tasks.append(self._execute_one(decision))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[SearchResultV1] = []
        all_errors: list[str] = []
        count: int | None = None
        aggregates: list[AggregateGroup] = []
        count_source: str | None = None
        aggregates_source: str | None = None

        for i, result in enumerate(results_list):
            if isinstance(result, Exception):
                all_errors.append(f"{decisions[i].source}: {result!s}")
                logger.warning("Search failed for %s: %s", decisions[i].source, result)
                continue
            if isinstance(result, tuple):
                part_results, part_errors, dres = result
                all_results.extend(part_results)
                all_errors.extend(part_errors)
                if isinstance(dres, DispatcherResult):
                    if dres.count is not None and count_source is None:
                        count = dres.count
                        count_source = dres.source
                    if dres.aggregates and aggregates_source is None:
                        aggregates = dres.aggregates
                        aggregates_source = dres.source

        return (
            all_results,
            all_errors,
            count,
            aggregates,
            count_source,
            aggregates_source,
        )

    async def _execute_one(
        self,
        decision: RoutingDecision,
    ) -> tuple[list[SearchResultV1], list[str], DispatcherResult | None]:
        """Execute a single routing decision against either MCP or direct backend."""
        source = decision.source
        errors: list[str] = []

        # Try MCP dispatcher first
        if self._dispatcher.has_source(source):
            try:
                dres = await self._dispatcher.search(
                    source=source,
                    query=decision.query,
                    methods=decision.methods,
                    filters=decision.filters,
                    top_k=10,
                    mode=decision.mode,
                    sort_field=decision.sort_field,
                    sort_order=decision.sort_order,
                    group_by=decision.group_by,
                    aggregate_top_n=decision.aggregate_top_n,
                )
                logger.debug(
                    "MCP search %s: %s results, mode=%s",
                    source,
                    len(dres.results),
                    dres.mode,
                )
                return dres.results, errors, dres
            except Exception as e:
                errors.append(f"{source}: MCP call failed: {e!s}")
                logger.warning("MCP search failed for %s: %s", source, e)
                return [], errors, DispatcherResult(source=source, mode=decision.mode)

        # Try direct backend (search mode only; no count/aggregate)
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
                dres = DispatcherResult(
                    results=results,
                    source=source,
                    mode=SearchMode.SEARCH,
                )
                return results, errors, dres
            except Exception as e:
                errors.append(f"{source}: {e!s}")
                logger.warning("Direct search failed for %s: %s", source, e)
                return (
                    [],
                    errors,
                    DispatcherResult(source=source, mode=SearchMode.SEARCH),
                )

        errors.append(f"No handler for source '{source}'")
        return [], errors, DispatcherResult(source=source, mode=decision.mode)

    def _should_refine(
        self,
        results: list[SearchResultV1],
        intent: dict[str, Any],
        routing_plan_decisions: list[RoutingDecision],
    ) -> tuple[bool, RefinementReason | None]:
        """Deterministic refinement triggers. Returns (should_refine, reason)."""
        complexity = _intent_complexity(intent.get("complexity"))
        if not results:
            # Explicit filters + simple query = no refinement, just report empty.
            has_filters = any(d.filters for d in routing_plan_decisions)
            if has_filters and complexity == IntentComplexity.SIMPLE:
                return False, None
            return True, RefinementReason.NO_RESULTS

        # Trigger 1: Too few results (low recall)
        if len(results) < 3:
            return True, RefinementReason.LOW_SOURCE_COVERAGE

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
            return True, RefinementReason.LOW_CONFIDENCE

        # Trigger 3: Only one source returned results (missing cross-source context)
        # Check if intent implies multiple sources (e.g. source_hints has >1 item)
        sources = {r.source for r in results}
        hints = intent.get("source_hints", [])
        if len(sources) == 1 and len(hints) > 1:
            # Skip refinement when intent is multi_hop: missing source likely needs
            # entity from this step (e.g. "email from that person"); retrying with
            # the same query would just repeat 0 results.
            if complexity == IntentComplexity.MULTI_HOP:
                logger.debug(
                    "Refinement skipped: single_source (intent is multi_hop, retry would use same query)"
                )
                return False, None
            return True, RefinementReason.SINGLE_SOURCE

        # Trigger 4: Results contradict each other (placeholder for now)
        # if self._detect_contradictions(results):
        #    return True, "contradiction"

        return False, None

    @traceable(name="search_refinement", run_type="chain")
    async def _refine(
        self,
        context: str,
        intent: dict[str, Any],
        results: list[SearchResultV1],
        previous_decisions: list[RoutingDecision],
        reason: RefinementReason,
    ) -> list[RoutingDecision]:
        """Generate refinement decisions. Uses deterministic adjustments + optional LLM."""
        refined: list[RoutingDecision] = []

        if reason == RefinementReason.NO_RESULTS:
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

        elif reason == RefinementReason.LOW_SOURCE_COVERAGE:
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

        elif reason == RefinementReason.LOW_CONFIDENCE:
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

        elif reason == RefinementReason.SINGLE_SOURCE:
            # Retry sources that were in the plan but returned no results (same query/filters)
            actual_sources = {r.source for r in results}
            for d in previous_decisions:
                if d.source not in actual_sources:
                    refined.append(
                        RoutingDecision(
                            source=d.source,
                            methods=d.methods,
                            query=d.query,
                            filters=d.filters,
                        )
                    )

        re_query_sources = [d.source for d in refined]
        logger.info(
            "Refinement (reason=%s): %s additional decisions",
            reason,
            len(refined),
        )
        logger.debug(
            "Refinement: reason=%s re_query_sources=%s",
            reason,
            re_query_sources,
        )
        return refined[:4]  # Cap at 4 refinement steps

    @traceable(name="search_multihop", run_type="chain")
    async def _run_multihop(
        self,
        query: str,
        intent: dict[str, Any],
        plan: list[dict[str, Any]],
    ) -> tuple[
        list[SearchResultV1],
        list[RoutingDecision],
        list[str],
        int | None,
        list[AggregateGroup],
        str | None,
        str | None,
    ]:
        """Execute retrieval_plan step by step; extract entity between steps for entity_from_previous."""
        all_results: list[SearchResultV1] = []
        all_decisions: list[RoutingDecision] = []
        errors: list[str] = []
        count: int | None = None
        aggregates: list[AggregateGroup] = []
        count_source: str | None = None
        aggregates_source: str | None = None
        extra_filters: list[dict[str, Any]] | None = None

        for step_idx, step in enumerate(plan):
            sources = step.get("sources") or []
            entity_from_previous = step.get("entity_from_previous") is True

            # Restrict step to sources that support the extracted filters when this step
            # consumes entity_from_previous (e.g. "email from that person"). Otherwise we
            # would query all step sources (e.g. email + whatsapp); sources that don't
            # support from_name/from_email get the raw query and can return irrelevant
            # hits (e.g. same WhatsApp chat again). Future-proof: we use capability
            # registry (sources_supporting_filter), so any new source that declares
            # from_name/from_email will be included automatically; no hardcoded list.
            if entity_from_previous and extra_filters:
                from_fields = {"from_name", "from_email"}
                filter_fields = {
                    f.get("field") for f in extra_filters if f.get("field")
                }
                if filter_fields & from_fields:
                    supporting = set()
                    for field in from_fields:
                        supporting.update(
                            self._capabilities.sources_supporting_filter(field)
                        )
                    if supporting:
                        sources = [s for s in sources if s in supporting]
                        logger.info(
                            "Multihop step %s: restricted to from-capable sources %s",
                            step_idx,
                            sources,
                        )

            decisions = self._router.decisions_for_sources(
                sources=sources,
                query=query,
                intent=intent,
                extra_filters=extra_filters,
            )
            if not decisions:
                logger.warning(
                    "Multihop step %s: no decisions for sources %s", step_idx, sources
                )
                continue

            (
                step_results,
                step_errors,
                step_count,
                step_aggregates,
                step_count_src,
                step_agg_src,
            ) = await self._execute_routing(decisions)
            all_results.extend(step_results)
            all_decisions.extend(decisions)
            errors.extend(step_errors)
            if step_count is not None and count_source is None:
                count = step_count
                count_source = step_count_src
            if step_aggregates and aggregates_source is None:
                aggregates = step_aggregates
                aggregates_source = step_agg_src

            # Prepare filters for next step if it needs entity from this step
            next_step = plan[step_idx + 1] if step_idx + 1 < len(plan) else None
            if next_step and next_step.get("entity_from_previous"):
                if step_aggregates:
                    # Use top aggregate group for from_name filter
                    top = step_aggregates[0] if step_aggregates else None
                    if top:
                        name = top.label or top.group_value
                        if name:
                            extra_filters = [
                                {
                                    "field": "from_name",
                                    "operator": "contains",
                                    "value": name,
                                }
                            ]
                            logger.info(
                                "Multihop: entity from aggregates -> from_name=%s",
                                name,
                            )
                        else:
                            extra_filters = None
                    else:
                        extra_filters = None
                elif step_results:
                    entity = await extract_entity(
                        step_results, llm_generate=self._generate
                    )
                    extra_filters = entity.to_filters()
                    if not extra_filters:
                        logger.warning(
                            "Multihop: no entity extracted from step %s for next step",
                            step_idx,
                        )
                else:
                    extra_filters = None
            else:
                extra_filters = None

        return (
            all_results,
            all_decisions,
            errors,
            count,
            aggregates,
            count_source,
            aggregates_source,
        )

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
        intent_trace: dict[str, Any] = {}

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
                    "complexity": RoutingComplexity.SIMPLE,
                    "intent_trace": intent_trace,
                    "timing_ms": {},
                },
            )

        # Use user_message directly as query when available; fall back to first-line extraction
        query = (
            user_message.strip()[:200]
            if user_message and user_message.strip()
            else _default_query_from_context(context)
        )
        errors: list[str] = []
        timing_ms: dict[str, float] = {}

        # 2. Intent analysis: deterministic modules first, then LLM fallback
        t0 = time.monotonic()
        source_matches = self._router.score_sources_from_text(
            query,
            threshold=0.0,
            top_n=5,
        )
        fast_intent = self._router.infer_fast_path_intent(query)
        deterministic = self._intent_analyzer.analyze(
            query=query,
            source_matches=source_matches,
            fast_path_intent=fast_intent,
        )
        intent_trace = deterministic.trace()
        if deterministic.should_use_deterministic:
            intent = deterministic.intent
            timing_ms["intent"] = round((time.monotonic() - t0) * 1000, 1)
            logger.info(
                "Intent (deterministic): hints=%s | temporal=%s | confidence=%.2f",
                intent.get("source_hints"),
                intent.get("temporal"),
                deterministic.aggregate_confidence,
            )
        else:
            intent = await self._analyze_intent(context)
            timing_ms["intent"] = round((time.monotonic() - t0) * 1000, 1)
            intent_trace["decision"] = "llm"
        logger.info(
            "Intent: %s | entities=%s | temporal=%s | hints=%s | complexity=%s | plan_steps=%s",
            intent.get("intent"),
            intent.get("entities"),
            intent.get("temporal"),
            intent.get("source_hints"),
            intent.get("complexity"),
            len(intent.get("retrieval_plan") or []),
        )

        available_sources = set(self._capabilities.all_sources())
        plan = intent.get("retrieval_plan")
        validated_plan = _validate_retrieval_plan(plan, available_sources)
        intent_complexity = _intent_complexity(intent.get("complexity"))
        use_multihop = (
            intent_complexity == IntentComplexity.MULTI_HOP
            and validated_plan is not None
        )
        broad_search_note: str | None = None
        if intent_complexity == IntentComplexity.MULTI_HOP and validated_plan is None:
            logger.info(
                "Multihop plan missing or invalid, using single-step search",
            )

        logger.debug(
            "Search intent: complexity=%s plan_steps=%s use_multihop=%s",
            intent.get("complexity"),
            len(plan or []),
            use_multihop,
        )

        if use_multihop:
            # 3a. Multi-hop: run steps sequentially with entity extraction between steps
            t0 = time.monotonic()
            (
                all_results,
                decisions_for_run,
                exec_errors,
                count,
                aggregates,
                count_source,
                aggregates_source,
            ) = await self._run_multihop(query, intent, validated_plan)
            timing_ms["routing"] = round((time.monotonic() - t0) * 1000, 1)
            timing_ms["execution"] = timing_ms["routing"]
            errors.extend(exec_errors)
            complexity_for_meta = RoutingComplexity.COMPLEX
            if not decisions_for_run:
                return UniversalSearchResponse(
                    results=[],
                    errors=errors or ["Multi-hop produced no decisions."],
                    meta={
                        "query": query,
                        "sources_queried": [],
                        "methods_used": [],
                        "iterations": 0,
                        "total_results": 0,
                        "complexity": complexity_for_meta,
                        "intent_trace": intent_trace,
                        "source_match_trace": [],
                        "timing_ms": timing_ms,
                    },
                )
            source_match_trace: list[dict[str, Any]] = []
        else:
            # 3. Single-step: route then execute (fallback when no valid retrieval_plan)
            t0 = time.monotonic()
            routing_plan = self._router.route(intent, query)
            source_match_trace = [
                {
                    "source": m.source,
                    "confidence": m.confidence,
                    "reasons": m.reasons,
                }
                for m in routing_plan.source_matches
            ]
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
                        "intent_trace": intent_trace,
                        "source_match_trace": source_match_trace,
                        "timing_ms": timing_ms,
                    },
                )

            decisions_for_run = routing_plan.decisions
            if getattr(routing_plan, "used_default_sources", False):
                decisions_for_run = self._cap_broad_decisions(routing_plan.decisions)
                broad_search_note = (
                    "No explicit source hint detected; ran capped broad search."
                )

            t0 = time.monotonic()
            (
                all_results,
                exec_errors,
                count,
                aggregates,
                count_source,
                aggregates_source,
            ) = await self._execute_routing(decisions_for_run)
            timing_ms["execution"] = round((time.monotonic() - t0) * 1000, 1)
            errors.extend(exec_errors)
            complexity_for_meta = routing_plan.complexity
        notes: list[str] = []
        if broad_search_note:
            notes.append(broad_search_note)

        # 6. Determine if query is personal based on routed sources
        is_personal = self._is_personal_query(decisions_for_run)

        # 6b. Skip refinement for count/aggregate modes
        first_mode = (
            decisions_for_run[0].mode if decisions_for_run else SearchMode.SEARCH
        )
        skip_refinement = first_mode in (SearchMode.COUNT, SearchMode.AGGREGATE)

        # 7. Refinement loop
        iterations = 1
        if do_refinement and not skip_refinement:
            for _ in range(self._max_refinement_rounds):
                should_refine, reason = self._should_refine(
                    all_results,
                    intent,
                    decisions_for_run,
                )
                if not should_refine:
                    # Note when we have explicit filters with no results
                    if not all_results:
                        has_filters = any(d.filters for d in decisions_for_run)
                        if has_filters:
                            notes.append("No data found for the requested criteria.")
                    break

                iterations += 1
                t0 = time.monotonic()
                refined_decisions = await self._refine(
                    context,
                    intent,
                    all_results,
                    decisions_for_run,
                    reason,
                )
                if not refined_decisions:
                    break

                (
                    refined_results,
                    refined_errors,
                    _,
                    _,
                    _,
                    _,
                ) = await self._execute_routing(refined_decisions)
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
        sources_queried = list({d.source for d in decisions_for_run})
        methods_used = list({m for d in decisions_for_run for m in d.methods})
        timing_ms["total"] = round((time.monotonic() - pipeline_start) * 1000, 1)

        logger.info(
            "Search complete: %s results | sources=%s | methods=%s | iterations=%s | total=%.0fms",
            len(ranked),
            sources_queried,
            methods_used,
            iterations,
            timing_ms["total"],
        )

        meta: dict[str, Any] = {
            "query": query,
            "sources_queried": sources_queried,
            "methods_used": methods_used,
            "iterations": iterations,
            "total_results": len(ranked),
            "complexity": complexity_for_meta,
            "intent_trace": intent_trace,
            "source_match_trace": source_match_trace,
            "timing_ms": timing_ms,
        }
        if count is not None:
            meta["count"] = count
            meta["count_source"] = count_source
        if aggregates:
            meta["aggregates"] = [
                {"group_value": a.group_value, "count": a.count, "label": a.label}
                for a in aggregates
            ]
            meta["aggregates_source"] = aggregates_source

        return UniversalSearchResponse(
            results=ranked,
            errors=errors,
            notes=notes,
            meta=meta,
        )

    def _is_personal_query(self, decisions: list[RoutingDecision]) -> bool:
        """Determine query class from routed source capabilities."""
        if not decisions:
            return True
        saw_web = False
        saw_personal = False
        for d in decisions:
            caps = self._capabilities.get(d.source)
            if not caps:
                continue
            if str(caps.source_class) == "web":
                saw_web = True
            else:
                saw_personal = True
        if saw_personal:
            return True
        if saw_web:
            return False
        return True
