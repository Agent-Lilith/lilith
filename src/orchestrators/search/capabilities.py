"""Capability registry: caches MCP server capabilities for routing decisions.

At bootstrap, the agent calls search_capabilities on each MCP server and
registers the results here. Non-MCP backends register their capabilities directly.
"""

import logging
from typing import Any

from src.contracts.mcp_search_v1 import SearchCapabilities, SourceClass

logger = logging.getLogger(__name__)


def _humanize_source_name(source_name: str) -> str:
    """Fallback display label: replace underscores with spaces, title-case."""
    return source_name.replace("_", " ").title()


class CapabilityRegistry:
    """Stores and queries search capabilities across all backends."""

    def __init__(self) -> None:
        self._capabilities: dict[str, SearchCapabilities] = {}

    def register(self, capabilities: SearchCapabilities) -> None:
        name = capabilities.source_name
        self._capabilities[name] = capabilities
        logger.info(
            "Registered capabilities: source=%s methods=%s filters=%s",
            name,
            [str(m) for m in capabilities.supported_methods],
            [f.name for f in capabilities.supported_filters],
        )

    def register_from_dict(self, data: dict[str, Any]) -> None:
        """Register from a raw dict (as returned by MCP search_capabilities tool).

        Handles both single-source format and multi-source format (browser server).
        """
        if "sources" in data:
            for source_data in data["sources"]:
                caps = SearchCapabilities(**source_data)
                self.register(caps)
        else:
            caps = SearchCapabilities(**data)
            self.register(caps)

    def get(self, source_name: str) -> SearchCapabilities | None:
        return self._capabilities.get(source_name)

    def all_sources(self) -> list[str]:
        return list(self._capabilities.keys())

    def source_labels_for_agent(self) -> list[str]:
        """Return user-facing labels for all sources, for the agent prompt.

        Uses display_label when set, otherwise humanized source_name.
        """
        result = []
        for name in sorted(self._capabilities.keys()):
            caps = self._capabilities[name]
            label = getattr(caps, "display_label", None) if caps else None
            if label and str(label).strip():
                result.append(str(label).strip())
            else:
                result.append(_humanize_source_name(name))
        return result

    def personal_sources(self) -> list[str]:
        return [
            name
            for name, caps in self._capabilities.items()
            if caps.source_class == SourceClass.PERSONAL
        ]

    def web_sources(self) -> list[str]:
        return [
            name
            for name, caps in self._capabilities.items()
            if caps.source_class == SourceClass.WEB
        ]

    def sources_supporting_method(self, method: str) -> list[str]:
        return [
            name
            for name, caps in self._capabilities.items()
            if method in [str(m) for m in caps.supported_methods]
        ]

    def sources_supporting_filter(self, filter_name: str) -> list[str]:
        return [
            name
            for name, caps in self._capabilities.items()
            if any(f.name == filter_name for f in caps.supported_filters)
        ]

    def can_handle(self, source_name: str, method: str) -> bool:
        caps = self._capabilities.get(source_name)
        if not caps:
            return False
        return method in [str(m) for m in caps.supported_methods]

    def supports_mode(self, source_name: str, mode: str) -> bool:
        caps = self._capabilities.get(source_name)
        if not caps:
            return False
        modes = getattr(caps, "supported_modes", None) or ["search"]
        return mode in modes

    def supports_group_by(self, source_name: str, field: str) -> bool:
        caps = self._capabilities.get(source_name)
        if not caps:
            return False
        fields = getattr(caps, "supported_group_by_fields", None) or []
        return field in fields
