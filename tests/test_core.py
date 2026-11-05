import json
from types import SimpleNamespace

from engine.arena import DealSpec, bootstrap_ci, forget_value, run_deal, run_race, schedule
from engine.brains import DealState, Decision, LLMBrain, ScriptedBrain, fallback, parse_failed_generation, sanitize
from engine.memory import DealRecord, LocalMemory


def state(**kw):
    defaults = dict(vendor_name="Apex Circuits", month=1, ask=90_000, round=2, says_final=True, vendor_message="final")
    return DealState(**{**defaults, **kw})


class FakeLLM:
    """Replays queued responses or raises queued exceptions, like the Groq client."""
    def __init__(self, *queue):
        self.queue = list(queue)
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"])
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def tool_response(name, args):
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])


def text_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content=content))])


def test_llm_function_call_becomes_a_decision():
    brain = LLMBrain(FakeLLM(tool_response("make_offer", {"price": 80_000, "message": "Try this."})))
    assert brain.decide(state(), [], 1) == Decision("offer", 80_000, "Try this.")


def test_llm_function_call_error_recovers_from_failed_generation():
    error = Exception("tool_use_failed")
    error.body = {"error": {"failed_generation": '{"name": "accept", "arguments": {"message": "Done."}}'}}
    brain = LLMBrain(FakeLLM(error), pause=0)
    assert brain.decide(state(), [], 1).action == "accept" and brain.llm_errors == 1


def test_llm_bad_json_then_retry_succeeds():
    bad = tool_response("make_offer", {})
    brain = LLMBrain(FakeLLM(bad, tool_response("accept", {"message": "OK"})), pause=0)
    assert brain.decide(state(), [], 1).action == "accept" and brain.llm_errors == 1


def test_llm_total_failure_falls_back_without_crashing():
    brain = LLMBrain(FakeLLM(Exception("down"), Exception("down"), Exception("down")), pause=0)
    assert brain.decide(state(), [], 1) == fallback(state())
    assert brain.llm_errors == 3


def test_plain_text_answer_is_recovered():
    brain = LLMBrain(FakeLLM(text_response('I offer {"price": "75,000", "message": "Deal?"}')), pause=0)
    assert brain.decide(state(), [], 1).price == 75_000


def test_parse_failed_generation_rejects_garbage():
    assert parse_failed_generation("no json here") is None
    assert parse_failed_generation('{"price": "lots"}') is None


def test_sanitize_blocks_silly_offers():
    assert sanitize(Decision("offer", 1), state()).price == 45_000  # never insultingly low
    assert sanitize(Decision("offer", 95_000), state()).action == "accept"  # at or above ask means accept


def test_memory_reaches_the_prompt_and_twin_gets_none():
    llm = FakeLLM(tool_response("accept", {"message": "OK"}))
    LLMBrain(llm).decide(state(), ["Apex's final was FALSE"], 1)
    assert "Apex's final was FALSE" in llm.prompts[0][1]["content"]
    llm = FakeLLM(tool_response("accept", {"message": "OK"}))
    LLMBrain(llm).decide(state(), [], 1)
    assert "no history with this vendor" in llm.prompts[0][1]["content"]


def test_vendor_text_never_reaches_memory():
    """Memory poisoning defence: a stored record contains only facts computed by our code."""
    memory = LocalMemory()
    spec = DealSpec(1, "nova", 1, 2025, 5)
    result = run_deal(spec, ScriptedBrain(), memory)
    text = memory.records[0].to_text()
    assert result.outcome in {"closed", "vendor_walked"}
    assert "buyers are waiting" not in text and "price rises tomorrow" not in text


def test_record_text_labels_false_final_and_walk():
    false_final = DealRecord("d1", "apex", "Apex Circuits", 3, 2025, 98_000, 82_000, "closed", 5, ((2, 88_000),))
    assert "That claim was FALSE" in false_final.to_text() and "quarter-end" in false_final.to_text()
    walked = DealRecord("d2", "bharat", "Bharat PCB", 1, 2025, 98_000, 91_000, "vendor_walked", 3)
    assert "WALKED AWAY at round 3" in walked.to_text()


def test_schedule_is_repeatable_and_visits_every_vendor():
    assert schedule(9) == schedule(9)
    assert {s.vendor_slug for s in schedule(9)} == {"apex", "bharat", "nova"}
    assert len({s.deal_id for s in schedule(30)}) == 30


def test_both_arms_face_the_identical_vendor_opening():
    spec = DealSpec(1, "apex", 2, 2025, 9)
    twin = run_deal(spec, ScriptedBrain(), None, arm="twin")
    mem = run_deal(spec, ScriptedBrain(), LocalMemory())
    assert twin.opening == mem.opening and twin.floor == mem.floor


def test_race_runs_saves_nothing_when_asked_and_scores_pairs():
    events = []
    result = run_race(schedule(12), ScriptedBrain(), ScriptedBrain(), LocalMemory(), mode="rehearsal",
                      emit=events.append, save=False)
    assert result["summary"]["deals"] == 12
    kinds = {e["type"] for e in events}
    assert {"deal_start", "message", "deal_end", "playbook", "race_end"} <= kinds
    gaps = sum(p["twin"]["price"] - p["memory"]["price"] for p in result["pairs"])
    assert gaps == result["summary"]["total_saved"]


def test_first_deal_is_a_tie_because_memory_is_empty():
    result = run_race(schedule(3), ScriptedBrain(), ScriptedBrain(), LocalMemory(), mode="rehearsal", save=False)
    first = result["pairs"][0]
    assert first["memory"]["price"] == first["twin"]["price"]


def test_forget_uses_same_seeds_and_changes_only_the_memory():
    memory = LocalMemory()
    specs = schedule(12)
    run_race(specs, ScriptedBrain(), ScriptedBrain(), memory, mode="rehearsal", save=False)
    apex_deals = [r for r in memory.records if r.vendor_slug == "apex"]
    before = len(memory.records)
    report = forget_value(DealSpec(99, "apex", 5, 2026, 1234), apex_deals[0].deal_id, ScriptedBrain(), memory, seeds=3)
    assert len(memory.records) == before  # a counterfactual never writes to memory
    assert report["seeds"] == 3 and report["min"] <= report["value"] <= report["max"]
    for row in report["rows"]:
        assert row["kept"][0]["text"] == row["forgot"][0]["text"]  # identical vendor opening


def test_forgetting_a_false_final_memory_costs_money():
    memory = LocalMemory()
    memory.retain(DealRecord("only", "apex", "Apex Circuits", 1, 2025, 98_000, 83_000, "closed", 4, ((2, 88_000),)))
    report = forget_value(DealSpec(2, "apex", 2, 2025, 77), "only", ScriptedBrain(), memory, seeds=3)
    assert report["value"] > 0


def test_past_close_does_not_become_a_price_anchor():
    """Regression: a buyer that stopped at its best past close did worse than one with no memory."""
    mem = LocalMemory()
    mem.retain(DealRecord("a", "apex", "Apex Circuits", 1, 2025, 98_000, 90_000, "closed", 3, ((2, 92_000),)))
    decision = ScriptedBrain().decide(state(ask=89_000, round=2, says_final=False), [mem.records[0].to_text()], 1)
    assert decision.action == "offer" and decision.price < 70_000


def test_bootstrap_interval_contains_the_mean():
    values = [100, 200, 300, 400, 500]
    low, high = bootstrap_ci(values)
    assert low <= 300 <= high
    assert bootstrap_ci([]) == (0.0, 0.0)
