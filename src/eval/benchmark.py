"""Benchmark runner for Lilith Search evaluation.

**What it's for:** Runs a set of search queries (defined in benchmarks.yaml) through
the full universal search pipeline (orchestrator + MCPs), then checks that results
match expected criteria (e.g. expected_sources, expected_top_3). Used to catch
regressions when changing search, routing, or MCP integrations.

Run: python -m src.eval.benchmark --config benchmarks.yaml
"""

import argparse
import asyncio
import logging
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.core.logger import logger

# Basic Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")


class BenchmarkRunner:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.orchestrator = None
        self.registry = None

    async def setup(self):
        """Initialize the orchestrator used for testing."""
        logger.info("Setting up orchestrator for benchmark...")

        from src.core.bootstrap import setup_tools

        # Initialize full tool registry (includes MCPs)
        self.registry = await setup_tools()

        # Extract the search tool/orchestrator
        search_tool = self.registry.get("universal_search")
        if not search_tool:
            raise RuntimeError("Universal search tool not found in registry")

        self.orchestrator = search_tool.orchestrator

    async def cleanup(self):
        """Clean up tools and MCP connections."""
        if self.registry:
            for tool in self.registry.list_tools():
                if hasattr(tool, "close"):
                    if asyncio.iscoroutinefunction(tool.close):
                        await tool.close()
                    else:
                        tool.close()

    def load_benchmarks(self) -> list[dict[str, Any]]:
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    async def run(self):
        try:
            await self.setup()
            benchmarks = self.load_benchmarks()

            passed = 0
            total = len(benchmarks)

            print("\n--- Starting Benchmark Run ---\n")

            for i, bench in enumerate(benchmarks):
                query = bench["query"]
                print(f"[{i + 1}/{total}] Query: {query}")

                try:
                    response = await self.orchestrator.search(user_message=query)
                    results = response.results

                    # Verify
                    is_pass = self.evaluate(bench, response)
                    if is_pass:
                        passed += 1
                        print("  ✅ PASS")
                    else:
                        print("  ❌ FAIL")

                    # Print metrics
                    print(f"     Results: {len(results)}")
                    print(f"     Sources: {response.meta.get('sources_queried')}")
                    print(f"     Methods: {response.meta.get('methods_used')}")

                except Exception as e:
                    print(f"  ❌ ERROR: {e}")
                    logger.exception("Benchmark query failed")

            print(
                f"\n--- Summary: {passed}/{total} ({passed / total * 100:.1f}%) Passed ---"
            )
        finally:
            await self.cleanup()

    def evaluate(self, bench: dict[str, Any], response: Any) -> bool:
        """Check if response meets benchmark criteria."""
        results = response.results

        # 1. Expected Sources
        expected_sources = bench.get("expected_sources")
        if expected_sources:
            actual_sources = {r.source for r in results}
            # Check coverage
            missing = set(expected_sources) - actual_sources
            if missing:
                print(f"     Missing sources: {missing}")
                return False

        # 2. Expected Top K (Exact ID match or constraints)
        expected_top = bench.get("expected_top_3")
        if expected_top:
            top_results = results[:3]
            # Simple check: do we have at least N matches?
            matches = 0
            for exp in expected_top:
                # multifaceted match
                found = False
                for res in top_results:
                    # Check conditions
                    checks = []
                    if "source" in exp:
                        checks.append(res.source == exp["source"])
                    if "id" in exp:
                        checks.append(res.id == exp["id"])
                    if "from" in exp:
                        # This would require accessing raw metadata which might vary
                        meta = res.metadata or {}
                        checks.append(exp["from"] in str(meta.get("from", "")))

                    if all(checks):
                        found = True
                        break
                if found:
                    matches += 1

            threshold = bench.get("precision_threshold", 0.0)
            score = matches / len(expected_top)
            if score < threshold:
                print(f"     Precision score {score:.2f} < threshold {threshold}")
                return False

        return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="benchmarks.yaml", help="Path to benchmark config"
    )
    args = parser.parse_args()

    runner = BenchmarkRunner(args.config)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
