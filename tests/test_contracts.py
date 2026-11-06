"""Checks our calls against the real SDK signatures and models, without a network."""
from types import SimpleNamespace
from unittest.mock import create_autospec

from groq.resources.chat.completions import Completions
from hindsight_client import Hindsight
from hindsight_client_api.models.recall_result import RecallResult

from engine.brains import TOOLS, DealState, LLMBrain
from engine.memory import BRIEF_SCHEMA, DealRecord, HindsightMemory


def test_hindsight_calls_match_the_real_client():
    client = create_autospec(Hindsight, instance=True)
    client.recall.return_value = SimpleNamespace(results=[
        RecallResult(id="f1", text="Apex bluffed", type="experience", document_id="deal-1"),
        RecallResult(id="o1", text="Apex usually bluffs", type="observation")])
    brief = {"final_claims": "usually bluffs", "safe_rounds": 5, "walked_at_round": None, "lowest_close": 82_000,
             "quarter_end": "unknown", "advice": "Push past the first final."}
    client.reflect.return_value = SimpleNamespace(text="...", structured_output=brief)
    memory = HindsightMemory(client)
    memory.reset()
    assert memory.bank_id.startswith("counterparty-")
    assert client.create_bank.call_args.kwargs["disposition_skepticism"] == 5
    memory.retain(DealRecord("deal-1", "apex", "Apex Circuits", 3, 2025, 98_000, 82_000, "closed", 5,
                             ((2, 88_000, 84_000),)))
    retained = client.retain.call_args.kwargs
    assert retained["document_id"] == "deal-1" and retained["tags"] == ["vendor:apex", "quarter_end:yes"]
    assert retained["timestamp"].startswith("2025-03-15")
    items = memory.recall("apex", "Apex Circuits", 6)
    assert [(m.id, m.document_id, m.kind) for m in items] == [("f1", "deal-1", "experience"), ("o1", "", "observation")]
    assert "quarter-end" in client.recall.call_args.args[1]
    assert memory.brief("apex", "Apex Circuits", 4) == brief
    assert client.reflect.call_args.kwargs["response_schema"] == BRIEF_SCHEMA


def test_groq_call_matches_the_real_client():
    create = create_autospec(Completions.create)
    call = SimpleNamespace(function=SimpleNamespace(name="accept", arguments='{"message": "OK"}'))
    create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])
    fake_self = object()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: create(fake_self, **kw))))
    state = DealState("Apex Circuits", 1, 90_000, 2, True, "final")
    assert LLMBrain(client).decide(state, [], 1).action == "accept"
    kwargs = create.call_args.kwargs
    assert kwargs["tools"] == TOOLS and kwargs["tool_choice"] == "required" and kwargs["reasoning_effort"] == "low"
