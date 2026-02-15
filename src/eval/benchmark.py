"""Deterministic retrieval eval loop with regression gating.

Run:
  python -m src.eval.benchmark --config benchmarks.yaml \
      --output .artifacts/retrieval_eval/latest.json \
      --report .artifacts/retrieval_eval/latest.md \
      --baseline src/eval/baseline_metrics.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.contracts.mcp_search_v1 import (
    CapabilityTier,
    FilterSpec,
    SearchCapabilities,
    SearchMode,
    SearchResultV1,
    SourceClass,
)
from src.orchestrators.search.capabilities import CapabilityRegistry
from src.orchestrators.search.dispatcher import DispatcherResult
from src.orchestrators.search.orchestrator import UniversalSearchOrchestrator


@dataclass
class CaseMetrics:
    case_id: str
    query: str
    precision_at_k: float
    coverage: float
    latency_ms: float
    refinement_hit: int
    expected_sources: list[str]
    actual_sources: list[str]
    failures: list[str]


@dataclass
class AggregateMetrics:
    avg_precision_at_k: float
    avg_coverage: float
    p95_latency_ms: float
    refinement_hit_rate: float


class FixtureDispatcher:
    """Per-case deterministic dispatcher used by the eval loop."""

    def __init__(
        self,
        capabilities: CapabilityRegistry,
        results_by_source: dict[str, dict[str, Any]],
    ) -> None:
        self._capabilities = capabilities
        self._results_by_source = results_by_source

    def has_source(self, source_name: str) -> bool:
        return source_name in set(self._capabilities.all_sources())

    async def search(
        self,
        source: str,
        query: str,
        methods: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        top_k: int = 10,
        mode: SearchMode = SearchMode.SEARCH,
        sort_field: str | None = None,
        sort_order: str = "desc",
        group_by: str | None = None,
        aggregate_top_n: int = 10,
    ) -> DispatcherResult:
        del (
            query,
            methods,
            filters,
            top_k,
            sort_field,
            sort_order,
            group_by,
            aggregate_top_n,
        )
        fixture = self._results_by_source.get(source, {})
        latency_ms = float(fixture.get("latency_ms", 0.0) or 0.0)
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000.0)

        if mode != SearchMode.SEARCH:
            return DispatcherResult(source=source, mode=mode)

        raw_items = fixture.get("items", [])
        results: list[SearchResultV1] = []
        caps = self._capabilities.get(source)
        source_class = caps.source_class if caps else SourceClass.PERSONAL
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            results.append(
                SearchResultV1(
                    id=str(raw.get("id", "")),
                    source=source,
                    source_class=source_class,
                    title=str(raw.get("title", "")),
                    snippet=str(raw.get("snippet", "")),
                    timestamp=raw.get("timestamp"),
                    scores=dict(raw.get("scores", {})),
                    methods_used=list(raw.get("methods_used", [])),
                    metadata=dict(raw.get("metadata", {})),
                    provenance=raw.get("provenance"),
                )
            )

        return DispatcherResult(results=results, source=source, mode=SearchMode.SEARCH)


class BenchmarkRunner:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        with self.config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("Benchmark config must be a mapping")
        if "sources" not in data or "cases" not in data:
            raise ValueError("Benchmark config must define 'sources' and 'cases'")
        return data

    def _build_capabilities(self) -> CapabilityRegistry:
        registry = CapabilityRegistry()
        for raw in self.config.get("sources", []):
            if not isinstance(raw, dict):
                raise ValueError("Each source entry must be a mapping")
            caps = SearchCapabilities(
                schema_version="1.2",
                source_name=str(raw["source_name"]),
                source_class=SourceClass(str(raw.get("source_class", "personal"))),
                supported_methods=list(raw.get("supported_methods", ["vector"])),
                supported_filters=[
                    FilterSpec(**f) for f in list(raw.get("supported_filters", []))
                ],
                alias_hints=list(raw.get("alias_hints", [])),
                freshness_window_days=raw.get("freshness_window_days"),
                latency_tier=CapabilityTier(str(raw.get("latency_tier", "medium"))),
                quality_tier=CapabilityTier(str(raw.get("quality_tier", "medium"))),
                cost_tier=CapabilityTier(str(raw.get("cost_tier", "medium"))),
            )
            registry.register(caps)
        return registry

    @staticmethod
    def _matches_expected(
        result: SearchResultV1,
        expected: dict[str, Any],
    ) -> bool:
        source = expected.get("source")
        if source and str(source) != result.source:
            return False

        result_id = expected.get("id")
        if result_id and str(result_id) != result.id:
            return False

        title_contains = expected.get("title_contains") or []
        for token in title_contains:
            if str(token).lower() not in result.title.lower():
                return False

        snippet_contains = expected.get("snippet_contains") or []
        for token in snippet_contains:
            if str(token).lower() not in result.snippet.lower():
                return False

        metadata_contains = expected.get("metadata_contains") or {}
        meta_blob = json.dumps(result.metadata, sort_keys=True).lower()
        for token in metadata_contains.values():
            if str(token).lower() not in meta_blob:
                return False

        return True

    def _score_case(
        self,
        case: dict[str, Any],
        response: Any,
    ) -> CaseMetrics:
        case_id = str(case.get("id", "unknown"))
        query = str(case.get("query", ""))
        failures: list[str] = []

        relevant_rules = case.get("expected_relevance", {}) or {}
        top_k = int(relevant_rules.get("top_k", 5) or 5)
        top_k = max(1, top_k)
        expected_relevant = list(relevant_rules.get("relevant", []))

        top_results = list(response.results[:top_k])
        relevant_hits = 0
        if expected_relevant:
            for result in top_results:
                if any(
                    self._matches_expected(result, rule)
                    for rule in expected_relevant
                    if isinstance(rule, dict)
                ):
                    relevant_hits += 1
        precision = relevant_hits / float(top_k)

        expected_sources = [str(s) for s in list(case.get("expected_sources", []))]
        actual_sources = sorted({r.source for r in response.results})
        if expected_sources:
            covered = sum(1 for s in expected_sources if s in actual_sources)
            coverage = covered / float(len(expected_sources))
        else:
            coverage = 1.0

        latency_ms = float(response.meta.get("timing_ms", {}).get("total", 0.0) or 0.0)
        refinement_trace = list(response.meta.get("refinement_trace", []))
        refinement_hit = 1 if any(t.get("triggered") for t in refinement_trace) else 0

        case_thresholds = case.get("thresholds", {}) or {}
        min_precision = case_thresholds.get("min_precision_at_k")
        min_coverage = case_thresholds.get("min_coverage")
        max_latency = case_thresholds.get("max_latency_ms")
        expected_refine = case_thresholds.get("expect_refinement")

        if min_precision is not None and precision < float(min_precision):
            failures.append(
                f"precision_at_k={precision:.3f} below min {float(min_precision):.3f}"
            )
        if min_coverage is not None and coverage < float(min_coverage):
            failures.append(
                f"coverage={coverage:.3f} below min {float(min_coverage):.3f}"
            )
        if max_latency is not None and latency_ms > float(max_latency):
            failures.append(
                f"latency_ms={latency_ms:.1f} above max {float(max_latency):.1f}"
            )
        if expected_refine is not None and bool(expected_refine) != bool(
            refinement_hit
        ):
            failures.append(
                f"refinement_hit={refinement_hit} expected {1 if expected_refine else 0}"
            )

        return CaseMetrics(
            case_id=case_id,
            query=query,
            precision_at_k=round(precision, 4),
            coverage=round(coverage, 4),
            latency_ms=round(latency_ms, 1),
            refinement_hit=refinement_hit,
            expected_sources=expected_sources,
            actual_sources=actual_sources,
            failures=failures,
        )

    @staticmethod
    def _aggregate(case_metrics: list[CaseMetrics]) -> AggregateMetrics:
        if not case_metrics:
            return AggregateMetrics(0.0, 0.0, 0.0, 0.0)

        precisions = [m.precision_at_k for m in case_metrics]
        coverages = [m.coverage for m in case_metrics]
        latencies = sorted(m.latency_ms for m in case_metrics)
        refinement_hits = [m.refinement_hit for m in case_metrics]

        p95_index = max(0, min(len(latencies) - 1, int(0.95 * (len(latencies) - 1))))

        return AggregateMetrics(
            avg_precision_at_k=round(sum(precisions) / len(precisions), 4),
            avg_coverage=round(sum(coverages) / len(coverages), 4),
            p95_latency_ms=round(latencies[p95_index], 1),
            refinement_hit_rate=round(sum(refinement_hits) / len(refinement_hits), 4),
        )

    @staticmethod
    def _check_global_thresholds(
        metrics: AggregateMetrics,
        thresholds: dict[str, Any],
    ) -> list[str]:
        failures: list[str] = []

        min_precision = thresholds.get("min_precision_at_k")
        if min_precision is not None and metrics.avg_precision_at_k < float(
            min_precision
        ):
            failures.append(
                "avg_precision_at_k "
                f"{metrics.avg_precision_at_k:.3f} < {float(min_precision):.3f}"
            )

        min_coverage = thresholds.get("min_coverage")
        if min_coverage is not None and metrics.avg_coverage < float(min_coverage):
            failures.append(
                f"avg_coverage {metrics.avg_coverage:.3f} < {float(min_coverage):.3f}"
            )

        max_p95_latency = thresholds.get("max_p95_latency_ms")
        if max_p95_latency is not None and metrics.p95_latency_ms > float(
            max_p95_latency
        ):
            failures.append(
                "p95_latency_ms "
                f"{metrics.p95_latency_ms:.1f} > {float(max_p95_latency):.1f}"
            )

        min_refine_hit_rate = thresholds.get("min_refinement_hit_rate")
        if min_refine_hit_rate is not None and metrics.refinement_hit_rate < float(
            min_refine_hit_rate
        ):
            failures.append(
                "refinement_hit_rate "
                f"{metrics.refinement_hit_rate:.3f} < {float(min_refine_hit_rate):.3f}"
            )

        return failures

    @staticmethod
    def _check_regression(
        current: AggregateMetrics,
        baseline: dict[str, Any],
        policy: dict[str, Any],
    ) -> list[str]:
        failures: list[str] = []

        base_precision = float(baseline.get("avg_precision_at_k", 0.0))
        base_coverage = float(baseline.get("avg_coverage", 0.0))
        base_latency = float(baseline.get("p95_latency_ms", 0.0))
        base_refine = float(baseline.get("refinement_hit_rate", 0.0))

        max_precision_drop = float(policy.get("max_precision_drop", 0.0))
        if (base_precision - current.avg_precision_at_k) > max_precision_drop:
            failures.append(
                "precision regression: "
                f"baseline={base_precision:.3f}, current={current.avg_precision_at_k:.3f}, "
                f"drop={base_precision - current.avg_precision_at_k:.3f}, "
                f"allowed={max_precision_drop:.3f}"
            )

        max_coverage_drop = float(policy.get("max_coverage_drop", 0.0))
        if (base_coverage - current.avg_coverage) > max_coverage_drop:
            failures.append(
                "coverage regression: "
                f"baseline={base_coverage:.3f}, current={current.avg_coverage:.3f}, "
                f"drop={base_coverage - current.avg_coverage:.3f}, "
                f"allowed={max_coverage_drop:.3f}"
            )

        max_latency_increase = float(policy.get("max_latency_increase_ms", 0.0))
        if (current.p95_latency_ms - base_latency) > max_latency_increase:
            failures.append(
                "latency regression: "
                f"baseline={base_latency:.1f}, current={current.p95_latency_ms:.1f}, "
                f"increase={current.p95_latency_ms - base_latency:.1f}, "
                f"allowed={max_latency_increase:.1f}"
            )

        max_refinement_drop = float(policy.get("max_refinement_hit_rate_drop", 0.0))
        if (base_refine - current.refinement_hit_rate) > max_refinement_drop:
            failures.append(
                "refinement hit rate regression: "
                f"baseline={base_refine:.3f}, current={current.refinement_hit_rate:.3f}, "
                f"drop={base_refine - current.refinement_hit_rate:.3f}, "
                f"allowed={max_refinement_drop:.3f}"
            )

        return failures

    async def run(self) -> dict[str, Any]:
        capabilities = self._build_capabilities()
        cases = list(self.config.get("cases", []))
        if not cases:
            raise ValueError("Benchmark config has no cases")

        case_metrics: list[CaseMetrics] = []
        case_errors: list[str] = []

        for case in cases:
            query = str(case.get("query", "")).strip()
            if not query:
                raise ValueError("Each benchmark case must have a non-empty query")

            dispatcher = FixtureDispatcher(
                capabilities=capabilities,
                results_by_source=dict(case.get("mock_results", {})),
            )
            orchestrator = UniversalSearchOrchestrator(
                capabilities=capabilities,
                dispatcher=dispatcher,  # type: ignore[arg-type]
                direct_backends=[],
                max_refinement_rounds=1,
            )

            # Benchmarks must stay deterministic and independent from LLM latency.
            # Force intent extraction to capability-driven hints for this query.
            async def _benchmark_intent(
                _: str,
                *,
                _router=orchestrator._router,
                _query=query,
            ) -> dict[str, Any]:
                fast = _router.infer_fast_path_intent(_query)
                if fast is not None:
                    return fast
                source_hints = [
                    m.source
                    for m in _router.score_sources_from_text(
                        _query,
                        threshold=0.3,
                        top_n=3,
                    )
                ]
                return {
                    "intent": "find_information",
                    "entities": [],
                    "temporal": None,
                    "source_hints": source_hints,
                    "complexity": "simple",
                    "retrieval_plan": None,
                }

            orchestrator._analyze_intent = _benchmark_intent  # type: ignore[method-assign]

            t0 = time.monotonic()
            response = await orchestrator.search(user_message=query)
            if "total" not in response.meta.get("timing_ms", {}):
                elapsed = round((time.monotonic() - t0) * 1000.0, 1)
                response.meta.setdefault("timing_ms", {})["total"] = elapsed

            metrics = self._score_case(case, response)
            case_metrics.append(metrics)
            case_errors.extend(f"{metrics.case_id}: {f}" for f in metrics.failures)

        aggregate = self._aggregate(case_metrics)
        threshold_failures = self._check_global_thresholds(
            aggregate,
            dict(self.config.get("thresholds", {})),
        )

        result: dict[str, Any] = {
            "metrics": {
                "avg_precision_at_k": aggregate.avg_precision_at_k,
                "avg_coverage": aggregate.avg_coverage,
                "p95_latency_ms": aggregate.p95_latency_ms,
                "refinement_hit_rate": aggregate.refinement_hit_rate,
            },
            "cases": [
                {
                    "id": m.case_id,
                    "query": m.query,
                    "precision_at_k": m.precision_at_k,
                    "coverage": m.coverage,
                    "latency_ms": m.latency_ms,
                    "refinement_hit": m.refinement_hit,
                    "expected_sources": m.expected_sources,
                    "actual_sources": m.actual_sources,
                    "failures": m.failures,
                }
                for m in case_metrics
            ],
            "case_failures": case_errors,
            "threshold_failures": threshold_failures,
            "regression_failures": [],
            "passed": not case_errors and not threshold_failures,
        }
        return result


def _render_markdown_report(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    lines = [
        "# Retrieval Eval Report",
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| avg_precision_at_k | {metrics['avg_precision_at_k']:.4f} |",
        f"| avg_coverage | {metrics['avg_coverage']:.4f} |",
        f"| p95_latency_ms | {metrics['p95_latency_ms']:.1f} |",
        f"| refinement_hit_rate | {metrics['refinement_hit_rate']:.4f} |",
        "",
        "## Per-case Metrics",
        "",
        "| Case | precision@k | coverage | latency_ms | refinement_hit | status |",
        "|---|---:|---:|---:|---:|---|",
    ]

    for case in payload["cases"]:
        status = "PASS" if not case["failures"] else "FAIL"
        lines.append(
            f"| {case['id']} | {case['precision_at_k']:.4f} | {case['coverage']:.4f} | "
            f"{case['latency_ms']:.1f} | {case['refinement_hit']} | {status} |"
        )

    failures = (
        list(payload.get("case_failures", []))
        + list(payload.get("threshold_failures", []))
        + list(payload.get("regression_failures", []))
    )
    lines.append("")
    lines.append("## Gate Result")
    lines.append("")
    lines.append("PASS" if payload.get("passed") else "FAIL")

    if failures:
        lines.append("")
        lines.append("## Failures")
        lines.append("")
        for failure in failures:
            lines.append(f"- {failure}")

    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="benchmarks.yaml")
    parser.add_argument(
        "--output",
        default=".artifacts/retrieval_eval/latest.json",
        help="JSON report output path",
    )
    parser.add_argument(
        "--report",
        default=".artifacts/retrieval_eval/latest.md",
        help="Markdown report output path",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="Optional baseline metrics JSON path for regression gating",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write current aggregate metrics to baseline path",
    )
    args = parser.parse_args()

    runner = BenchmarkRunner(args.config)
    payload = await runner.run()

    baseline_path = Path(args.baseline) if args.baseline else None
    if baseline_path and baseline_path.exists():
        with baseline_path.open("r", encoding="utf-8") as f:
            baseline = json.load(f)
        regression_policy = dict(runner.config.get("regression", {}))
        regressions = runner._check_regression(
            AggregateMetrics(**payload["metrics"]),
            baseline,
            regression_policy,
        )
        payload["regression_failures"] = regressions
        if regressions:
            payload["passed"] = False

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_markdown_report(payload), encoding="utf-8")

    if baseline_path and args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(payload["metrics"], indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"Wrote JSON report: {output_path}")
    print(f"Wrote Markdown report: {report_path}")
    if baseline_path:
        print(f"Baseline path: {baseline_path}")
    if payload["passed"]:
        print("Gate result: PASS")
        return 0
    print("Gate result: FAIL")
    for failure in (
        payload.get("case_failures", [])
        + payload.get("threshold_failures", [])
        + payload.get("regression_failures", [])
    ):
        print(f"- {failure}")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
