"""Weighted fusion ranker: merges results from multiple sources using per-method score weighting.

Replaces the old naive concatenation + LLM-only rerank pattern.
LLM rerank is kept only as an optional tie-breaker for top results.
"""

import logging
from typing import Any

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass

logger = logging.getLogger(__name__)

# Method weight hierarchy: structured exactness > fulltext relevance > vector similarity
METHOD_WEIGHTS: dict[str, float] = {
    "structured": 1.0,
    "fulltext": 0.85,
    "vector": 0.7,
    "graph": 0.9,  # reserved for Phase 2
}

# Source class boost for personal queries
SOURCE_CLASS_BOOST: dict[str, float] = {
    "personal": 1.0,
    "web": 0.8,
}


def compute_fused_score(
    result: SearchResultV1,
    is_personal_query: bool = True,
) -> float:
    """Compute a weighted aggregate score for a single result.

    Score = weighted_method_average * source_class_boost
    """
    scores = result.scores
    if not scores:
        return 0.0

    total_weight = 0.0
    total_score = 0.0
    for method, score in scores.items():
        w = METHOD_WEIGHTS.get(method, 0.5)
        total_weight += w
        total_score += score * w

    method_avg = total_score / total_weight if total_weight > 0 else 0.0

    # Apply source class boost
    if is_personal_query:
        boost = SOURCE_CLASS_BOOST.get(result.source_class.value, 0.8)
    else:
        # For web queries, invert the boost
        boost = 1.0 if result.source_class == SourceClass.WEB else 0.9

    return method_avg * boost


def deduplicate_results(results: list[SearchResultV1]) -> list[SearchResultV1]:
    """Deduplicate results by ID + source, keeping the version with highest score."""
    seen: dict[str, SearchResultV1] = {}
    for r in results:
        key = f"{r.source}:{r.id}"
        existing = seen.get(key)
        if existing is None:
            seen[key] = r
        else:
            # Merge scores: keep the higher score per method
            merged_scores = dict(existing.scores)
            for method, score in r.scores.items():
                if method not in merged_scores or score > merged_scores[method]:
                    merged_scores[method] = score
            merged_methods = list(set(existing.methods_used + r.methods_used))
            seen[key] = existing.model_copy(update={
                "scores": merged_scores,
                "methods_used": merged_methods,
            })
    return list(seen.values())


class WeightedFusionRanker:
    """Ranks search results using weighted score fusion across methods and sources."""

    def fuse_and_rank(
        self,
        results: list[SearchResultV1],
        is_personal_query: bool = True,
        max_results: int = 20,
    ) -> list[SearchResultV1]:
        """Deduplicate, score, and rank all results.

        Args:
            results: Raw results from all sources.
            is_personal_query: Whether the query is about personal data (boosts personal sources).
            max_results: Maximum results to return.

        Returns:
            Ranked, deduplicated results.
        """
        if not results:
            return []

        # Deduplicate
        deduped = deduplicate_results(results)

        # Score and sort
        scored: list[tuple[float, SearchResultV1]] = [
            (compute_fused_score(r, is_personal_query), r)
            for r in deduped
        ]
        scored.sort(key=lambda x: -x[0])

        ranked = [r for _, r in scored[:max_results]]

        logger.info(
            "Fusion: %s input -> %s deduped -> %s ranked | personal=%s",
            len(results), len(deduped), len(ranked), is_personal_query,
        )

        return ranked
