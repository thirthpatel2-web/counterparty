"""Replays the pre-registered briefed buyer on every valid live belief study and writes the README section.

Usage: python scripts/briefed_buyer.py
"""
import json
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.arena import RUNS_DIR  # noqa: E402
from engine.briefed import replay_study  # noqa: E402


def studies() -> list[dict]:
    out = []
    for path in sorted(RUNS_DIR.glob("beliefs-*-live.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("protocol") == "beliefs-v1" and "hindsight_neutral" not in data["curve"]:
            out.append(data)
    return out


def money(v: int) -> str:
    return f"₹{abs(v):,} {'more' if v > 0 else 'less'}" if v else "the same"


def block(results: list[dict]) -> str:
    if not results:
        return "No valid live belief studies to replay yet."
    rows = []
    for r in results:
        for m in ("hindsight", "arithmetic"):
            if m in r["memories"]:
                s = r["memories"][m]
                rows.append(f"| `{r['study']}` | {'Hindsight' if m == 'hindsight' else 'Arithmetic'} briefings | "
                            f"₹{s['avg']:,} | ₹{r['rule_avg']:,} | ₹{r['oracle_avg']:,} | {money(s['vs_rule'])} | "
                            f"{money(s['vs_rule_last_third'])} | {s['lost']} |")
    hs = [r["memories"]["hindsight"] for r in results if "hindsight" in r["memories"]]
    last = round(statistics.fmean(s["vs_rule_last_third"] for s in hs)) if hs else 0
    overall = round(statistics.fmean(s["vs_rule"] for s in hs)) if hs else 0
    return (f"A buyer with no LLM that follows Hindsight's own briefings, replayed on the same deals as the rule. "
            f"Across {len(hs)} studies it paid {money(overall)} per deal overall than the rule, and "
            f"**{money(last)} per deal in the last third**, once it had learned. Early on it loses some deals "
            "finding where each vendor walks away. The same buyer following arithmetic over the same records is shown "
            "for comparison: where the two match, the saving comes from the knowledge, which Hindsight matched from labelled outcome "
            "records, not from Hindsight beating counting.\n\n"
            "| Study | Buyer | Avg paid | Rule avg | Oracle avg | Vs rule, all deals | Vs rule, last third | Deals lost |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n" + "\n".join(rows) +
            "\n\nThe policy and the comparison were committed in [EVALUATION.md](EVALUATION.md) before computing. "
            "The briefings are the ones Hindsight wrote during the live studies, so no new calls were made. "
            f"With {len(hs)} studies and 8 deals each in the last third, treat this as early evidence, not proof.")


def main() -> int:
    results = [replay_study(s) for s in studies()]
    for r in results:
        print(r["study"], {m: (s["vs_rule"], s["vs_rule_last_third"]) for m, s in r["memories"].items()})
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    readme.write_text(re.sub(r"(<!-- BRIEFED -->).*?(<!-- /BRIEFED -->)",
                             lambda m: f"{m.group(1)}\n{block(results)}\n{m.group(2)}", text, flags=re.S),
                      encoding="utf-8")
    (RUNS_DIR / "briefed-v1.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("README briefed-buyer section rebuilt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
