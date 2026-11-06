"""Does memory learn the truth about each vendor? Hindsight's briefings scored against the simulator's hidden traits.

A no-LLM learner plays the deals, so the evidence is the same for every memory being tested and costs no LLM
tokens. After each vendor's 1st, 2nd, 3rd, 4th, 6th and 8th deal, the memory is asked for a briefing, and each
claim in it is checked against what the vendor really does.
"""
from dataclasses import asdict
import json
import statistics
import time
import uuid

from .arena import RUNS_DIR, DealSpec, provenance, run_deal
from .brains import ScriptedBrain
from .memory import LocalMemory, Memory
from .simulator import VENDOR_BY_SLUG, VENDORS

CHECKPOINTS = (1, 2, 3, 4, 6, 8)
QUESTIONS = ("final_claims", "walked_at_round", "quarter_end")


def truth(vendor_slug: str) -> dict:
    """What a perfectly informed briefing would say, from the vendor's hidden traits."""
    v = VENDOR_BY_SLUG[vendor_slug]
    return {
        "final_claims": "usually bluffs" if v.false_final_at_round else "usually honest",
        "walked_at_round": {v.walks_after_rounds, v.walks_after_rounds + 1},  # patience varies by one round
        "quarter_end": "cheaper" if v.quarter_end_discount else "no difference",
    }


def score(brief: dict, vendor_slug: str) -> dict:
    """Per question: True (right), False (wrong) or None (said unknown). Abstaining is not counted as right."""
    real = truth(vendor_slug)
    out = {}
    for q in QUESTIONS:
        answer = brief.get(q)
        if answer in (None, 0, "", "unknown"):
            out[q] = None
        elif q == "walked_at_round":
            out[q] = int(answer) in real[q]
        else:
            out[q] = answer == real[q]
    return out


def accuracy(rows: list[dict], memory_name: str, checkpoint: int) -> dict:
    marks = [m for r in rows if r["memory"] == memory_name and r["checkpoint"] == checkpoint
             for m in r["score"].values()]
    answered = [m for m in marks if m is not None]
    return {"checkpoint": checkpoint, "correct": sum(answered), "answered": len(answered), "asked": len(marks),
            "accuracy": round(sum(answered) / len(marks), 3) if marks else 0.0}


def run_belief_study(specs: list[DealSpec], memories: dict[str, Memory], *, mode: str, emit=lambda e: None,
                     save: bool = True, backend: str = "", protocol: str = "beliefs-v1") -> dict:
    """Plays the deals once with the learner, writes every outcome into each memory, and scores their briefings."""
    for memory in memories.values():
        memory.reset()
    player_memory = LocalMemory()
    seen = {v.slug: 0 for v in VENDORS}
    rows = []
    for spec in specs:
        deal = run_deal(spec, ScriptedBrain(), player_memory, arm="learner")
        record = player_memory.records[-1]
        for memory in memories.values():
            memory.retain(record)
        seen[spec.vendor_slug] += 1
        n = seen[spec.vendor_slug]
        emit({"type": "belief_deal", "deal": spec.index, "vendor": record.vendor_name, "outcome": deal.outcome,
              "memory_text": record.to_text()})
        if n in CHECKPOINTS:
            for name, memory in memories.items():
                brief = memory.brief(spec.vendor_slug, record.vendor_name, (spec.month % 12) + 1)
                row = {"memory": name, "vendor": spec.vendor_slug, "checkpoint": n, "after_deal": spec.index,
                       "brief": brief, "score": score(brief, spec.vendor_slug)}
                rows.append(row)
                emit({"type": "belief", **row})
    curve = {name: [accuracy(rows, name, c) for c in CHECKPOINTS] for name in memories}
    result = {"id": f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}", "kind": "beliefs", "mode": mode,
              "protocol": protocol, "rows": rows, "curve": curve, "specs": [asdict(s) for s in specs],
              "brief_failures": {n: getattr(m, "brief_failures", 0) for n, m in memories.items()},
              "bank_id": {n: getattr(m, "bank_id", None) for n, m in memories.items()}, "backend": backend,
              "provenance": provenance("")}
    if save:
        RUNS_DIR.mkdir(exist_ok=True)
        (RUNS_DIR / f"beliefs-{result['id']}-{mode}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    emit({"type": "beliefs_end", "curve": curve, "run_id": result["id"]})
    return result


def pooled_curve(studies: list[dict], memory_name: str) -> list[dict]:
    """Accuracy per checkpoint across several studies, with each study's accuracy kept for the spread."""
    out = []
    for i, c in enumerate(CHECKPOINTS):
        per = [s["curve"][memory_name][i]["accuracy"] for s in studies if memory_name in s["curve"]]
        out.append({"checkpoint": c, "accuracy": round(statistics.fmean(per), 3) if per else 0.0,
                    "low": min(per, default=0.0), "high": max(per, default=0.0), "studies": len(per)})
    return out
