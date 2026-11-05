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
    return (f"Run `{result['id']}`: {s['deals']} simulated deals, {result['llm_errors']} LLM errors, "
            f"{result['fallbacks']} fallback decisions.\n\n"
            f"- Average saved per deal: ₹{s['mean_gap']:,} (95% interval ₹{s['ci95'][0]:,} to ₹{s['ci95'][1]:,})\n"
            f"- First third of deals: ₹{s['first_third_gap']:,} per deal. Last third: ₹{s['last_third_gap']:,} per deal\n\n"
            "| Vendor | Deals | No memory, avg | With Hindsight, avg | Lost, no memory | Lost, with memory |\n"
            f"| --- | --- | --- | --- | --- | --- |\n{rows}\n\n"
            "Vendors are simulated, so these are not real procurement savings.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deals", type=int, default=24)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
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
        readme = ROOT / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(re.sub(r"(<!-- RESULTS -->).*?(<!-- /RESULTS -->)",
                                 lambda m: f"{m.group(1)}\n{results_block(result)}\n{m.group(2)}", text, flags=re.S),
                          encoding="utf-8")
        print("README updated with these results.")
    else:
        print("Rehearsal numbers are not results, so the README was left alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
