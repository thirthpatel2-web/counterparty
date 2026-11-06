"""A live, narrated retain -> recall -> reflect walkthrough for screen recording.

Plays three Apex Circuits deals, retains each outcome in a fresh Hindsight bank, then recalls the history and asks
reflect for a briefing before a fourth deal, which is played twice: once with no memory, once following the briefing.
Run it in a large terminal font while recording the screen:

    python scripts/live_demo.py
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from engine.arena import DealSpec, run_deal  # noqa: E402
from engine.brains import RuleBrain, ScriptedBrain  # noqa: E402
from engine.briefed import BriefedBrain  # noqa: E402
from engine.factory import build  # noqa: E402
from engine.memory import LocalMemory  # noqa: E402

PAUSE = 1.2


def say(text: str = "", pause: float = PAUSE) -> None:
    print(text, flush=True)
    time.sleep(pause)


def main() -> int:
    stack = build("auto")
    live = stack.mode == "live"
    memory = stack.memory if live else LocalMemory()
    say(f"Counterparty live demo · memory: {'Hindsight' if live else 'LOCAL REHEARSAL (no keys, not Hindsight)'}", 2)
    memory.reset()
    learner_memory = LocalMemory()
    deals = [DealSpec(i, "apex", m, 2025, 4200 + i) for i, m in ((1, 1), (2, 2), (3, 4))]
    for spec in deals:
        run_deal(spec, ScriptedBrain(), learner_memory, arm="learner")
        record = learner_memory.records[-1]
        say(f"\n> retain  deal {spec.index} with Apex Circuits", 0.6)
        say(f"  {record.to_text()}")
        memory.retain(record)
    say("\n> recall  \"How does Apex Circuits really negotiate?\"", 0.6)
    for item in memory.recall("apex", "Apex Circuits", 5)[:5]:
        say(f"  [{item.kind}] {item.text[:150]}", 0.5)
    say("\n> reflect  brief a buyer before the next Apex deal", 0.6)
    brief = memory.brief("apex", "Apex Circuits", 5)
    for key in ("final_claims", "walked_at_round", "safe_rounds", "lowest_close", "quarter_end", "advice"):
        say(f"  {key}: {brief.get(key)}", 0.5)
    spec = DealSpec(4, "apex", 5, 2025, 4204)
    plain = run_deal(spec, RuleBrain(), None, arm="rule")
    briefed = run_deal(spec, BriefedBrain(brief), None, arm="briefed")
    say("\n> next deal, same vendor, same seed", 0.6)
    say(f"  no memory: accepted the first \"final\" and paid ₹{plain.price:,}")
    say(f"  following the briefing: paid ₹{briefed.price:,}")
    say(f"  difference on this deal: ₹{plain.price - briefed.price:,}  (simulated vendor)", 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
