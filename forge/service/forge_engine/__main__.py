"""Command line checks used by the Jenkins pipeline.

    python -m forge_engine selftest   # real Forge game, no API (pre-deploy integration test)
    python -m forge_engine smoke      # submit a 1-game job to the running API and wait for it
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .cards import CardIndex, normalise, parse_dck
from .worker import build_command, detect_forge_version, detect_java_version, run_forge

SMOKE_DECKS = ("Abzan_Food", "Rakdos_Orcs")


def selftest() -> int:
    ok = True
    java, forge = detect_java_version(), detect_forge_version()
    print(f"java={java or 'MISSING'} forge={forge or 'MISSING'} jar={config.forge_jar()}")
    m = re.match(r"\d+", java)
    if not m or int(m.group()) < 17:
        print("FAIL: Java >= 17 required")
        return 1
    t0 = time.monotonic()
    index = CardIndex.load(config.FORGE_HOME / "res")
    print(f"card index: {index.stats()} in {time.monotonic() - t0:.1f}s")
    if index.stats()["cards"] < 10000:
        print("FAIL: Forge card database looks incomplete")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for deck_file in sorted(config.TEST_DECKS_DIR.glob("*.dck")):
            text, issues = normalise(parse_dck(deck_file.read_text("utf-8")), index)
            red = [i.card_name for i in issues if i.severity == "RED"]
            print(f"  {deck_file.stem}: {len(red)} unsupported {red if red else ''}")
            if deck_file.stem in SMOKE_DECKS:
                if red:
                    ok = False
                side = "a" if deck_file.stem == SMOKE_DECKS[0] else "b"
                (work / f"deck_{side}.dck").write_text(text, encoding="utf-8")
        if not ((work / "deck_a.dck").is_file() and (work / "deck_b.dck").is_file()):
            print(f"FAIL: smoke decks {SMOKE_DECKS} not found in {config.TEST_DECKS_DIR}")
            return 1
        cmd = build_command(work, "deck_a.dck", "deck_b.dck", "constructed", 1, 120, 42)
        t0 = time.monotonic()
        rc, out, _ = run_forge(cmd, work / "selftest.log", ["selftest"])
        elapsed = time.monotonic() - t0
        if rc != 0 or len(out.games) != 1 or out.load_failures or out.unsupported:
            print((work / "selftest.log").read_text("utf-8")[-4000:])
            print(f"FAIL: rc={rc} games={len(out.games)} load_failures={out.load_failures} "
                  f"unsupported={sorted(out.unsupported)}")
            return 1
        g = out.games[0]
        print(f"game: winner={g['winner_name'] or 'draw'} turns={g['turns']} {g['duration_ms']}ms "
              f"(total {elapsed:.0f}s, build {out.forge_build})")
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


def _http(method: str, url: str, body: dict | None = None, timeout: int = 15) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def smoke(base: str = "http://127.0.0.1:8787", timeout: int = 900) -> int:
    deadline = time.monotonic() + timeout
    while True:
        try:
            code, health = _http("GET", f"{base}/health")
            if code == 200:
                break
        except OSError as exc:
            code, health = 0, {"error": str(exc)}
        if time.monotonic() > deadline:
            print(f"FAIL: health {code} {health}")
            return 1
        time.sleep(3)
    print(f"health: {health}")
    # An audit (1-game self-test) is used so smoke runs never pollute matchup statistics.
    code, job = _http("POST", f"{base}/api/forge/audit", {"deck": SMOKE_DECKS[0], "priority": "HIGH"})
    if code != 200:
        print(f"FAIL: submit {code} {job}")
        return 1
    job_id = job["job_id"]
    print(f"submitted {job_id}")
    while time.monotonic() < deadline:
        _, j = _http("GET", f"{base}/api/simulations/{job_id}")
        if j["status"] in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
            audit = (j.get("result") or {}).get("audit", {})
            print(f"{job_id}: {j['status']} games={j['games_completed']} audit={audit.get('status')}"
                  f" error={j.get('error')}")
            return 0 if j["status"] == "COMPLETED" and j["games_completed"] == 1 else 1
        time.sleep(5)
    print(f"FAIL: {job_id} did not finish within {timeout}s")
    return 1


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "selftest":
        return selftest()
    if len(argv) >= 2 and argv[1] == "smoke":
        return smoke(*(argv[2:3] or []))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
