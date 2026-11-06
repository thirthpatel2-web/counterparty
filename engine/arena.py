"""Runs deals, the memory-versus-memoryless race, and the Forget counterfactual."""
from dataclasses import asdict, dataclass, field
import json
import pathlib
import random
import statistics
import time
from typing import Callable

from .brains import Brain, DealState
from .memory import DealRecord, Memory
from .simulator import VENDOR_BY_SLUG, VENDORS, Negotiation, is_quarter_end, lost_deal_price

MAX_ROUNDS = 12
RUNS_DIR = pathlib.Path(__file__).resolve().parents[1] / "runs"
Emit = Callable[[dict], None]


def no_emit(_event: dict) -> None:
    pass


@dataclass(frozen=True)
class DealSpec:
    index: int
    vendor_slug: str
    month: int
    year: int
    seed: int

    @property
    def deal_id(self) -> str:
        return f"deal-{self.index:03d}-{self.vendor_slug}-{self.year}-{self.month:02d}"


@dataclass
class DealResult:
    spec: dict
    outcome: str
    price: int
    opening: int
    rounds: int
    floor: int
    overpaid: int
    transcript: list
    memories_used: list = field(default_factory=list)
    record: dict = field(default_factory=dict)
    final_challenged: bool = False


def schedule(n_deals: int, seed: int = 7, start_year: int = 2025) -> list[DealSpec]:
    """One deal a month per vendor, with the vendor order shuffled inside each month."""
    rng = random.Random(seed)
    specs, index, month, year = [], 1, 1, start_year
    while len(specs) < n_deals:
        order = list(VENDORS)
        rng.shuffle(order)
        for vendor in order:
            if len(specs) == n_deals:
                break
            specs.append(DealSpec(index, vendor.slug, month, year, seed * 1000 + index))
            index += 1
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return specs


def run_deal(spec: DealSpec, brain: Brain, memory: Memory | None, *, exclude: frozenset = frozenset(),
             raw_facts_only: bool = False, retain: bool = True, seed: int | None = None, emit: Emit = no_emit,
             arm: str = "memory") -> DealResult:
    vendor = VENDOR_BY_SLUG[spec.vendor_slug]
    effective_seed = spec.seed if seed is None else seed
    talks = Negotiation(vendor, spec.month, effective_seed)
    items = memory.recall(vendor.slug, vendor.name, spec.month, exclude, raw_facts_only) if memory else []
    memory_texts = [m.text for m in items]
    reply = talks.opening()
    opening = reply.price
    transcript, history, final_claims, claims = [], [], [], []
    challenged = False

    def say(role: str, text: str, **extra) -> None:
        line = {"arm": arm, "deal": spec.index, "role": role, "text": text, **extra}
        transcript.append(line)
        emit({"type": "message", **line})

    say("vendor", reply.message, price=reply.price)
    history.append(f"Vendor: {reply.message}")
    outcome, price = "", reply.price
    while not outcome:
        if reply.claim:
            claims.append((reply.claim, reply.price))
        if reply.says_final:
            final_claims.append((talks.round, reply.price))
        state = DealState(vendor.name, spec.month, reply.price, talks.round, reply.says_final, reply.message,
                          list(history), lost_deal_price())
        decision = brain.decide(state, memory_texts, effective_seed)
        if decision.action == "accept" or talks.round >= MAX_ROUNDS:
            say("buyer", decision.message or "We accept.", action="accept")
            reply = talks.accept()
            say("vendor", reply.message, price=reply.price)
            outcome, price = "closed", reply.price
            break
        challenged = challenged or reply.says_final
        say("buyer", decision.message or f"We offer ₹{decision.price:,}.", action="offer", price=decision.price,
            against_final=reply.says_final)
        history.append(f"You: offered ₹{decision.price:,}. {decision.message}")
        reply = talks.counter(decision.price)
        say("vendor", reply.message, price=reply.price, final=reply.says_final, walked=reply.walked)
        history.append(f"Vendor: {reply.message}")
        if reply.deal:
            outcome, price = "closed", reply.price
        elif reply.walked:
            outcome, price = "vendor_walked", reply.price

    scored = price if outcome == "closed" else lost_deal_price()
    false_claims = tuple(c for c, at in claims if outcome == "closed" and price < at)
    record = DealRecord(spec.deal_id, vendor.slug, vendor.name, spec.month, spec.year, opening, price, outcome,
                        talks.round, tuple(final_claims), false_claims)
    if memory and retain:
        memory.retain(record)
    return DealResult(asdict(spec), outcome, scored, opening, talks.round, round(talks.floor),
                      scored - round(talks.floor), transcript,
                      [asdict(m) for m in items], asdict(record), challenged)


