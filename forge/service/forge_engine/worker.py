"""Single-worker job queue that drives the Forge CLI."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config
from .cards import parse_dck
from .logparse import ForgeOutput
from .store import Store, now


def detect_java_version() -> str:
    try:
        r = subprocess.run([config.JAVA_BIN, *config.JAVA_OPTS, "-version"], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r'version "([^"]+)"', r.stderr + r.stdout)
    return m.group(1) if m else ""


def detect_forge_version() -> str:
    if config.FORGE_VERSION:
        return config.FORGE_VERSION
    jar = config.forge_jar()
    if jar:
        m = re.search(r"forge-gui-desktop-(.+?)-jar-with-dependencies", jar.resolve().name)
        if m:
            return m.group(1)
    return ""


def build_command(workdir: Path, deck_a: str, deck_b: str, fmt: str, games: int, clock: int,
                  seed: int | None) -> list[str]:
    jar = config.forge_jar()
    if jar is None:
        raise FileNotFoundError(f"Forge jar not found under {config.FORGE_HOME}")
    cmd = [config.JAVA_BIN, *config.JAVA_OPTS, "-jar", str(jar), "sim",
           "-D", str(workdir), "-d", deck_a, deck_b, "-n", str(games), "-c", str(clock), "-q"]
    if fmt == "commander":
        cmd += ["-f", "Commander"]
    if seed is not None:
        cmd += ["-s", str(seed)]
    return cmd


def _kill(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass


def run_forge(cmd: list[str], log_path: Path, header: list[str],
              on_game: Callable[[dict], None] | None = None,
              should_stop: Callable[[], str | None] | None = None) -> tuple[int, ForgeOutput, str | None]:
    """Run Forge, tee every output line to ``log_path`` and parse it as it streams."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = ForgeOutput()
    stop_reason: list[str] = []
    env = {**os.environ, "SENTRY_DSN": ""}
    proc = subprocess.Popen(
        cmd, cwd=str(config.FORGE_HOME), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1, env=env,
        start_new_session=hasattr(os, "killpg"),
    )

    def watchdog() -> None:
        while proc.poll() is None:
            reason = should_stop() if should_stop else None
            if reason:
                stop_reason.append(reason)
                _kill(proc)
                return
            time.sleep(1)

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        with log_path.open("a", encoding="utf-8") as log:
            for h in header:
                log.write(f"# {h}\n")
            log.write("# " + " ".join(cmd) + "\n")
            log.flush()
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                log.flush()
                game = out.feed(line)
                if game and on_game:
                    on_game(game)
            rc = proc.wait()
            if stop_reason:
                log.write(f"# job stopped: {stop_reason[0]}\n")
            log.write(f"# forge exit code: {rc}\n")
    finally:
        # Never leave a 2 GB JVM behind if logging/parsing/DB writes fail mid-run.
        if proc.poll() is None:
            _kill(proc)
    return rc, out, (stop_reason[0] if stop_reason else None)


def _card_sides(workdir: Path, sides_to_read: tuple[str, ...]) -> dict[str, set[str]]:
    sides: dict[str, set[str]] = {}
    for side in sides_to_read:
        f = workdir / f"deck_{side}.dck"
        if f.is_file():
            sides[side] = {ln.name for ln in parse_dck(f.read_text("utf-8")).lines}
    return sides


def _side_of(card: str, sides: dict[str, set[str]]) -> str | None:
    hits = [s for s, names in sides.items() if card in names]
    return hits[0] if len(hits) == 1 else None


