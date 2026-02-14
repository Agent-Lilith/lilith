"""Extract sender (or other role) entity from search results for multi-hop follow-up steps.

Uses result metadata first (e.g. contact_push_name for WhatsApp, from for email);
falls back to LLM when metadata is missing or ambiguous.
"""

import re
from dataclasses import dataclass
from typing import Any

from src.contracts.mcp_search_v1 import SearchResultV1
from src.core.logger import logger

# Email "from" format: "Display Name <addr@domain>" or "addr@domain"
_FROM_PATTERN = re.compile(r"^(?:(.+?)\s*<([^>]+)>|(.+))$", re.IGNORECASE)


@dataclass
class ExtractedEntity:
    """Entity extracted from step results for use as filters in the next step."""

    from_name: str | None = None
    from_email: str | None = None

    def to_filters(self) -> list[dict[str, Any]]:
        """Filters for email (and other sources that support from_name/from_email)."""
        out: list[dict[str, Any]] = []
        if self.from_name and self.from_name.strip():
            out.append(
                {
                    "field": "from_name",
                    "operator": "contains",
                    "value": self.from_name.strip(),
                }
            )
        if self.from_email and self.from_email.strip():
            out.append(
                {
                    "field": "from_email",
                    "operator": "contains",
                    "value": self.from_email.strip(),
                }
            )
        return out

    def is_empty(self) -> bool:
        return not (self.from_name or self.from_email)


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


def extract_from_metadata(results: list[SearchResultV1]) -> ExtractedEntity:
    """Extract sender from result metadata. No LLM call."""
    entity = ExtractedEntity()
    for r in results:
        if not r.metadata:
            continue
        meta = r.metadata
        if r.source == "whatsapp":
            name = meta.get("contact_push_name")
            if name and str(name).strip():
                entity.from_name = str(name).strip()
                logger.debug(
                    "Entity from whatsapp metadata: from_name=%s", entity.from_name
                )
                return entity
        if r.source == "email":
            from_str = meta.get("from")
            if from_str:
                name, email = _parse_email_from(str(from_str))
                if name:
                    entity.from_name = name
                if email:
                    entity.from_email = email
                if not entity.is_empty():
                    logger.debug(
                        "Entity from email metadata: from_name=%s from_email=%s",
                        entity.from_name,
                        entity.from_email,
                    )
                    return entity
    return entity


async def extract_entity(
    results: list[SearchResultV1],
    llm_generate: Any = None,
) -> ExtractedEntity:
    """Extract sender entity from step results. Tries metadata first, then optional LLM."""
    entity = extract_from_metadata(results)
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
        return ExtractedEntity(from_name=name or None, from_email=email or None)
    # Plain name
    return ExtractedEntity(from_name=text)
