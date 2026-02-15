from src.contracts.mcp_search_v1 import (
    CapabilityTier,
    SearchCapabilities,
    SearchResultV1,
)
from src.orchestrators.search.entity_extraction import extract_from_metadata


def test_entity_extraction_uses_capability_rules_without_source_branches():
    caps_by_source = {
        "messages": SearchCapabilities(
            source_name="messages",
            supported_methods=["structured"],
            latency_tier=CapabilityTier.MEDIUM,
            quality_tier=CapabilityTier.MEDIUM,
            cost_tier=CapabilityTier.MEDIUM,
            entity_extraction_rules=[
                {
                    "target_field": "from_name",
                    "metadata_key": "sender",
                    "parser": "email_from_header",
                },
                {
                    "target_field": "from_email",
                    "metadata_key": "sender",
                    "parser": "email_from_header",
                },
            ],
        ),
    }

    result = SearchResultV1(
        id="1",
        source="messages",
        title="hello",
        snippet="",
        metadata={"sender": "Alice Example <alice@example.com>"},
    )

    entity = extract_from_metadata([result], capability_lookup=caps_by_source.get)
    filters = {f["field"]: f["value"] for f in entity.to_filters()}
    assert filters["from_name"] == "Alice Example"
    assert filters["from_email"] == "alice@example.com"
