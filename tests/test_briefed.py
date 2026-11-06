from engine.arena import DealSpec, run_deal, schedule
from engine.beliefs import run_belief_study
from engine.brains import DealState, RuleBrain
from engine.briefed import BriefedBrain, brief_for, latest_briefs, replay_study
from engine.memory import LocalMemory


def state(**kw):
    base = dict(vendor_name="Apex Circuits", month=1, ask=90_000, round=2, says_final=True, vendor_message="final")
    return DealState(**{**base, **kw})


def test_no_briefing_is_exactly_the_rule():
    spec = DealSpec(1, "apex", 2, 2025, 9)
    assert run_deal(spec, BriefedBrain({}), None).price == run_deal(spec, RuleBrain(), None).price


def test_briefing_rules_follow_the_protocol():
    bluffs = BriefedBrain({"final_claims": "usually bluffs", "walked_at_round": 6, "safe_rounds": 5})
    assert bluffs.decide(state(round=2), [], 1).action == "offer"       # pushes past a "final"
    assert bluffs.decide(state(round=5), [], 1).action == "accept"      # never makes the round-6 offer
    honest = BriefedBrain({"final_claims": "usually honest", "walked_at_round": 3})
    assert honest.decide(state(round=2), [], 1).action == "accept"
    unknown_walk = BriefedBrain({"final_claims": "usually bluffs", "walked_at_round": 0, "safe_rounds": 4})
    assert unknown_walk.decide(state(round=4), [], 1).action == "offer"
    assert unknown_walk.decide(state(round=5), [], 1).action == "accept"


def test_only_briefings_written_before_a_deal_are_used():
    briefs = {"apex": [(3, {"a": 1}), (9, {"a": 2})]}
    assert brief_for(briefs, DealSpec(3, "apex", 1, 2025, 1)) == {}
    assert brief_for(briefs, DealSpec(4, "apex", 1, 2025, 1)) == {"a": 1}
    assert brief_for(briefs, DealSpec(10, "apex", 1, 2025, 1)) == {"a": 2}


def test_replay_scores_against_rule_and_oracle():
    study = run_belief_study(schedule(24, 201), {"arithmetic": LocalMemory()}, mode="rehearsal", save=False)
    study["curve"] = {"arithmetic": study["curve"]["arithmetic"]}
    result = replay_study(study)
    assert result["deals"] == 24 and result["oracle_avg"] <= result["rule_avg"]
    assert set(latest_briefs(study, "arithmetic")) == {"apex", "bharat", "nova"}
    assert "vs_rule_last_third" in result["memories"]["arithmetic"]
