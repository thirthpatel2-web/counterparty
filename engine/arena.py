"""Runs deals, the race against baselines, and the Forget counterfactual."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import pathlib
import random
import statistics
import subprocess
import time
import uuid
from typing import Callable

from .brains import PROMPT_VERSION, SYSTEM_PROMPT, Brain, DealState, RuleBrain, ScriptedBrain
from .memory import DealRecord, LocalMemory, Memory, brief_text
from .simulator import VENDOR_BY_SLUG, VENDORS, Negotiation, is_quarter_end, lost_deal_price

MAX_ROUNDS = 12
ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
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
    brief: dict = field(default_factory=dict)


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


def run_deal(spec: DealSpec, brain: Brain, memory: Memory | None, *, retain: bool = True, seed: int | None = None,
             emit: Emit = no_emit, arm: str = "memory") -> DealResult:
    vendor = VENDOR_BY_SLUG[spec.vendor_slug]
    effective_seed = spec.seed if seed is None else seed
    talks = Negotiation(vendor, spec.month, effective_seed)
    items = memory.recall(vendor.slug, vendor.name, spec.month) if memory else []
    brief = memory.brief(vendor.slug, vendor.name, spec.month) if memory else {}
    memory_texts = ([brief_text(brief)] if brief else []) + [m.text for m in items]
    reply = talks.opening()
    opening = reply.price
    transcript, history, claims = [], [], []
    finals: list[list[int]] = []  # [round, price, lowest ask seen after it]
    challenged = accepted_final = False

    def say(role: str, text: str, **extra) -> None:
        line = {"arm": arm, "deal": spec.index, "role": role, "text": text, **extra}
        transcript.append(line)
        emit({"type": "message", **line})

    say("vendor", reply.message, price=reply.price)
    history.append(f"Vendor: {reply.message}")
    outcome, price = "", reply.price
    while not outcome:
        for claim in finals:
            claim[2] = min(claim[2], reply.price)
        if reply.claim:
            claims.append((reply.claim, reply.price))
        if reply.says_final:
            finals.append([talks.round, reply.price, reply.price])
        state = DealState(vendor.name, spec.month, reply.price, talks.round, reply.says_final, reply.message,
                          list(history), lost_deal_price())
        decision = brain.decide(state, memory_texts, effective_seed)
        if decision.action == "accept" or talks.round >= MAX_ROUNDS:
            accepted_final = reply.says_final
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
        if not reply.walked:
            for claim in finals:
                claim[2] = min(claim[2], reply.price)

    scored = price if outcome == "closed" else lost_deal_price()
    false_claims = tuple(c for c, at in claims if outcome == "closed" and price < at)
    record = DealRecord(spec.deal_id, vendor.slug, vendor.name, spec.month, spec.year, opening, price, outcome,
                        talks.round, tuple(tuple(f) for f in finals), false_claims, accepted_final)
    if memory and retain:
        memory.retain(record)
    return DealResult(asdict(spec), outcome, scored, opening, talks.round, round(talks.floor),
                      scored - round(talks.floor), transcript, [asdict(m) for m in items], asdict(record),
                      challenged, brief)


def oracle_price(spec: DealSpec) -> int:
    """The ceiling: a buyer that knows this deal's hidden floor and patience."""
    talks = Negotiation(VENDOR_BY_SLUG[spec.vendor_slug], spec.month, spec.seed)
    talks.opening()
    for _ in range(talks.walk_round - 1):
        reply = talks.counter(talks.floor)
        if reply.deal:
            return reply.price
        if talks.ask <= talks.floor + 1:
            break
    return talks.accept().price


def learner_prices(specs: list[DealSpec]) -> list[int]:
    """A learner with exact memory and hand-written rules, no LLM and no Hindsight. The bar memory must clear."""
    memory = LocalMemory()
    return [run_deal(s, ScriptedBrain(), memory, arm="learner").price for s in specs]


