"""Does what Hindsight learned turn into better prices? A no-LLM buyer that follows Hindsight's own briefings.

The briefings are the ones Hindsight wrote during the recorded belief studies, so this costs no new calls.
The policy is the one pre-registered in EVALUATION.md (briefed-v1).
"""
import statistics

from .arena import DealSpec, oracle_price, run_deal
from .brains import Decision, DealState, RuleBrain, sanitize


class BriefedBrain:
    """Follows one vendor briefing. With no briefing it is the rule."""
    llm_errors = 0
    fallbacks = 0

    def __init__(self, brief: dict | None):
        self.brief = brief or {}

    def decide(self, state: DealState, memories: list[str], seed: int) -> Decision:
        if not self.brief:
            return RuleBrain().decide(state, memories, seed)
        walk = int(self.brief.get("walked_at_round") or 0)
        if walk and state.round + 1 >= walk:
            return Decision("accept", message="We accept.")  # the next offer would be the walk-away round
        if self.brief.get("final_claims") == "usually bluffs":
            limit = walk - 1 if walk else int(self.brief.get("safe_rounds") or 0) + 1
            if state.round >= limit:
                return Decision("accept", message="We accept.")
            return sanitize(Decision("offer", int(state.ask * 0.7), "Your final price wasn't final last time."), state)
        if state.says_final:
            return Decision("accept", message="We accept your final price.")
        return sanitize(Decision("offer", int(state.ask * 0.7), "Can you do better?"), state)


def latest_briefs(study: dict, memory: str) -> dict:
    """vendor -> [(after_deal, brief)] in deal order, for one memory in a recorded study."""
    out: dict[str, list] = {}
    for row in study["rows"]:
        if row["memory"] == memory:
            out.setdefault(row["vendor"], []).append((row["after_deal"], row["brief"]))
    return {v: sorted(rows, key=lambda r: r[0]) for v, rows in out.items()}


def brief_for(briefs: dict, spec: DealSpec) -> dict:
    earlier = [b for after, b in briefs.get(spec.vendor_slug, []) if after < spec.index]
    return earlier[-1] if earlier else {}


def replay_study(study: dict) -> dict:
    """Prices for every deal of a study: rule, the buyer briefed by each memory, and the oracle."""
    specs = [DealSpec(**s) for s in study["specs"]]
    memories = [m for m in ("hindsight", "arithmetic") if m in study["curve"]]
    prices = {"rule": [run_deal(s, RuleBrain(), None, arm="rule").price for s in specs],
              "oracle": [oracle_price(s) for s in specs]}
    for m in memories:
        briefs = latest_briefs(study, m)
        prices[m] = [run_deal(s, BriefedBrain(brief_for(briefs, s)), None, arm=m).price for s in specs]
    third = max(1, len(specs) // 3)
    summary = {}
    for m in memories:
        diffs = [p - r for p, r in zip(prices[m], prices["rule"])]
        summary[m] = {"avg": round(statistics.fmean(prices[m])), "vs_rule": round(statistics.fmean(diffs)),
                      "vs_rule_last_third": round(statistics.fmean(diffs[-third:])),
                      "lost": sum(p >= 105_000 for p in prices[m])}
    return {"study": study["id"], "deals": len(specs), "rule_avg": round(statistics.fmean(prices["rule"])),
            "oracle_avg": round(statistics.fmean(prices["oracle"])), "memories": summary, "prices": prices}
