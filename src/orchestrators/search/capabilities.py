"""Capability registry: caches MCP server capabilities for routing decisions.

At bootstrap, the agent calls search_capabilities on each MCP server and
registers the results here. Non-MCP backends register their capabilities directly.
"""

import logging
from typing import Any

from src.contracts.mcp_search_v1 import SearchCapabilities, SourceClass

logger = logging.getLogger(__name__)


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

    def personal_sources(self) -> list[str]:
        return [
            name for name, caps in self._capabilities.items()
            if caps.source_class == SourceClass.PERSONAL
        ]

    def web_sources(self) -> list[str]:
        return [
            name for name, caps in self._capabilities.items()
            if caps.source_class == SourceClass.WEB
        ]

    def sources_supporting_method(self, method: str) -> list[str]:
        return [
            name for name, caps in self._capabilities.items()
            if method in [str(m) for m in caps.supported_methods]
        ]

    def sources_supporting_filter(self, filter_name: str) -> list[str]:
        return [
            name for name, caps in self._capabilities.items()
            if any(f.name == filter_name for f in caps.supported_filters)
        ]

    def can_handle(self, source_name: str, method: str) -> bool:
        caps = self._capabilities.get(source_name)
        if not caps:
            return False
        return method in [str(m) for m in caps.supported_methods]
