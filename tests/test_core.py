import json
from types import SimpleNamespace

from engine.arena import DealSpec, bootstrap_ci, forget_value, run_deal, run_race, schedule
from engine.brains import (DealState, Decision, LLMBrain, RuleBrain, ScriptedBrain, fallback,
                           parse_failed_generation, sanitize)
from engine.memory import DealRecord, LocalMemory, brief_text


def state(**kw):
    defaults = dict(vendor_name="Apex Circuits", month=1, ask=90_000, round=2, says_final=True, vendor_message="final")
    return DealState(**{**defaults, **kw})


class FakeLLM:
    """Replays queued responses or raises queued exceptions, like the Groq client."""
    def __init__(self, *queue):
        self.queue = list(queue)
        self.prompts = []
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"])
        self.calls.append(kwargs)
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


def test_daily_quota_stops_the_run_instead_of_faking_decisions():
    import pytest
    from engine.brains import QuotaExhausted

    quota = Exception("Rate limit reached on tokens per day (TPD)")
    quota.status_code = 429
    brain = LLMBrain(FakeLLM(quota), pause=0)
    with pytest.raises(QuotaExhausted):
        brain.decide(state(), [], 1)
    assert brain.fallbacks == 0


def test_gpt_oss_uses_low_reasoning_effort_and_others_do_not():
    llm = FakeLLM(tool_response("accept", {"message": "OK"}))
    LLMBrain(llm).decide(state(), [], 1)
    assert llm.calls[0]["reasoning_effort"] == "low"
    llm = FakeLLM(tool_response("accept", {"message": "OK"}))
    LLMBrain(llm, model="nvidia/llama-3.3-nemotron-super-49b-v1.5", tool_choice="auto").decide(state(), [], 1)
    assert "reasoning_effort" not in llm.calls[0] and llm.calls[0]["tool_choice"] == "auto"


def test_provider_presets_pick_the_right_key(monkeypatch):
    from engine import factory

    monkeypatch.setenv("HINDSIGHT_API_KEY", "x")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert factory.missing_keys() == ["NVIDIA_API_KEY"]
    assert factory.build().mode == "rehearsal"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert factory.missing_keys() == ["LLM_BASE_URL"]


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


def test_record_text_labels_finals_from_what_the_vendor_did_next():
    """Regression: a bluff in a deal that ended in a walk-away was once stored as TRUE."""
    bluff_then_walk = DealRecord("d1", "apex", "Apex Circuits", 3, 2025, 98_000, 83_000, "vendor_walked", 6,
                                 ((2, 88_000, 83_000),))
    text = bluff_then_walk.to_text()
    assert "That claim was FALSE: the vendor later went down to ₹83,000" in text and "quarter-end" in text
    held = DealRecord("d2", "bharat", "Bharat PCB", 1, 2025, 98_000, 91_000, "vendor_walked", 3, ((2, 91_000, 91_000),))
    assert "did not go lower" in held.to_text() and "WALKED AWAY at round 3" in held.to_text()
    untested = DealRecord("d3", "apex", "Apex Circuits", 1, 2025, 98_000, 88_000, "closed", 2, ((2, 88_000, 88_000),),
                          (), True)
    assert "UNKNOWN" in untested.to_text()


def test_memory_stores_outcomes_never_lessons():
    walked = DealRecord("d", "bharat", "Bharat PCB", 1, 2025, 98_000, 91_000, "vendor_walked", 3)
    assert "Next time" not in walked.to_text() and "accept before" not in walked.to_text()


def test_old_two_part_claims_still_load():
    old = DealRecord.from_dict({"deal_id": "x", "vendor_slug": "apex", "vendor_name": "Apex Circuits", "month": 1,
                                "year": 2025, "opening": 98_000, "closing": 82_000, "outcome": "closed", "rounds": 5,
                                "final_claims": [[2, 88_000]], "false_claims": []})
    assert "did not go lower" in old.to_text()


def test_run_deal_tracks_the_lowest_ask_after_a_final():
    memory = LocalMemory()
    result = run_deal(DealSpec(1, "apex", 1, 2025, 4), ScriptedBrain(), memory)  # no history: pushes once past final
    claims = memory.records[0].final_claims
    assert claims and claims[0][2] < claims[0][1]
    assert "FALSE" in memory.records[0].to_text() and result.outcome == "closed"


