"""Run this first: one real call to each live service, so a bad key shows up now rather than on stage."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from engine.brains import DealState  # noqa: E402
from engine.factory import build  # noqa: E402
from engine.memory import DealRecord  # noqa: E402


def main() -> int:
    stack = build("auto")
    if stack.mode != "live":
        print("Missing in .env:", ", ".join(stack.missing))
        return 1
    failed = 0
    state = DealState("Apex Circuits", 1, 90_000, 2, True, "This is my final price.")
    try:
        decision = stack.brain_memory.decide(state, [], 1)
        ok = stack.brain_memory.llm_errors == 0
        print(f"Groq function call: {'OK' if ok else 'recovered after errors'} -> {decision.action}")
        failed += not ok
    except Exception as exc:
        print("Groq FAILED:", exc)
        failed += 1
    try:
        memory = stack.memory
        memory.reset()
        memory.retain(DealRecord("preflight", "apex", "Apex Circuits", 1, 2025, 98_000, 82_000, "closed", 5,
                                 ((2, 88_000),)))
        items = memory.recall("apex", "Apex Circuits", 2)
        print(f"Hindsight retain and recall: OK, bank {memory.bank_id}, {len(items)} memories")
        linked = all(i.document_id == "preflight" for i in items if i.kind != "observation")
        print("Recall links facts to their deal, needed by Forget:", "OK" if items and linked else "CHECK THIS")
        failed += not (items and linked)
        print("Hindsight reflect:", memory.playbook("apex", "Apex Circuits")[:160].encode("ascii", "replace").decode())
    except Exception as exc:
        print("Hindsight FAILED:", exc)
        failed += 1
    print("All good." if not failed else f"{failed} problem(s) to fix.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
