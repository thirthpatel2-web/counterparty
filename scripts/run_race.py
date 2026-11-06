"""Records a race, and rebuilds the README's results from every valid recorded run.

Usage: python scripts/run_race.py --deals 24 --seed 101      record one run
       python scripts/run_race.py --report                    rebuild the README results only
"""
import argparse
import json
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.arena import RUNS_DIR, bootstrap_ci, run_race, schedule  # noqa: E402
from engine.brains import QuotaExhausted  # noqa: E402
from engine.factory import build  # noqa: E402


def valid_runs() -> list[dict]:
    runs = []
    for path in sorted(RUNS_DIR.glob("*-live.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("protocol") == "v2" and not data.get("fallbacks"):
            runs.append(data)
    return runs


def money(value: int) -> str:
    return f"₹{abs(value):,} {'more' if value > 0 else 'less'}" if value else "₹0 different"


def results_block(runs: list[dict]) -> str:
    if not runs:
        return "No valid live runs recorded yet under the current protocol."
    rows = []
    for r in runs:
        s, p = r["summary"], r.get("provenance", {})
        rows.append(f"| `{r['id']}` | {s['deals']} | {p.get('model', '')} | {p.get('prompt_version', '')} | "
                    f"₹{s['memory_avg']:,} | ₹{s['rule_avg']:,} | ₹{s['learner_avg']:,} | ₹{s['oracle_avg']:,} | "
                    f"{money(s['vs_rule'])} | {money(s['vs_rule_last_third'])} |")
    per_run = [r["summary"]["vs_rule"] for r in runs]
    low, high = bootstrap_ci(per_run) if len(runs) > 1 else tuple(runs[0]["summary"]["vs_rule_ci95"])
    verdict = ("beats" if high < 0 else "loses to" if low > 0 else "is not distinguishable from")
    return (f"{len(runs)} valid live run(s). Across them, the Hindsight buyer paid {money(round(statistics.fmean(per_run)))} "
            f"per deal than the rule (95% interval ₹{round(low):,} to ₹{round(high):,}), so it **{verdict}** the rule.\n\n"
            "| Run | Deals | Model | Prompt | Hindsight buyer avg | Rule avg | Learner avg | Oracle avg | "
            "Hindsight vs rule | Last third vs rule |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            + "\n".join(rows) +
            "\n\n- **Rule:** offer low and accept the first \"final\", with no memory and no LLM.\n"
            "- **Learner:** exact memory and hand-written rules, with no LLM and no Hindsight.\n"
            "- **Oracle:** knows each deal's hidden floor and patience. It's the ceiling.\n\n"
            "Every valid live run under the current protocol is listed here, and runs with any fallback decision "
            "are excluded. Vendors are simulated, so these are not real procurement savings.")


def write_readme(runs: list[dict]) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    readme.write_text(re.sub(r"(<!-- RESULTS -->).*?(<!-- /RESULTS -->)",
                             lambda m: f"{m.group(1)}\n{results_block(runs)}\n{m.group(2)}", text, flags=re.S),
                      encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deals", type=int, default=24)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--twin", action="store_true", help="also run a memoryless LLM buyer (doubles LLM use)")
    parser.add_argument("--report", action="store_true", help="only rebuild the README from recorded runs")
    args = parser.parse_args()
    if not args.report:
        stack = build("auto")
        print(f"Mode: {stack.mode} {stack.model}")

        def progress(event: dict) -> None:
            if event["type"] == "deal_end":
                print(f"Deal {event['deal']:>3} {event['vendor']:<16} rule {event['baseline_price']:>7,} "
                      f"memory {event['memory_price']:>7,}", flush=True)

        try:
            result = run_race(schedule(args.deals, args.seed), stack.brain_memory, stack.memory, mode=stack.mode,
                              brain_twin=stack.brain_twin if args.twin else None, emit=progress, model=stack.model)
        except QuotaExhausted as exc:
            print(f"Stopped: the LLM provider's daily quota is used up, so no result was recorded. {exc}"[:400])
            return 1
        s = result["summary"]
        print(f"Run {result['id']}: memory {s['memory_avg']:,}, rule {s['rule_avg']:,}, learner {s['learner_avg']:,}, "
              f"oracle {s['oracle_avg']:,}. Memory vs rule {s['vs_rule']:+,} per deal, last third "
              f"{s['vs_rule_last_third']:+,}. Errors {result['llm_errors']}, fallbacks {result['fallbacks']}.")
        if stack.mode != "live":
            print("Rehearsal numbers are not results, so the README was left alone.")
            return 0
    write_readme(valid_runs())
    print("README results rebuilt from every valid live run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