def bootstrap_ci(values: list[float], draws: int = 2000, seed: int = 11) -> tuple[float, float]:
    """95% interval of the mean from resampled values. Use per-run means across runs where possible."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws))
    return (means[int(0.025 * draws)], means[int(0.975 * draws) - 1])


def mean(values) -> int:
    values = list(values)
    return round(statistics.fmean(values)) if values else 0


def summarize(pairs: list[dict]) -> dict:
    """Positive "vs" numbers mean the memory buyer paid more than that comparison."""
    specs = [DealSpec(**p["memory"]["spec"]) for p in pairs]
    memory = [p["memory"]["price"] for p in pairs]
    rule = [p["baseline"]["price"] for p in pairs]
    learner = learner_prices(specs)
    oracle = [oracle_price(s) for s in specs]
    third = max(1, len(pairs) // 3)
    vs_rule = [m - r for m, r in zip(memory, rule)]
    per_vendor = {}
    for vendor in VENDORS:
        idx = [i for i, s in enumerate(specs) if s.vendor_slug == vendor.slug]
        if idx:
            per_vendor[vendor.slug] = {
                "name": vendor.name, "deals": len(idx), "memory_avg": mean(memory[i] for i in idx),
                "rule_avg": mean(rule[i] for i in idx), "learner_avg": mean(learner[i] for i in idx),
                "oracle_avg": mean(oracle[i] for i in idx),
                "memory_lost": sum(pairs[i]["memory"]["outcome"] != "closed" for i in idx),
                "rule_lost": sum(pairs[i]["baseline"]["outcome"] != "closed" for i in idx),
            }
    summary = {
        "deals": len(pairs), "memory_avg": mean(memory), "rule_avg": mean(rule), "learner_avg": mean(learner),
        "oracle_avg": mean(oracle), "vs_rule": mean(vs_rule), "vs_rule_ci95": [round(v) for v in bootstrap_ci(vs_rule)],
        "vs_rule_first_third": mean(vs_rule[:third]), "vs_rule_last_third": mean(vs_rule[-third:]),
        "vs_learner": mean(m - l for m, l in zip(memory, learner)),
        "memory_lost": sum(p["memory"]["outcome"] != "closed" for p in pairs),
        "rule_lost": sum(p["baseline"]["outcome"] != "closed" for p in pairs),
        "bluffs_challenged": sum(p["memory"]["final_challenged"] for p in pairs),
        "per_vendor": per_vendor,
    }
    if all(p.get("twin") for p in pairs):
        summary["vs_twin"] = mean(p["memory"]["price"] - p["twin"]["price"] for p in pairs)
    return summary


def deal_end_event(spec: DealSpec, mem: dict, baseline: dict, cumulative: int, generation: str = "") -> dict:
    record = DealRecord.from_dict(mem["record"])
    false_finals = sum(after < price for _, price, after in
                       (tuple(c) if len(c) == 3 else (*c, c[1]) for c in record.final_claims))
    return {"type": "deal_end", "generation": generation, "deal": spec.index, "vendor": record.vendor_name,
            "vendor_slug": spec.vendor_slug, "memory_price": mem["price"], "baseline_price": baseline["price"],
            "memory_outcome": mem["outcome"], "baseline_outcome": baseline["outcome"],
            "saved": baseline["price"] - mem["price"], "cumulative_saved": cumulative,
            "memories_used": mem["memories_used"], "brief": mem.get("brief", {}), "deal_id": spec.deal_id,
            "challenged": mem["final_challenged"], "memory_text": record.to_text(), "false_finals": false_finals,
            "false_claims": len(record.false_claims), "walked": record.outcome != "closed"}


def provenance(model: str) -> dict:
    """What produced a run, so results can be traced to the exact prompt and code."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "engine"], cwd=ROOT, capture_output=True,
                                    text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "", False
    return {"model": model, "prompt_version": PROMPT_VERSION,
            "prompt_sha": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12],
            "commit": commit, "engine_modified": dirty}


def run_race(specs: list[DealSpec], brain_memory: Brain, memory: Memory, *, mode: str, brain_twin: Brain | None = None,
             emit: Emit = no_emit, save: bool = True, model: str = "", generation: str = "") -> dict:
    """Memory buyer against the rule on every deal, with an optional memoryless LLM twin."""
    memory.reset()
    pairs = []
    cumulative = 0

    def tagged(event: dict) -> None:
        emit({**event, "generation": generation})

    for spec in specs:
        vendor = VENDOR_BY_SLUG[spec.vendor_slug]
        tagged({"type": "deal_start", "deal": spec.index, "vendor": vendor.name, "month": spec.month, "year": spec.year,
                "quarter_end": is_quarter_end(spec.month), "total": len(specs)})
        baseline = run_deal(spec, RuleBrain(), None, retain=False, emit=tagged, arm="baseline")
        twin = run_deal(spec, brain_twin, None, retain=False, arm="twin") if brain_twin else None
        mem = run_deal(spec, brain_memory, memory, emit=tagged, arm="memory")
        cumulative += baseline.price - mem.price
        pairs.append({"memory": asdict(mem), "baseline": asdict(baseline), "twin": asdict(twin) if twin else None,
                      "cumulative_saved": cumulative})
        emit(deal_end_event(spec, asdict(mem), asdict(baseline), cumulative, generation))
    errors = brain_memory.llm_errors + (brain_twin.llm_errors if brain_twin else 0)
    fallbacks = getattr(brain_memory, "fallbacks", 0) + (getattr(brain_twin, "fallbacks", 0) if brain_twin else 0)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    result = {"id": run_id, "mode": mode, "protocol": "v2", "summary": summarize(pairs), "pairs": pairs,
              "llm_errors": errors, "fallbacks": fallbacks, "specs": [asdict(s) for s in specs],
              "bank_id": getattr(memory, "bank_id", None), "brief_failures": getattr(memory, "brief_failures", 0),
              "provenance": provenance(model)}
    if save:
        RUNS_DIR.mkdir(exist_ok=True)
        (RUNS_DIR / f"{run_id}-{mode}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    tagged({"type": "race_end", "summary": result["summary"], "run_id": run_id, "mode": mode,
            "llm_errors": errors, "fallbacks": fallbacks})
    return result


def forget_value(spec: DealSpec, document_ids: str | set, brain: Brain, memory: Memory, seeds: int = 3) -> dict:
    """What remembered deals are worth: the same new deal, on the same vendor seeds, with full memory and with a
    copy of memory where those deals were really deleted. Both sides use the live recall and briefing."""
    forgotten = {document_ids} if isinstance(document_ids, str) else set(document_ids)
    without = memory.forgotten_copy(forgotten)
    rows = []
    for k in range(seeds):
        seed = spec.seed + 100 * (k + 1)
        kept = run_deal(spec, brain, memory, retain=False, seed=seed, arm="kept")
        gone = run_deal(spec, brain, without, retain=False, seed=seed, arm="forgot")
        rows.append({"seed": seed, "with_memory": kept.price, "without_memory": gone.price,
                     "difference": gone.price - kept.price, "kept": kept.transcript, "forgot": gone.transcript})
    diffs = [r["difference"] for r in rows]
    return {"forgotten": sorted(forgotten), "deal": spec.deal_id, "seeds": seeds, "value": mean(diffs),
            "min": min(diffs), "max": max(diffs), "rows": rows}
