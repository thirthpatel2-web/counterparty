"""Dashboard server: runs a race live, replays a recorded run, and prices a memory with Forget."""
import asyncio
import json
import pathlib
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from engine.arena import RUNS_DIR, DealSpec, deal_end_event, forget_value, run_race, schedule
from engine.factory import build
from engine.memory import DealRecord, LocalMemory
from engine.simulator import VENDOR_BY_SLUG, VENDORS

ROOT = pathlib.Path(__file__).resolve().parent
app = FastAPI(title="Counterparty")


class Session:
    """The one race the dashboard is showing. Events are append-only, so any browser can catch up."""
    def __init__(self):
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.running = False
        self.result: dict | None = None
        self.memory = None
        self.brain = None

    def emit(self, event: dict) -> None:
        with self.lock:
            self.events.append(event)

    def begin(self) -> None:
        with self.lock:
            self.events = []
            self.running = True
            self.result = None


session = Session()


class RaceRequest(BaseModel):
    deals: int = Field(default=24, ge=3, le=60)
    mode: str = "auto"
    seed: int = 7


class ReplayRequest(BaseModel):
    run: str
    pace: float = Field(default=0.06, ge=0, le=2)


class ForgetRequest(BaseModel):
    document_id: str = ""
    vendor: str = ""  # forget everything about this vendor instead of one deal
    seeds: int = Field(default=3, ge=1, le=10)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    stack = build("auto")
    return {"mode": stack.mode, "missing": stack.missing, "running": session.running,
            "vendors": [v.name for v in VENDORS], "runs": list_runs()}


def list_runs() -> list[dict]:
    rows = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        s = data["summary"]
        rows.append({"file": path.name, "mode": data["mode"], "deals": s["deals"], "mean_gap": s["mean_gap"]})
    return rows


@app.post("/api/race")
def start_race(request: RaceRequest):
    if session.running:
        raise HTTPException(409, "A race is already running.")
    stack = build(request.mode)
    if request.mode == "live" and stack.mode != "live":
        raise HTTPException(400, f"Live mode needs: {', '.join(stack.missing)}")
    session.begin()
    session.memory, session.brain = stack.memory, stack.brain_memory

    def work():
        try:
            session.emit({"type": "race_start", "mode": stack.mode, "total": request.deals})
            session.result = run_race(schedule(request.deals, request.seed), stack.brain_memory, stack.brain_twin,
                                      stack.memory, mode=stack.mode, emit=session.emit)
        except Exception as exc:
            session.emit({"type": "error", "message": f"Race stopped: {exc}"})
        finally:
            session.running = False

    threading.Thread(target=work, daemon=True).start()
    return {"mode": stack.mode}


@app.post("/api/replay")
def start_replay(request: ReplayRequest):
    if session.running:
        raise HTTPException(409, "A race is already running.")
    path = RUNS_DIR / pathlib.Path(request.run).name
    if not path.exists():
        raise HTTPException(404, "Recorded run not found.")
    data = json.loads(path.read_text(encoding="utf-8"))
    session.begin()
    stack = build(data["mode"])
    session.result = data
    session.brain = stack.brain_memory
    # Rehearsal memory lives in-process, so rebuild it. A live run's memory stays in its Hindsight bank.
    if data["mode"] == "live" and stack.mode == "live":
        stack.memory.bank_id = data["bank_id"]
        session.memory = stack.memory
    else:
        session.memory = rebuilt_memory(data)

    def work():
        try:
            session.emit({"type": "race_start", "mode": data["mode"], "total": data["summary"]["deals"],
                          "replay": path.name})
            shown_playbooks = {p["after_deal"]: [] for p in data["playbooks"]}
            for p in data["playbooks"]:
                shown_playbooks[p["after_deal"]].append(p)
            for n, pair in enumerate(data["pairs"], 1):
                spec = DealSpec(**pair["memory"]["spec"])
                vendor = VENDOR_BY_SLUG[spec.vendor_slug]
                session.emit({"type": "deal_start", "deal": spec.index, "vendor": vendor.name, "month": spec.month,
                              "year": spec.year, "quarter_end": spec.month % 3 == 0, "total": len(data["pairs"])})
                for line in pair["twin"]["transcript"] + pair["memory"]["transcript"]:
                    session.emit({"type": "message", **line})
                    time.sleep(request.pace)
                session.emit(deal_end_event(spec, pair["memory"], pair["twin"], pair["cumulative_saved"]))
                for p in shown_playbooks.get(n, []):
                    session.emit({"type": "playbook", **p})
            session.emit({"type": "race_end", "summary": data["summary"], "run_id": data["id"], "mode": data["mode"],
                          "llm_errors": data.get("llm_errors", 0), "fallbacks": data.get("fallbacks", 0)})
        finally:
            session.running = False

    threading.Thread(target=work, daemon=True).start()
    return {"mode": data["mode"]}


def rebuilt_memory(data: dict) -> LocalMemory:
    memory = LocalMemory()
    for pair in data["pairs"]:
        r = pair["memory"]["record"]
        memory.retain(DealRecord(**{**r, "final_claims": tuple(tuple(c) for c in r["final_claims"]),
                                    "false_claims": tuple(r["false_claims"])}))
    return memory


@app.get("/api/stream")
async def stream(after: int = 0):
    async def generate():
        index = after
        idle = 0
        while True:
            with session.lock:
                batch = session.events[index:]
            for event in batch:
                yield f"id: {index}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                index += 1
            if batch:
                idle = 0
            else:
                idle += 1
                if idle % 75 == 0:
                    yield ": keep-alive\n\n"
                if len(session.events) < index:  # a new race started: send the browser back to the start
                    yield 'data: {"type": "reset"}\n\n'
                    index = 0
            await asyncio.sleep(0.2)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/forget")
def forget(request: ForgetRequest):
    if session.running:
        raise HTTPException(409, "Wait for the race to finish.")
    if not session.result or session.memory is None:
        raise HTTPException(400, "Run or replay a race first.")
    records = [p["memory"]["record"] for p in session.result["pairs"]]
    chosen = [r for r in records if r["deal_id"] == request.document_id or r["vendor_slug"] == request.vendor]
    if not chosen:
        raise HTTPException(404, "That memory is not part of this run.")
    last = session.result["pairs"][-1]["memory"]["spec"]
    next_month = last["month"] % 12 + 1
    year = last["year"] + (1 if last["month"] == 12 else 0)
    spec = DealSpec(last["index"] + 1, chosen[0]["vendor_slug"], next_month, year, last["seed"] + 500)
    try:
        return forget_value(spec, {r["deal_id"] for r in chosen}, session.brain, session.memory, seeds=request.seeds)
    except Exception as exc:
        raise HTTPException(502, f"Could not rerun the deal: {exc}") from exc
