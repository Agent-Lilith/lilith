import pytest
from pydantic import ValidationError

from src.contracts.mcp_search_v1 import CapabilityTier, SearchCapabilities


def test_search_capabilities_defaults_are_explicit():
    caps = SearchCapabilities(
        source_name="email",
        supported_methods=["structured", "vector"],
        latency_tier=CapabilityTier.MEDIUM,
        quality_tier=CapabilityTier.HIGH,
        cost_tier=CapabilityTier.MEDIUM,
    )
    assert caps.schema_version == "1.2"
    assert caps.alias_hints == []
    assert caps.freshness_window_days is None
    assert caps.latency_tier == CapabilityTier.MEDIUM
    assert caps.quality_tier == CapabilityTier.HIGH
    assert caps.cost_tier == CapabilityTier.MEDIUM


def test_search_capabilities_requires_tiers():
    with pytest.raises(ValidationError):
        SearchCapabilities(
            source_name="email",
            supported_methods=["vector"],
        )


def test_search_capabilities_rejects_invalid_tiers():
    with pytest.raises(ValidationError):
        SearchCapabilities(
            source_name="email",
            supported_methods=["vector"],
            latency_tier="very_fast",
            quality_tier=CapabilityTier.MEDIUM,
            cost_tier=CapabilityTier.MEDIUM,
        )


def test_search_capabilities_rejects_non_positive_freshness():
    with pytest.raises(ValidationError):
        SearchCapabilities(
            source_name="email",
            supported_methods=["vector"],
            freshness_window_days=0,
            latency_tier=CapabilityTier.MEDIUM,
            quality_tier=CapabilityTier.MEDIUM,
            cost_tier=CapabilityTier.MEDIUM,
        )


def test_search_capabilities_rejects_empty_alias_hints():
    with pytest.raises(ValidationError):
        SearchCapabilities(
            source_name="email",
            supported_methods=["vector"],
            alias_hints=["ok", "  "],
            latency_tier=CapabilityTier.MEDIUM,
            quality_tier=CapabilityTier.MEDIUM,
            cost_tier=CapabilityTier.MEDIUM,
        )