def bootstrap_ci(values: list[float], draws: int = 2000, seed: int = 11) -> tuple[float, float]:
    """95% confidence interval of the mean, resampling the paired per-deal differences."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws))
    return (means[int(0.025 * draws)], means[int(0.975 * draws) - 1])


def accept_first_final(spec: DealSpec) -> int:
    """The strongest fixed no-memory strategy we know: offer low, accept the first "final" price.

    It needs no LLM, so it is a baseline that wording in a prompt cannot weaken.
    """
    talks = Negotiation(VENDOR_BY_SLUG[spec.vendor_slug], spec.month, spec.seed)
    bid = talks.opening().price * 0.7
    while True:
        reply = talks.counter(bid)
        if reply.deal:
            return reply.price
        if reply.walked:
            return lost_deal_price()
        if reply.says_final:
            return talks.accept().price


def summarize(pairs: list[dict]) -> dict:
    gaps = [p["twin"]["price"] - p["memory"]["price"] for p in pairs]
    third = max(1, len(pairs) // 3)
    low, high = bootstrap_ci(gaps)
    per_vendor = {}
    for vendor in VENDORS:
        rows = [p for p in pairs if p["memory"]["spec"]["vendor_slug"] == vendor.slug]
        if rows:
            per_vendor[vendor.slug] = {
                "name": vendor.name, "deals": len(rows),
                "memory_avg": round(statistics.fmean(r["memory"]["price"] for r in rows)),
                "twin_avg": round(statistics.fmean(r["twin"]["price"] for r in rows)),
                "memory_lost": sum(r["memory"]["outcome"] != "closed" for r in rows),
                "twin_lost": sum(r["twin"]["outcome"] != "closed" for r in rows),
            }
    fixed = [p["memory"]["price"] - accept_first_final(DealSpec(**p["memory"]["spec"])) for p in pairs]
    return {
        "vs_fixed_mean": round(statistics.fmean(fixed)) if fixed else 0,
        "vs_fixed_ci95": [round(v) for v in bootstrap_ci(fixed)],
        "vs_fixed_last_third": round(statistics.fmean(fixed[-third:])) if fixed else 0,
        "deals": len(pairs), "total_saved": sum(gaps),
        "mean_gap": round(statistics.fmean(gaps)) if gaps else 0,
        "ci95": [round(low), round(high)],
        "first_third_gap": round(statistics.fmean(gaps[:third])) if gaps else 0,
        "last_third_gap": round(statistics.fmean(gaps[-third:])) if gaps else 0,
        "memory_lost": sum(p["memory"]["outcome"] != "closed" for p in pairs),
        "twin_lost": sum(p["twin"]["outcome"] != "closed" for p in pairs),
        "bluffs_challenged": sum(p["memory"]["final_challenged"] for p in pairs),
        "per_vendor": per_vendor,
    }


def deal_end_event(spec: DealSpec, mem: dict, twin: dict, cumulative: int) -> dict:
    r = mem["record"]
    record = DealRecord(**{**r, "final_claims": tuple(tuple(c) for c in r["final_claims"]),
                           "false_claims": tuple(r["false_claims"])})
    return {"type": "deal_end", "deal": spec.index, "vendor": record.vendor_name, "vendor_slug": spec.vendor_slug,
            "memory_price": mem["price"], "twin_price": twin["price"], "memory_outcome": mem["outcome"],
            "twin_outcome": twin["outcome"], "saved": twin["price"] - mem["price"], "cumulative_saved": cumulative,
            "memories_used": mem["memories_used"], "deal_id": spec.deal_id, "challenged": mem["final_challenged"],
            "memory_text": record.to_text(), "false_finals": sum(record.closing < p for _, p in record.final_claims)
            if record.outcome == "closed" else 0, "false_claims": len(record.false_claims),
            "walked": record.outcome != "closed"}


def run_race(specs: list[DealSpec], brain_memory: Brain, brain_twin: Brain, memory: Memory, *, mode: str,
             emit: Emit = no_emit, playbook_every: int = 6, save: bool = True) -> dict:
    memory.reset()
    pairs, playbooks = [], []
    cumulative = 0
    for n, spec in enumerate(specs, 1):
        vendor = VENDOR_BY_SLUG[spec.vendor_slug]
        emit({"type": "deal_start", "deal": spec.index, "vendor": vendor.name, "month": spec.month, "year": spec.year,
              "quarter_end": is_quarter_end(spec.month), "total": len(specs)})
        twin = run_deal(spec, brain_twin, None, retain=False, emit=emit, arm="twin")
        mem = run_deal(spec, brain_memory, memory, emit=emit, arm="memory")
        cumulative += twin.price - mem.price
        pairs.append({"memory": asdict(mem), "twin": asdict(twin), "cumulative_saved": cumulative})
        emit(deal_end_event(spec, asdict(mem), asdict(twin), cumulative))
        if n % playbook_every == 0 or n == len(specs):
            for v in VENDORS:
                try:
                    text = memory.playbook(v.slug, v.name)
                except Exception as exc:  # a failed reflection must not lose a finished race
                    text = f"Reflection unavailable: {exc}"
                entry = {"after_deal": n, "vendor": v.slug, "name": v.name, "text": text}
                playbooks.append(entry)
                emit({"type": "playbook", **entry})
    result = {"id": time.strftime("%Y%m%d-%H%M%S"), "mode": mode, "summary": summarize(pairs), "pairs": pairs,
              "playbooks": playbooks, "llm_errors": brain_memory.llm_errors + brain_twin.llm_errors,
              "fallbacks": getattr(brain_memory, "fallbacks", 0) + getattr(brain_twin, "fallbacks", 0),
              "specs": [asdict(s) for s in specs], "bank_id": getattr(memory, "bank_id", None)}
    if save:
        RUNS_DIR.mkdir(exist_ok=True)
        (RUNS_DIR / f"{result['id']}-{mode}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    emit({"type": "race_end", "summary": result["summary"], "run_id": result["id"], "mode": mode,
          "llm_errors": result["llm_errors"], "fallbacks": result["fallbacks"]})
    return result


def forget_value(spec: DealSpec, document_ids: str | set, brain: Brain, memory: Memory, seeds: int = 5) -> dict:
    """What remembered deals are worth: rerun the same deal with and without them, on the same seeds.

    Raw facts only on both sides, because a consolidated observation could still carry the forgotten deal.
    """
    forgotten = frozenset({document_ids} if isinstance(document_ids, str) else document_ids)
    rows = []
    for k in range(seeds):
        seed = spec.seed + 100 * (k + 1)
        kept = run_deal(spec, brain, memory, raw_facts_only=True, retain=False, seed=seed, arm="kept")
        gone = run_deal(spec, brain, memory, exclude=forgotten, raw_facts_only=True, retain=False,
                        seed=seed, arm="forgot")
        rows.append({"seed": seed, "with_memory": kept.price, "without_memory": gone.price,
                     "difference": gone.price - kept.price, "kept": kept.transcript, "forgot": gone.transcript})
    diffs = [r["difference"] for r in rows]
    return {"forgotten": sorted(forgotten), "deal": spec.deal_id, "seeds": seeds, "value": round(statistics.fmean(diffs)),
            "min": min(diffs), "max": max(diffs), "rows": rows}
