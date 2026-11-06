"""Dashboard server: runs a race, replays a recorded run, and prices memories with Forget."""
import asyncio
import json
import pathlib
import threading
import time
import uuid

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
    """The one race the dashboard shows. Every race gets a new generation, so a browser
    watching an old race resets as soon as a new one starts."""
    def __init__(self):
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.generation = ""
        self.running = False
        self.result: dict | None = None
        self.memory = None
        self.brain = None
        self.mode = ""

    def emit(self, event: dict) -> None:
        with self.lock:
            self.events.append({**event, "generation": self.generation})

    def try_begin(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.generation = uuid.uuid4().hex[:8]
            self.events = []
            self.result = None
            return True


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


@app.get("/api/beliefs")
def beliefs():
    """Every belief study, pooled per checkpoint, plus the latest briefing per vendor scored against the truth."""
    from engine.beliefs import QUESTIONS, pooled_curve, truth

    found, mode = [], "live"
    for wanted in ("live", "rehearsal"):
        for path in sorted(RUNS_DIR.glob(f"beliefs-*-{wanted}.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("protocol") == "beliefs-v1" and "hindsight_neutral" not in data["curve"]:
                found.append(data)  # the disposition experiment is reported on its own
        if found:
            mode = wanted
            break
    if not found:
        return {"studies": 0}
    names = [n for n in ("hindsight", "arithmetic") if n in found[-1]["curve"]]
    latest = {}
    for row in found[-1]["rows"]:
        if row["memory"] == names[0]:
            latest[row["vendor"]] = row
    vendors = []
    for slug, row in latest.items():
        real = truth(slug)
        vendors.append({"vendor": VENDOR_BY_SLUG[slug].name, "checkpoint": row["checkpoint"], "advice":
                        row["brief"].get("advice", ""), "answers": [
                            {"question": q, "said": row["brief"].get(q), "right": row["score"][q],
                             "truth": sorted(real[q]) if isinstance(real[q], set) else real[q]} for q in QUESTIONS]})
    return {"studies": len(found), "mode": mode, "ids": [s["id"] for s in found],
            "curves": {n: pooled_curve(found, n) for n in names}, "vendors": vendors}


def list_runs() -> list[dict]:
    rows = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("protocol") != "v2":
            continue
        s = data["summary"]
        rows.append({"file": path.name, "mode": data["mode"], "deals": s["deals"], "vs_rule": s["vs_rule"],
                     "fallbacks": data.get("fallbacks", 0)})
    return rows


@app.post("/api/race")
def start_race(request: RaceRequest):
    stack = build(request.mode)
    if request.mode == "live" and stack.mode != "live":
        raise HTTPException(400, f"Live mode needs: {', '.join(stack.missing)}")
    if not session.try_begin():
        raise HTTPException(409, "A race is already running.")
    session.memory, session.brain, session.mode = stack.memory, stack.brain_memory, stack.mode
    generation = session.generation

    def work():
        try:
            session.emit({"type": "race_start", "mode": stack.mode, "total": request.deals})
            session.result = run_race(schedule(request.deals, request.seed), stack.brain_memory, stack.memory,
                                      mode=stack.mode, emit=session.emit, model=stack.model, generation=generation)
        except Exception as exc:
            session.emit({"type": "error", "message": f"Race stopped: {exc}"})
        finally:
            session.running = False

    threading.Thread(target=work, daemon=True).start()
    return {"mode": stack.mode}


@app.post("/api/replay")
def start_replay(request: ReplayRequest):
    path = RUNS_DIR / pathlib.Path(request.run).name
    if path.suffix != ".json" or not path.is_file():
        raise HTTPException(404, "Recorded run not found.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("protocol") != "v2":
        raise HTTPException(400, "That run was recorded with an older protocol and can't be replayed.")
    if not session.try_begin():
        raise HTTPException(409, "A race is already running.")
    stack = build(data["mode"])
    session.result = data
    if data["mode"] == "live" and stack.mode == "live":
        stack.memory.bank_id = data["bank_id"]
        session.memory, session.brain, session.mode = stack.memory, stack.brain_memory, "live"
    elif data["mode"] == "live":
        session.memory, session.brain, session.mode = None, None, "replay-only"  # Forget needs the live services
    else:
        session.memory, session.brain, session.mode = rebuilt_memory(data), stack.brain_memory, "rehearsal"

    def work():
        try:
            session.emit({"type": "race_start", "mode": data["mode"], "total": data["summary"]["deals"],
                          "replay": path.name, "forget": session.mode})
            for pair in data["pairs"]:
                spec = DealSpec(**pair["memory"]["spec"])
                vendor = VENDOR_BY_SLUG[spec.vendor_slug]
                session.emit({"type": "deal_start", "deal": spec.index, "vendor": vendor.name, "month": spec.month,
                              "year": spec.year, "quarter_end": spec.month % 3 == 0, "total": len(data["pairs"])})
                for line in pair["baseline"]["transcript"] + pair["memory"]["transcript"]:
                    session.emit({"type": "message", **line})
                    time.sleep(request.pace)
                session.emit(deal_end_event(spec, pair["memory"], pair["baseline"], pair["cumulative_saved"]))
            session.emit({"type": "race_end", "summary": data["summary"], "run_id": data["id"], "mode": data["mode"],
                          "llm_errors": data.get("llm_errors", 0), "fallbacks": data.get("fallbacks", 0)})
        finally:
            session.running = False

    threading.Thread(target=work, daemon=True).start()
    return {"mode": data["mode"]}


def rebuilt_memory(data: dict) -> LocalMemory:
    memory = LocalMemory()
    for pair in data["pairs"]:
        memory.retain(DealRecord.from_dict(pair["memory"]["record"]))
    return memory


@app.get("/api/stream")
async def stream():
    async def generate():
        index, generation, idle = 0, None, 0
        while True:
            with session.lock:
                if session.generation != generation:
                    generation, index = session.generation, 0
                    yield 'data: {"type": "reset"}\n\n'
                batch = session.events[index:]
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            index += len(batch)
            idle = 0 if batch else idle + 1
            if idle and idle % 75 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/forget")
def forget(request: ForgetRequest):
    if session.running:
        raise HTTPException(409, "Wait for the race to finish.")
    if not session.result:
        raise HTTPException(400, "Run or replay a race first.")
    if session.memory is None:
        raise HTTPException(400, "Forget on a live run needs the live keys in .env, so it reruns with the real "
                                 "model and Hindsight. Rehearsal numbers would be misleading here.")
    records = [p["memory"]["record"] for p in session.result["pairs"]]
    chosen = [r for r in records if r["deal_id"] == request.document_id or r["vendor_slug"] == request.vendor]
    if not chosen:
        raise HTTPException(404, "That memory is not part of this run.")
    last = session.result["pairs"][-1]["memory"]["spec"]
    next_month = last["month"] % 12 + 1
    year = last["year"] + (1 if last["month"] == 12 else 0)
    spec = DealSpec(last["index"] + 1, chosen[0]["vendor_slug"], next_month, year, last["seed"] + 500)
    try:
        report = forget_value(spec, {r["deal_id"] for r in chosen}, session.brain, session.memory, seeds=request.seeds)
    except Exception as exc:
        raise HTTPException(502, f"Could not rerun the deal: {exc}") from exc
    return {**report, "mode": session.mode}
