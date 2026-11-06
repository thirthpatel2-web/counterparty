"""Day-one briefings from your own purchase history.

Give it a CSV of past negotiation notes (columns: vendor, date, notes). Each note is retained in a fresh Hindsight
bank as written, then Hindsight reflects and briefs you on every vendor: do they bluff about "final", when do
they walk away, are quarter-ends cheaper.

Usage: python scripts/import_history.py data/sample_purchase_history.csv
       python scripts/import_history.py --make-sample     writes a synthetic sample from the simulator
"""
import csv
import pathlib
import re
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.arena import run_deal, schedule  # noqa: E402
from engine.brains import ScriptedBrain  # noqa: E402
from engine.factory import build  # noqa: E402
from engine.memory import LocalMemory  # noqa: E402

SAMPLE = ROOT / "data" / "sample_purchase_history.csv"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def make_sample(path: pathlib.Path = SAMPLE, deals: int = 18, seed: int = 501) -> None:
    """Synthetic notes for the three simulated vendors, written the way a buyer would."""
    memory = LocalMemory()
    rows = []
    for spec in schedule(deals, seed):
        run_deal(spec, ScriptedBrain(), memory, arm="learner")
        record = memory.records[-1]
        rows.append({"vendor": record.vendor_name, "date": f"{record.year}-{record.month:02d}-15",
                     "notes": record.to_notes()})
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["vendor", "date", "notes"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} synthetic notes to {path}")


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--make-sample":
        make_sample()
        return 0
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    rows = list(csv.DictReader(pathlib.Path(sys.argv[1]).open(encoding="utf-8")))
    stack = build("auto")
    if stack.mode != "live":
        print("This needs Hindsight: notes are free text, and the rehearsal stand-in can't read them.")
        print("Add HINDSIGHT_API_KEY (and an LLM key) to .env.")
        return 1
    memory = stack.memory
    memory.reset()
    for n, row in enumerate(rows, 1):
        when = datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc).isoformat()
        memory.retain_note(slug(row["vendor"]), row["notes"], when, f"note-{n:04d}")
        print(f"Retained note {n}/{len(rows)}: {row['vendor']}", flush=True)
    print(f"\nBank {memory.bank_id}\n")
    for vendor in sorted({r["vendor"] for r in rows}):
        last_month = max(datetime.fromisoformat(r["date"]).month for r in rows if r["vendor"] == vendor)
        brief = memory.brief(slug(vendor), vendor, last_month % 12 + 1)
        print(f"{vendor}")
        for key in ("final_claims", "walked_at_round", "safe_rounds", "lowest_close", "quarter_end", "advice"):
            print(f"  {key}: {brief.get(key)}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