def test_local_brief_summarises_records():
    memory = LocalMemory()
    memory.retain(DealRecord("a", "apex", "Apex Circuits", 1, 2025, 98_000, 82_000, "closed", 5, ((2, 88_000, 82_000),)))
    memory.retain(DealRecord("b", "apex", "Apex Circuits", 2, 2025, 98_000, 84_000, "vendor_walked", 6))
    brief = memory.brief("apex", "Apex Circuits", 3)
    assert brief["final_claims"] == "usually bluffs" and brief["walked_at_round"] == 6 and brief["safe_rounds"] == 5
    assert "Briefing from memory" in brief_text(brief) and memory.brief("nova", "Nova", 1) == {}


def test_schedule_is_repeatable_and_visits_every_vendor():
    assert schedule(9) == schedule(9)
    assert {s.vendor_slug for s in schedule(9)} == {"apex", "bharat", "nova"}
    assert len({s.deal_id for s in schedule(30)}) == 30


def test_all_arms_face_the_identical_vendor():
    spec = DealSpec(1, "apex", 2, 2025, 9)
    rule = run_deal(spec, RuleBrain(), None, arm="baseline")
    mem = run_deal(spec, ScriptedBrain(), LocalMemory())
    assert rule.opening == mem.opening and rule.floor == mem.floor


def test_race_scores_memory_against_rule_learner_and_oracle():
    events = []
    result = run_race(schedule(12), ScriptedBrain(), LocalMemory(), mode="rehearsal", emit=events.append, save=False,
                      generation="g1")
    s = result["summary"]
    assert s["deals"] == 12 and "vs_twin" not in s
    assert s["oracle_avg"] <= min(s["rule_avg"], s["learner_avg"], s["memory_avg"])
    assert s["vs_rule"] == round(sum(p["memory"]["price"] - p["baseline"]["price"] for p in result["pairs"]) / 12)
    assert {"deal_start", "message", "deal_end", "race_end"} <= {e["type"] for e in events}
    assert all(e["generation"] == "g1" for e in events)
    assert result["provenance"]["prompt_sha"] and result["protocol"] == "v2"


def test_optional_twin_is_reported():
    result = run_race(schedule(3), ScriptedBrain(), LocalMemory(), mode="rehearsal", brain_twin=ScriptedBrain(),
                      save=False)
    assert "vs_twin" in result["summary"]


def test_forget_deletes_from_a_copy_and_reruns_on_the_same_vendor_seeds():
    memory = LocalMemory()
    run_race(schedule(12), ScriptedBrain(), memory, mode="rehearsal", save=False)
    apex = [r.deal_id for r in memory.records if r.vendor_slug == "apex"]
    before = len(memory.records)
    report = forget_value(DealSpec(99, "apex", 5, 2026, 1234), set(apex), ScriptedBrain(), memory, seeds=3)
    assert len(memory.records) == before  # the original memory is untouched
    assert report["seeds"] == 3 and report["min"] <= report["value"] <= report["max"]
    for row in report["rows"]:
        assert row["kept"][0]["text"] == row["forgot"][0]["text"]  # identical vendor opening


def test_forgetting_a_bluff_memory_costs_money():
    memory = LocalMemory()
    memory.retain(DealRecord("only", "apex", "Apex Circuits", 1, 2025, 98_000, 83_000, "closed", 4, ((2, 88_000, 83_000),)))
    report = forget_value(DealSpec(2, "apex", 2, 2025, 77), "only", ScriptedBrain(), memory, seeds=3)
    assert report["value"] > 0


def test_past_close_does_not_become_a_price_anchor():
    """Regression: a buyer that stopped at its best past close did worse than one with no memory."""
    mem = LocalMemory()
    mem.retain(DealRecord("a", "apex", "Apex Circuits", 1, 2025, 98_000, 90_000, "closed", 3, ((2, 92_000, 90_000),)))
    decision = ScriptedBrain().decide(state(ask=89_000, round=2, says_final=False), [mem.records[0].to_text()], 1)
    assert decision.action == "offer" and decision.price < 70_000


def test_bootstrap_interval_contains_the_mean():
    low, high = bootstrap_ci([100, 200, 300, 400, 500])
    assert low <= 300 <= high
    assert bootstrap_ci([]) == (0.0, 0.0)
