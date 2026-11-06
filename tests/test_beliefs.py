from engine.arena import schedule
from engine.beliefs import CHECKPOINTS, pooled_curve, run_belief_study, score, truth
from engine.memory import LocalMemory


def test_truth_matches_the_hidden_traits():
    assert truth("apex")["final_claims"] == "usually bluffs" and truth("bharat")["final_claims"] == "usually honest"
    assert truth("bharat")["walked_at_round"] == {3, 4} and truth("nova")["quarter_end"] == "cheaper"
    assert truth("bharat")["quarter_end"] == "no difference"


def test_score_marks_right_wrong_and_unknown():
    assert score({"final_claims": "usually bluffs", "walked_at_round": 7, "quarter_end": "unknown"}, "apex") == \
        {"final_claims": True, "walked_at_round": True, "quarter_end": None}
    assert score({"final_claims": "usually honest", "walked_at_round": 0}, "apex")["final_claims"] is False


def test_study_scores_every_vendor_at_every_checkpoint_and_learns():
    result = run_belief_study(schedule(24, 201), {"arithmetic": LocalMemory()}, mode="rehearsal", save=False)
    assert {(r["vendor"], r["checkpoint"]) for r in result["rows"]} == \
        {(v, c) for v in ("apex", "bharat", "nova") for c in CHECKPOINTS}
    curve = [p["accuracy"] for p in result["curve"]["arithmetic"]]
    assert curve[-1] > curve[0]  # more evidence, better beliefs


def test_every_memory_sees_identical_evidence():
    a, b = LocalMemory(), LocalMemory()
    run_belief_study(schedule(12, 202), {"a": a, "b": b}, mode="rehearsal", save=False)
    assert [r.to_text() for r in a.records] == [r.to_text() for r in b.records]


def test_pooled_curve_reports_the_spread():
    studies = [{"curve": {"m": [{"accuracy": x} for x in (0.2, 0.4, 0.6, 0.8, 0.9, 1.0)]}},
               {"curve": {"m": [{"accuracy": x} for x in (0.4, 0.4, 0.6, 0.6, 0.9, 0.8)]}}]
    first = pooled_curve(studies, "m")[0]
    assert first == {"checkpoint": 1, "accuracy": 0.3, "low": 0.2, "high": 0.4, "studies": 2}
