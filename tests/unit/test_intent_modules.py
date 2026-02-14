from src.contracts.mcp_search_v1 import SearchMode
from src.orchestrators.search.constants import IntentComplexity
from src.orchestrators.search.intent_modules import DeterministicIntentAnalyzer
from src.orchestrators.search.router import SourceMatch


def test_temporal_normalization_today():
    analyzer = DeterministicIntentAnalyzer()
    result = analyzer.analyze(
        query="Find emails from Alice today",
        source_matches=[SourceMatch(source="email", confidence=0.9, reasons=["token"])],
    )
    assert result.intent["temporal"] == "today"
    assert result.extractor_confidence["temporal"] > 0.9


def test_entity_extraction_sender_name():
    analyzer = DeterministicIntentAnalyzer()
    result = analyzer.analyze(
        query="Find messages from Alice",
        source_matches=[
            SourceMatch(source="whatsapp", confidence=0.8, reasons=["token"])
        ],
    )
    entities = result.intent["entities"]
    assert entities
    assert entities[0]["role"] == "sender"
    assert entities[0]["name"].lower() == "alice"


def test_query_type_detection_count():
    analyzer = DeterministicIntentAnalyzer()
    result = analyzer.analyze(
        query="How many emails from Bob yesterday?",
        source_matches=[
            SourceMatch(source="email", confidence=0.85, reasons=["token"])
        ],
    )
    assert result.intent["search_mode"] == SearchMode.COUNT
    assert result.extractor_confidence["query_type"] >= 0.9


def test_multihop_plan_from_fast_path_raises_complexity():
    analyzer = DeterministicIntentAnalyzer()
    result = analyzer.analyze(
        query="Find latest calendar items and latest email from that person",
        source_matches=[
            SourceMatch(source="calendar", confidence=0.85, reasons=["token"]),
            SourceMatch(source="email", confidence=0.81, reasons=["token"]),
        ],
        fast_path_intent={
            "retrieval_plan": [
                {
                    "step": "step_1",
                    "sources": ["calendar"],
                    "entity_from_previous": False,
                },
                {"step": "step_2", "sources": ["email"], "entity_from_previous": True},
            ]
        },
    )
    assert result.intent["complexity"] == IntentComplexity.MULTI_HOP
    assert result.intent["retrieval_plan"] is not None
    assert result.should_use_deterministic is True


def test_low_confidence_gates_to_llm():
    analyzer = DeterministicIntentAnalyzer()
    result = analyzer.analyze(query="What should I do?", source_matches=[])
    assert result.should_use_deterministic is False
