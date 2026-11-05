"""Checks our calls against the real SDK signatures and models, without a network."""
from unittest.mock import create_autospec

from groq.resources.chat.completions import Completions
from hindsight_client import Hindsight
from hindsight_client_api.models.recall_result import RecallResult

from engine.brains import TOOLS, DealState, LLMBrain
from engine.memory import DealRecord, HindsightMemory
from types import SimpleNamespace


def test_hindsight_calls_match_the_real_client():
    client = create_autospec(Hindsight, instance=True)
    client.recall.return_value = SimpleNamespace(results=[
        RecallResult(id="f1", text="Apex bluffed", type="experience", document_id="deal-1"),
        RecallResult(id="f2", text="Other deal", type="world", document_id="deal-2")])
    client.reflect.return_value = SimpleNamespace(text="Apex bluffs early.")
    memory = HindsightMemory(client)
    memory.reset()
    assert memory.bank_id.startswith("counterparty-")
    kwargs = client.create_bank.call_args.kwargs
    assert kwargs["disposition_skepticism"] == 5
    memory.retain(DealRecord("deal-1", "apex", "Apex Circuits", 3, 2025, 98_000, 82_000, "closed", 5, ((2, 88_000),)))
    retained = client.retain.call_args
    assert retained.kwargs["document_id"] == "deal-1" and retained.kwargs["tags"] == ["vendor:apex"]
    assert retained.kwargs["timestamp"].startswith("2025-03-15")
    assert [m.id for m in memory.recall("apex", "Apex Circuits", 4)] == ["f1", "f2"]
    kept = memory.recall("apex", "Apex Circuits", 4, exclude=frozenset({"deal-1"}), raw_facts_only=True)
    assert [m.id for m in kept] == ["f2"]
    assert client.recall.call_args.kwargs["types"] == ["world", "experience"]
    assert memory.playbook("apex", "Apex Circuits") == "Apex bluffs early."


def test_groq_call_matches_the_real_client():
    create = create_autospec(Completions.create)
    call = SimpleNamespace(function=SimpleNamespace(name="accept", arguments='{"message": "OK"}'))
    create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])
    fake_self = object()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: create(fake_self, **kw))))
    state = DealState("Apex Circuits", 1, 90_000, 2, True, "final")
    assert LLMBrain(client).decide(state, [], 1).action == "accept"
    assert create.call_args.kwargs["tools"] == TOOLS and create.call_args.kwargs["tool_choice"] == "required"
