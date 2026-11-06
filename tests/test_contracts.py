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
    brief = {"final_claims": "usually bluffs", "safe_rounds": 5, "walked_at_round": 0, "lowest_close": 82_000,
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
    assert all(not isinstance(p["type"], list) for p in BRIEF_SCHEMA["properties"].values())  # 500s otherwise
    client.reflect.side_effect = RuntimeError("500")
    assert memory.brief("apex", "Apex Circuits", 4) == {} and memory.brief_failures == 1
    client.reflect.side_effect = None
    client.recall.return_value = SimpleNamespace(results=[])
    client.reflect.reset_mock()
    from engine.arena import DealSpec, run_deal
    from engine.brains import RuleBrain
    run_deal(DealSpec(1, "nova", 1, 2025, 3), RuleBrain(), memory)  # no history: no briefing call
    assert not client.reflect.called


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


def test_rate_limits_are_retried_but_other_errors_are_not(monkeypatch):
    import engine.memory as memory_module

    monkeypatch.setattr(memory_module.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("HTTP 429: Rate limit reached")
        return "ok"

    assert memory_module.patiently(flaky) == "ok" and len(calls) == 3
    calls.clear()

    def broke():
        calls.append(1)
        raise RuntimeError("402 Insufficient credits")

    try:
        memory_module.patiently(broke)
    except RuntimeError:
        pass
    assert len(calls) == 1
