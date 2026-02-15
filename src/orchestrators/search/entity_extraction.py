"""Extract cross-step entities from search results for multi-hop follow-up steps.

Uses capability-declared metadata parsing rules first; falls back to LLM when
metadata is missing or ambiguous.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from src.contracts.mcp_search_v1 import (
    EntityExtractionRule,
    EntityValueParser,
    SearchResultV1,
)
from src.core.logger import logger

# Email "from" format: "Display Name <addr@domain>" or "addr@domain"
_FROM_PATTERN = re.compile(r"^(?:(.+?)\s*<([^>]+)>|(.+))$", re.IGNORECASE)


@dataclass
class ExtractedEntity:
    """Entity extracted from step results for use as filters in the next step."""

    filter_values: dict[str, str] = field(default_factory=dict)

    def to_filters(self) -> list[dict[str, Any]]:
        """Render extracted values as filter clauses for next-step routing."""
        out: list[dict[str, Any]] = []
        for field_name, value in self.filter_values.items():
            normalized = str(value or "").strip()
            if not normalized:
                continue
            out.append(
                {
                    "field": field_name,
                    "operator": "contains",
                    "value": normalized,
                }
            )
        return out

    def is_empty(self) -> bool:
        return not bool(self.filter_values)


def _parse_email_from(s: str) -> tuple[str | None, str | None]:
    """Parse 'Name <email>' or 'email' into (name, email)."""
    if not s or not s.strip():
        return None, None
    s = s.strip()
    m = _FROM_PATTERN.match(s)
    if not m:
        return s, None
    g1, g2, g3 = m.group(1), m.group(2), m.group(3)
    if g2:
        name = (g1 or "").strip() or None
        return name, (g2.strip() or None)
    if g3:
        # Plain email or plain name
        if "@" in g3:
            return None, g3.strip()
        return g3.strip(), None
    return None, None


def _extract_value_from_rule(
    metadata: dict[str, Any], rule: EntityExtractionRule
) -> str | None:
    raw = metadata.get(rule.metadata_key)
    if raw is None:
        return None
    if rule.parser == EntityValueParser.EMAIL_FROM_HEADER:
        name, email = _parse_email_from(str(raw))
        if rule.target_field == "from_name":
            return name
        if rule.target_field == "from_email":
            return email
        return None
    value = str(raw).strip()
    return value or None


def extract_from_metadata(
    results: list[SearchResultV1],
    capability_lookup: Any = None,
) -> ExtractedEntity:
    """Extract entity fields from result metadata via capability-declared rules."""
    entity = ExtractedEntity()
    for r in results:
        if not r.metadata:
            continue
        caps = capability_lookup(r.source) if capability_lookup else None
        rules = getattr(caps, "entity_extraction_rules", None) if caps else None
        if not rules:
            continue
        for rule in rules:
            value = _extract_value_from_rule(r.metadata, rule)
            if value:
                entity.filter_values[rule.target_field] = value
        if not entity.is_empty():
            logger.debug(
                "Entity from metadata using capability rules: source=%s fields=%s",
                r.source,
                sorted(entity.filter_values.keys()),
            )
            return entity
    return entity


async def extract_entity(
    results: list[SearchResultV1],
    capability_lookup: Any = None,
    llm_generate: Any = None,
) -> ExtractedEntity:
    """Extract entity from step results. Uses capability metadata rules first."""
    entity = extract_from_metadata(results, capability_lookup=capability_lookup)
    if not entity.is_empty():
        return entity
    if llm_generate and results:
        try:
            entity = await _extract_via_llm(results[:3], llm_generate)
        except Exception as e:
            logger.warning("Entity extraction LLM fallback failed: %s", e)
    return entity


async def _extract_via_llm(
    results: list[SearchResultV1],
    generate: Any,
) -> ExtractedEntity:
    """Use LLM to infer main person/contact name from snippets and provenance."""
    from src.core.prompts import load_search_prompt

    prompt_template = load_search_prompt("entity_extract")
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[{i}] source={r.source} title={r.title!r} provenance={r.provenance or ''} snippet={r.snippet[:200] if r.snippet else ''}"
        )
    context = "\n".join(parts)
    prompt = prompt_template.replace("{results}", context)
    logger.set_prompt_role("entity_extract")
    try:
        raw = await generate(prompt, max_tokens=100)
    finally:
        logger.set_prompt_role(None)
    text = (raw if isinstance(raw, str) else str(raw)).strip()
    return _parse_llm_entity(text)


def _parse_llm_name(text: str) -> str | None:
    """Parse a single name from LLM output (strip quotes, newlines, markdown)."""
    if not text:
        return None
    text = text.strip().strip("\"'")
    if "\n" in text:
        text = text.split("\n")[0].strip()
    if not text or len(text) > 200:
        return None
    return text


# Pattern for "Name (email)" format from LLM output
_LLM_NAME_EMAIL_PATTERN = re.compile(r"^(.+?)\s*\(([^)]+@[^)]+)\)$")


def _parse_llm_entity(text: str) -> ExtractedEntity:
    """Parse LLM entity output: 'name (email)', 'name', or 'NONE'."""
    if not text:
        return ExtractedEntity()
    text = text.strip().strip("\"'")
    if "\n" in text:
        text = text.split("\n")[0].strip()
    if (
        not text
        or text.upper() == "NONE"
        or text.lower() == "unknown"
        or len(text) > 200
    ):
        return ExtractedEntity()
    # Try "Name (email)" format
    m = _LLM_NAME_EMAIL_PATTERN.match(text)
    if m:
        name = m.group(1).strip()
        email = m.group(2).strip()
        values: dict[str, str] = {}
        if name:
            values["from_name"] = name
        if email:
            values["from_email"] = email
        return ExtractedEntity(filter_values=values)
    # Plain name
    return ExtractedEntity(filter_values={"from_name": text})
