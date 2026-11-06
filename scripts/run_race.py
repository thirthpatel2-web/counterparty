"""Records a race for the demo and writes its measured results into the README."""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.arena import run_race, schedule  # noqa: E402
from engine.factory import build  # noqa: E402


def results_block(result: dict) -> str:
    s = result["summary"]
    rows = "\n".join(f"| {v['name']} | {v['deals']} | ₹{v['twin_avg']:,} | ₹{v['memory_avg']:,} | {v['twin_lost']} "
                     f"| {v['memory_lost']} |" for v in s["per_vendor"].values())
    def versus(amount: int) -> str:
        return f"₹{abs(amount):,} {'more' if amount > 0 else 'less'}"

    low, high = s["vs_fixed_ci95"]
    return (f"Run `{result['id']}`: {s['deals']} simulated deals, {result['llm_errors']} LLM errors, "
            f"{result['fallbacks']} fallback decisions.\n\n"
            "**Against the strongest fixed no-memory strategy** (offer low, accept the first \"final\"), replayed "
            "on the same deals without an LLM, the Hindsight buyer paid "
            f"{versus(s['vs_fixed_mean'])} per deal (95% interval ₹{low:,} to ₹{high:,}; positive means it paid "
            f"more). In the last third of deals it paid {versus(s['vs_fixed_last_third'])} per deal.\n\n"
            "Against an LLM buyer with no memory and the same prompt: "
            f"₹{s['mean_gap']:,} saved per deal (95% interval ₹{s['ci95'][0]:,} to ₹{s['ci95'][1]:,}). "
            "Treat this as the weaker comparison, because that buyer's results depend heavily on prompt wording.\n\n"
            "| Vendor | Deals | No memory, avg | With Hindsight, avg | Lost, no memory | Lost, with memory |\n"
            f"| --- | --- | --- | --- | --- | --- |\n{rows}\n\n"
            "Vendors are simulated, so these are not real procurement savings.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deals", type=int, default=24)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--from-run", help="rewrite the README from a recorded live run instead of racing")
    args = parser.parse_args()
    if args.from_run:
        import json
        from engine.arena import summarize
        result = json.loads((ROOT / "runs" / pathlib.Path(args.from_run).name).read_text(encoding="utf-8"))
        result["summary"] = summarize(result["pairs"])
        write_readme(result)
        print(results_block(result).encode("ascii", "replace").decode())
        return 0
    stack = build("auto")
    print(f"Mode: {stack.mode}")

    def progress(event: dict) -> None:
        if event["type"] == "deal_end":
            print(f"Deal {event['deal']:>3} {event['vendor']:<16} no memory {event['twin_price']:>7,} "
                  f"memory {event['memory_price']:>7,} total saved {event['cumulative_saved']:>9,}", flush=True)

    result = run_race(schedule(args.deals, args.seed), stack.brain_memory, stack.brain_twin, stack.memory,
                      mode=stack.mode, emit=progress)
    print(results_block(result).encode("ascii", "replace").decode())
    if stack.mode == "live":
        write_readme(result)
        print("README updated with these results.")
    else:
        print("Rehearsal numbers are not results, so the README was left alone.")
    return 0


def write_readme(result: dict) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    readme.write_text(re.sub(r"(<!-- RESULTS -->).*?(<!-- /RESULTS -->)",
                             lambda m: f"{m.group(1)}\n{results_block(result)}\n{m.group(2)}", text, flags=re.S),
                      encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
