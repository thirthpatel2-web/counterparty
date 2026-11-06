"""Scores Hindsight's vendor briefings against the simulator's hidden truth. Uses no LLM tokens of our own.

Usage: python scripts/belief_study.py --seed 201        one study (live Hindsight if keys are set)
       python scripts/belief_study.py --report          rebuild the README's belief results only
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.arena import RUNS_DIR, schedule  # noqa: E402
from engine.beliefs import CHECKPOINTS, pooled_curve, run_belief_study  # noqa: E402
from engine.factory import build  # noqa: E402
from engine.memory import LocalMemory  # noqa: E402


def studies() -> list[dict]:
    out = []
    for path in sorted(RUNS_DIR.glob("beliefs-*-live.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("protocol") == "beliefs-v1" and "hindsight_neutral" not in data["curve"]:
            out.append(data)
    return out


def block(found: list[dict]) -> str:
    if not found:
        return "No live belief studies recorded yet."
    hs, ar = pooled_curve(found, "hindsight"), pooled_curve(found, "arithmetic")
    head = "| Deals seen per vendor | " + " | ".join(str(c) for c in CHECKPOINTS) + " |\n|" + " --- |" * (len(CHECKPOINTS) + 1)
    row = lambda name, curve: f"| {name} | " + " | ".join(f"{p['accuracy']:.0%}" for p in curve) + " |"
    spread = lambda curve: " | ".join(f"{p['low']:.0%}–{p['high']:.0%}" for p in curve)
    failures = sum(s["brief_failures"].get("hindsight", 0) for s in found)

    def tally(name: str, studies_: list[dict], max_checkpoint: int = 99) -> str:
        marks = [v for s in studies_ for r in s["rows"] if r["memory"] == name and r["checkpoint"] <= max_checkpoint
                 for v in r["score"].values()]
        right, wrong = sum(v is True for v in marks), sum(v is False for v in marks)
        return (f"{right} right, {wrong} confidently wrong, {len(marks) - right - wrong} unknown, out of {len(marks)}; "
                f"calibrated score {(right - wrong) / len(marks):+.2f}")

    partial = sorted(RUNS_DIR.glob("archive-beliefs-invalid/beliefs-*-live.json"))
    partial_note = ""
    if partial:
        p = json.loads(partial[0].read_text(encoding="utf-8"))
        partial_note = (f"\n\nStudy `{p['id']}` (seed 203) was invalidated when Hindsight Cloud ran out of credit "
                        "after checkpoint 3. Its valid checkpoints 1-3 alone give Hindsight "
                        f"{tally('hindsight', [p], 3)}, and arithmetic {tally('arithmetic', [p], 3)}.")
    return (f"{len(found)} live stud{'y' if len(found) == 1 else 'ies'}: "
            + ", ".join(f"`{s['id']}` ({s.get('backend') or 'Hindsight Cloud'})" for s in found)
            + f". {failures} failed briefings.\n\n"
            "Share of the 9 questions (3 per vendor) that the briefing answered correctly. Saying \"unknown\" counts "
            "as not correct.\n\n"
            f"{head}\n{row('Hindsight reflect briefing', hs)}\n{row('Exact arithmetic over the same records', ar)}\n\n"
            f"Range across studies for Hindsight: {spread(hs)}.\n\n"
            f"Across all checkpoints, Hindsight: {tally('hindsight', found)}. Arithmetic: "
            f"{tally('arithmetic', found)}. The calibrated score counts +1 per right answer and -1 per confidently "
            "wrong one, so guessing doesn't pay. Both curves rise as the same records arrive. Arithmetic is a "
            "hand-written rule over the same labels, not a ceiling."
            f"{partial_note}\n\n"
            "The questions: does the vendor bluff about \"final\"? At which round does it walk away? Is quarter-end "
            "cheaper? The answers are checked against the simulator's hidden traits.")


def write_readme(found: list[dict]) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    readme.write_text(re.sub(r"(<!-- BELIEFS -->).*?(<!-- /BELIEFS -->)",
                             lambda m: f"{m.group(1)}\n{block(found)}\n{m.group(2)}", text, flags=re.S),
                      encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=201)
    parser.add_argument("--deals", type=int, default=24)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--neutral", action="store_true", help="also test a bank with neutral skepticism (3 of 5)")
    args = parser.parse_args()
    if not args.report:
        stack = build("auto")
        memories = {"arithmetic": LocalMemory()}
        if stack.mode == "live":
            memories["hindsight"] = stack.memory
            if args.neutral:
                from engine.memory import HindsightMemory
                memories["hindsight_neutral"] = HindsightMemory(stack.memory.client, skepticism=3)
        print(f"Mode: {stack.mode}", flush=True)

        def show(event: dict) -> None:
            if event["type"] == "belief" and event["memory"] == "hindsight":
                print(f"after {event['checkpoint']} {event['vendor']} deals: {event['score']} {event['brief']}"[:300],
                      flush=True)

        import os
        backend = os.getenv("HINDSIGHT_BASE_URL") or "Hindsight Cloud"
        backend = "self-hosted Hindsight 1.2.0, LLM groq/openai/gpt-oss-20b" if "localhost" in backend else backend
        result = run_belief_study(schedule(args.deals, args.seed), memories, mode=stack.mode, emit=show,
                                  backend=backend if stack.mode == "live" else "")
        for name, curve in result["curve"].items():
            print(name, " ".join(f"{p['checkpoint']}:{p['accuracy']:.0%}" for p in curve), flush=True)
        if stack.mode != "live":
            print("Rehearsal: the README was left alone.")
            return 0
    write_readme(studies())
    print("README belief results rebuilt from every live study.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
