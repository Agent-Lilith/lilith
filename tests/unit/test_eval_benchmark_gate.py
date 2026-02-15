from __future__ import annotations

import pytest

from src.eval.benchmark import BenchmarkRunner


@pytest.mark.asyncio
async def test_benchmark_runner_computes_metrics_and_passes(tmp_path):
    config_path = tmp_path / "bench.yaml"
    config_path.write_text(
        """
version: 1
thresholds:
  min_precision_at_k: 0.3
  min_coverage: 0.5
  max_p95_latency_ms: 500
  min_refinement_hit_rate: 0.0
regression:
  max_precision_drop: 0.1
  max_coverage_drop: 0.1
  max_latency_increase_ms: 50
  max_refinement_hit_rate_drop: 0.5
sources:
  - source_name: email
    source_class: personal
    supported_methods: [vector, fulltext]
    supported_filters: []
    alias_hints: [email]
    freshness_window_days: 30
    latency_tier: medium
    quality_tier: medium
    cost_tier: medium
cases:
  - id: c1
    query: "email roadmap"
    expected_sources: [email]
    expected_relevance:
      top_k: 1
      relevant:
        - source: email
          title_contains: [roadmap]
    thresholds:
      min_precision_at_k: 1.0
      min_coverage: 1.0
      max_latency_ms: 300
      expect_refinement: true
    mock_results:
      email:
        latency_ms: 10
        items:
          - id: e1
            title: "Roadmap update"
            snippet: "Q3 planning"
            scores: {vector: 0.9}
            methods_used: [vector]
            metadata: {}
""",
        encoding="utf-8",
    )

    runner = BenchmarkRunner(str(config_path))
    payload = await runner.run()

    assert payload["passed"] is True
    assert payload["metrics"]["avg_precision_at_k"] >= 1.0
    assert payload["metrics"]["avg_coverage"] >= 1.0
    assert payload["metrics"]["p95_latency_ms"] >= 0.0
