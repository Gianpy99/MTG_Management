"""REST API for the Forge engine (LAN service, port 8787)."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import config
from .cards import CardIndex, Issue, normalise, parse_dck, structure_issues
from .store import PRIORITIES, Store
from .worker import Worker, detect_forge_version, detect_java_version

DECK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class State:
    store: Store
    worker: Worker
    index: CardIndex | None = None
    index_seconds: float = 0.0
    java_version = ""
    forge_version = ""
    forge_build = ""


S = State()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    S.store = Store(config.db_path())
    S.store.requeue_interrupted()
    res = config.FORGE_HOME / "res"
    if res.is_dir():
        t0 = time.monotonic()
        S.index = CardIndex.load(res)
        S.index_seconds = round(time.monotonic() - t0, 1)
    build = config.FORGE_HOME / "build.txt"
    S.forge_build = build.read_text("utf-8").strip() if build.is_file() else ""
    S.java_version = detect_java_version()
    S.forge_version = detect_forge_version()
    S.worker = Worker(S.store, S.java_version, S.forge_version)
    S.worker.start()
    yield


app = FastAPI(title="MTG Forge Engine", version="1.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class DeckIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dck: str = Field(min_length=1)
    ref: Optional[str] = Field(default=None, max_length=120)
    version: Optional[str] = Field(default=None, max_length=60)


# A plain string refers to one of the bundled test decks (e.g. "Abzan_Food").
DeckRef = Union[str, DeckIn]
Format = Literal["constructed", "commander"]
Priority = Literal["HIGH", "NORMAL", "LOW"]


class SimulationIn(BaseModel):
    deck_a: DeckRef
    deck_b: DeckRef
    format: Optional[Format] = None
    games: int = Field(default=10, ge=1)
    priority: Priority = "NORMAL"
    seed: Optional[int] = Field(default=None, ge=0, le=2**62)
    allow_invalid: bool = False


class AuditIn(BaseModel):
    deck: DeckRef
    format: Optional[Format] = None
    games: int = Field(default=1, ge=1, le=10)
    priority: Priority = "HIGH"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve(ref: DeckRef) -> dict:
    if isinstance(ref, str):
        if not DECK_NAME_RE.match(ref):
            raise HTTPException(422, f"Invalid test deck name: {ref!r}")
        path = config.TEST_DECKS_DIR / f"{ref}.dck"
        if not path.is_file():
            raise HTTPException(404, f"Unknown test deck: {ref}")
        return {"name": ref, "text": path.read_text("utf-8"), "ref": ref, "source": "test", "version": None}
    if len(ref.dck.encode("utf-8")) > config.MAX_DCK_BYTES:
        raise HTTPException(413, "Deck file too large")
    return {"name": ref.name, "text": ref.dck, "ref": ref.ref, "source": "inline", "version": ref.version}


def _job_id(kind: str) -> str:
    prefix = "sim" if kind == "simulation" else "audit"
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"


def _submit(kind: str, decks: list[tuple[str, dict]], fmt: str | None, games: int, priority: str,
            seed: int | None, allow_invalid: bool) -> dict:
    if games > config.MAX_GAMES:
        raise HTTPException(422, f"games must be <= {config.MAX_GAMES}")
    if S.store.queue_length() >= config.MAX_QUEUED_JOBS:
        raise HTTPException(429, "Simulation queue is full, try again later")

    parsed = {side: parse_dck(d["text"]) for side, d in decks}
    detected = {side: ("commander" if p.has_commander else "constructed") for side, p in parsed.items()}
    if fmt is None:
        if len(set(detected.values())) > 1:
            raise HTTPException(422, "Decks have different formats (Commander vs Constructed)")
        fmt = detected[decks[0][0]]

    job_id = _job_id(kind)
    workdir = config.DATA_DIR / "jobs" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "decks").mkdir(parents=True, exist_ok=True)

    issues: list[dict] = []
    deck_rows: list[dict] = []
    for side, d in decks:
        p = parsed[side]
        if not p.name:
            p.name = d["name"]
        found = structure_issues(p, fmt)
        forge_text, card_issues = normalise(p, S.index)
        if S.index is None:
            card_issues.append(_info("card_index_unavailable", "Forge card index not loaded; cards not pre-checked"))
        for issue in found + card_issues:
            issues.append({**issue.to_dict(), "side": side})
        (workdir / f"deck_{side}.dck").write_text(forge_text, encoding="utf-8")
        raw_hash = _sha(d["text"])
        archived = config.DATA_DIR / "decks" / f"{raw_hash}.dck"
        if not archived.exists():
            archived.write_text(d["text"], encoding="utf-8")
        deck_rows.append({
            "side": side, "deck_name": d["name"], "deck_ref": d["ref"], "deck_source": d["source"],
            "deck_version": d["version"], "deck_hash": raw_hash, "forge_dck_hash": _sha(forge_text),
            "card_count": p.count("main") + p.count("commander"),
        })
    if kind == "audit":
        shutil.copyfile(workdir / "deck_a.dck", workdir / "deck_b.dck")

    errors = [i for i in issues if i["severity"] == "ERROR"]
    if kind == "simulation" and errors and not allow_invalid:
        shutil.rmtree(workdir, ignore_errors=True)
        return JSONResponse(status_code=422, content={
            "detail": "Deck structure is invalid; fix it, run an audit, or pass allow_invalid=true",
            "issues": errors,
        })

    S.store.create_job({
        "id": job_id, "kind": kind, "status": "QUEUED", "priority": PRIORITIES[priority], "format": fmt,
        "games_requested": games, "clock_seconds": config.GAME_CLOCK_SECONDS[fmt], "seed": seed,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, deck_rows, issues)
    S.worker.wake()
    return {"job_id": job_id, "status": "queued", "format": fmt,
            "issues": {"ERROR": len(errors), "RED": sum(i["severity"] == "RED" for i in issues)}}


def _info(issue_type: str, message: str) -> Issue:
    return Issue("INFO", issue_type, message)


def _get(job_id: str) -> dict:
    job = S.store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _meminfo() -> dict:
    try:
        vals = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                vals[key] = int(rest.split()[0]) // 1024
        return {"total_mb": vals.get("MemTotal"), "available_mb": vals.get("MemAvailable")}
    except OSError:
        return {}


def _platform() -> str:
    machine = platform.machine().lower()
    return f"{platform.system().lower()}-{ {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(machine, machine) }"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    checks = {
        "database": True,
        "worker": S.worker.is_alive(),
        "forge_jar": config.forge_jar() is not None,
        "java": bool(S.java_version),
        "card_index": S.index is not None,
    }
    try:
        S.store.queue_length()
    except Exception:
        checks["database"] = False
    ok = all(checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", **checks})


@app.get("/api/forge/status")
def forge_status():
    current = S.store.get_job(S.worker.current_job) if S.worker.current_job else None
    return {
        "status": "ready" if S.worker.is_alive() and config.forge_jar() and S.java_version else "degraded",
        "forge_version": S.forge_version,
        "forge_build": S.forge_build,
        "java_version": S.java_version,
        "platform": _platform(),
        "worker": ("running" if current else "idle") if S.worker.is_alive() else "dead",
        "current_job": current and {"id": current["id"], "kind": current["kind"], "progress": current["progress"],
                                    "decks": [d["deck_name"] for d in current["decks"]]},
        "queue": S.store.queue_length(),
        "card_index": {**S.index.stats(), "load_seconds": S.index_seconds} if S.index else None,
        "limits": {"max_games": config.MAX_GAMES, "max_queued_jobs": config.MAX_QUEUED_JOBS,
                   "max_runtime_seconds": config.MAX_RUNTIME_SECONDS, "game_clock_seconds": config.GAME_CLOCK_SECONDS},
        "last_successful_simulation": S.store.last_by_status("COMPLETED"),
        "last_failed_simulation": S.store.last_by_status("FAILED"),
        "memory": _meminfo(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }


@app.get("/api/forge/test-decks")
def test_decks():
    out = []
    for f in sorted(config.TEST_DECKS_DIR.glob("*.dck")):
        p = parse_dck(f.read_text("utf-8"))
        out.append({"name": f.stem, "format": "commander" if p.has_commander else "constructed",
                    "cards": p.count("main") + p.count("commander")})
    return out


@app.post("/api/forge/audit")
def audit(body: AuditIn):
    return _submit("audit", [("a", _resolve(body.deck))], body.format, body.games, body.priority, None, True)


@app.post("/api/simulations")
def create_simulation(body: SimulationIn):
    decks = [("a", _resolve(body.deck_a)), ("b", _resolve(body.deck_b))]
    return _submit("simulation", decks, body.format, body.games, body.priority, body.seed, body.allow_invalid)


@app.get("/api/simulations")
def list_simulations(status: Optional[str] = None, deck: Optional[str] = None,
                     kind: Optional[Literal["simulation", "audit"]] = None, limit: int = 50):
    return S.store.list_jobs(status, deck, kind, max(1, min(limit, 500)))


@app.get("/api/simulations/{job_id}")
def get_simulation(job_id: str):
    return _get(job_id)


@app.get("/api/simulations/{job_id}/results")
def simulation_results(job_id: str):
    job = _get(job_id)
    return {**job, "games": S.store.games(job_id)}


@app.get("/api/simulations/{job_id}/log", response_class=PlainTextResponse)
def simulation_log(job_id: str):
    job = _get(job_id)
    path = Path(job["log_path"]) if job.get("log_path") else None
    if not path or not path.is_file():
        raise HTTPException(404, "No log yet")
    data = path.read_bytes()
    limit = 5 * 1024 * 1024
    if len(data) > limit:
        data = b"# ... truncated, showing the last 5 MB ...\n" + data[-limit:]
    return PlainTextResponse(data.decode("utf-8", "replace"))


@app.post("/api/simulations/{job_id}/cancel")
def cancel_simulation(job_id: str):
    job = _get(job_id)
    if job["status"] == "QUEUED" and S.store.cancel_queued(job_id):
        return {"job_id": job_id, "status": "cancelled"}
    if job["status"] in ("QUEUED", "RUNNING"):
        S.worker.request_cancel(job_id)
        return {"job_id": job_id, "status": "cancelling"}
    raise HTTPException(409, f"Job is already {job['status']}")


@app.get("/api/decks/{name}/audit")
def deck_audit(name: str):
    jobs = S.store.list_jobs("COMPLETED", name, "audit", 1) or S.store.list_jobs(None, name, "audit", 1)
    if not jobs:
        raise HTTPException(404, "No audit for this deck")
    return _get(jobs[0]["id"])


@app.get("/api/decks/{name}/history")
def deck_history(name: str, limit: int = 50):
    return S.store.list_jobs(None, name, None, max(1, min(limit, 500)))


@app.get("/api/matchups")
def matchups():
    return S.store.matchups()