class Worker(threading.Thread):
    def __init__(self, store: Store, java_version: str, forge_version: str) -> None:
        super().__init__(name="forge-worker", daemon=True)
        self.store = store
        self.java_version = java_version
        self.forge_version = forge_version
        self.current_job: str | None = None
        self.last_heartbeat = time.monotonic()
        self._cancel: set[str] = set()
        self._wake = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def request_cancel(self, job_id: str) -> None:
        self._cancel.add(job_id)

    def run(self) -> None:
        while True:
            self.last_heartbeat = time.monotonic()
            job = self.store.next_queued()
            if not job or not self.store.claim(job["id"]):
                self._wake.wait(2)
                self._wake.clear()
                continue
            self.current_job = job["id"]
            try:
                self._run_job(job)
            except Exception as exc:  # never let one job kill the worker
                self.store.update_job(job["id"], status="FAILED", completed_at=now(), error=f"Worker error: {exc}")
            finally:
                self.current_job = None
                self._cancel.discard(job["id"])

    def _run_job(self, job: dict) -> None:
        job_id, fmt, games = job["id"], job["format"], job["games_requested"]
        workdir = config.DATA_DIR / "jobs" / job_id
        stamp = datetime.now(timezone.utc)
        log_path = config.DATA_DIR / "logs" / f"{stamp:%Y}" / f"{stamp:%m}" / f"{job_id}.log"
        self.store.update_job(job_id, started_at=now(), log_path=str(log_path),
                              forge_version=self.forge_version, java_version=self.java_version)
        budget = min(config.MAX_RUNTIME_SECONDS,
                     config.STARTUP_GRACE_SECONDS + games * (job["clock_seconds"] + 30))
        deadline = time.monotonic() + budget

        def should_stop() -> str | None:
            self.last_heartbeat = time.monotonic()
            if job_id in self._cancel:
                return "CANCELLED"
            if time.monotonic() > deadline:
                return "TIMEOUT"
            return None

        try:
            cmd = build_command(workdir, "deck_a.dck", "deck_b.dck", fmt, games, job["clock_seconds"], job["seed"])
            header = [f"job {job_id} ({job['kind']}, {fmt}, {games} games)",
                      f"forge {self.forge_version} / java {self.java_version}",
                      f"started {now()} / budget {budget}s"]
            rc, out, reason = run_forge(cmd, log_path, header,
                                        on_game=lambda g: self.store.add_game(job_id, g),
                                        should_stop=should_stop)
        except OSError as exc:
            self.store.update_job(job_id, status="FAILED", completed_at=now(), error=f"Forge run failed: {exc}")
            return

        done = len(out.games)
        if reason:
            status, error = reason, ("Cancelled by user" if reason == "CANCELLED" else f"Exceeded {budget}s")
        elif out.load_failures:
            status, error = "FAILED", "Forge could not load deck(s): " + ", ".join(out.load_failures)
        elif done >= games:
            status, error = "COMPLETED", None
        else:
            first = out.errors[0] if out.errors else "no game result in output"
            status, error = "FAILED", f"Forge exited with code {rc} after {done}/{games} games: {first}"

        sides = _card_sides(workdir, ("a",) if job["kind"] == "audit" else ("a", "b"))
        log_issues: list[dict] = []
        for card, set_code in sorted(out.unsupported):
            log_issues.append({"side": _side_of(card, sides), "severity": "RED", "issue_type": "unsupported_card_log",
                               "card_name": card, "set_code": set_code,
                               "message": "Forge reported this card as unsupported and dropped it"})
        for card, methods in sorted(out.ai_warnings.items()):
            log_issues.append({"side": _side_of(card, sides), "severity": "YELLOW", "issue_type": "ai_fallback",
                               "card_name": card,
                               "message": "AI uses a default implementation of " + ", ".join(sorted(methods))})
        for api_name in sorted(out.no_ai):
            log_issues.append({"side": None, "severity": "YELLOW", "issue_type": "no_ai",
                               "message": f"No AI assigned for ability API '{api_name}'"})
        for deck in out.load_failures:
            log_issues.append({"side": None, "severity": "ERROR", "issue_type": "forge_load",
                               "message": f"Forge could not load deck {deck}"})
        self.store.add_issues(job_id, log_issues)

        current = self.store.get_job(job_id) or {}
        issues = current.get("issues", [])
        severities = {i["severity"] for i in issues}
        flags = []
        if "ERROR" in severities:
            flags.append("structure_invalid")
        if "RED" in severities:
            flags.append("unsupported_cards")
        if "YELLOW" in severities:
            flags.append("ai_fallback")
        if any(g["draw_reason"] == "clock" for g in out.games):
            flags.append("clock_draws")
        if job["kind"] == "simulation" and done < 30:
            flags.append("small_sample")
        result = {"quality_flags": flags, **out.summary()}
        if job["kind"] == "audit":
            structure_ok = not any(i["severity"] == "ERROR" and i["issue_type"] != "forge_load" for i in issues)
            load_ok = status == "COMPLETED"
            red = sorted({i["card_name"] for i in issues if i["severity"] == "RED"})
            yellow = sorted({i["card_name"] for i in issues if i["severity"] == "YELLOW" and i["card_name"]})
            verdict = "FAIL" if not (structure_ok and load_ok) or red else ("WARN" if yellow or out.no_ai else "PASS")
            result["audit"] = {
                "status": verdict,
                "structure": "PASS" if structure_ok else "FAIL",
                "forge_load": "PASS" if load_ok else "FAIL",
                "red_cards": red,
                "yellow_cards": yellow,
                "note": "AI warnings only cover cards actually drawn/played in the self-test game(s).",
            }
        self.store.update_job(job_id, status=status, completed_at=now(), error=error,
                              result_json=json.dumps(result))
