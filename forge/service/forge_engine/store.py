"""forge_results.db — jobs, decks, games and audit issues (never the collection DB)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

PRIORITIES = {"HIGH": 2, "NORMAL": 1, "LOW": 0}
PRIORITY_NAMES = {v: k for k, v in PRIORITIES.items()}
ACTIVE = ("QUEUED", "RUNNING")

SCHEMA = """
CREATE TABLE IF NOT EXISTS simulation_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- simulation | audit
    status TEXT NOT NULL,               -- QUEUED RUNNING COMPLETED FAILED CANCELLED TIMEOUT
    priority INTEGER NOT NULL DEFAULT 1,
    format TEXT NOT NULL,               -- constructed | commander
    games_requested INTEGER NOT NULL,
    games_completed INTEGER NOT NULL DEFAULT 0,
    deck_a_wins INTEGER NOT NULL DEFAULT 0,
    deck_b_wins INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    clock_seconds INTEGER NOT NULL,
    seed INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    forge_version TEXT,
    java_version TEXT,
    log_path TEXT,
    error TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON simulation_jobs(status, priority, created_at);

CREATE TABLE IF NOT EXISTS simulation_decks (
    simulation_id TEXT NOT NULL,
    side TEXT NOT NULL,                 -- a | b
    deck_name TEXT NOT NULL,
    deck_ref TEXT,                      -- caller reference, e.g. collection deck slug
    deck_source TEXT,                   -- inline | test
    deck_version TEXT,
    deck_hash TEXT NOT NULL,            -- SHA256 of the submitted .dck
    forge_dck_hash TEXT NOT NULL,       -- SHA256 of the normalised .dck given to Forge
    card_count INTEGER,
    PRIMARY KEY (simulation_id, side)
);
CREATE INDEX IF NOT EXISTS ix_decks_ref ON simulation_decks(deck_ref);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id TEXT NOT NULL,
    game_number INTEGER NOT NULL,
    winner_side TEXT,                   -- a | b | NULL (draw)
    winner_name TEXT,
    turns INTEGER,
    duration_ms INTEGER,
    draw_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_games_sim ON games(simulation_id);

CREATE TABLE IF NOT EXISTS audit_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id TEXT NOT NULL,
    side TEXT,
    severity TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    card_name TEXT,
    set_code TEXT,
    collector_number TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS ix_issues_sim ON audit_issues(simulation_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def _exec(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _all(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # -- jobs -------------------------------------------------------------- #
    def create_job(self, job: dict, decks: list[dict], issues: list[dict]) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                cols = ", ".join(job)
                self._conn.execute(
                    f"INSERT INTO simulation_jobs ({cols}) VALUES ({', '.join('?' * len(job))})",
                    list(job.values()),
                )
                for d in decks:
                    d = {"simulation_id": job["id"], **d}
                    self._conn.execute(
                        f"INSERT INTO simulation_decks ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
                        list(d.values()),
                    )
                self._insert_issues(job["id"], issues)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _insert_issues(self, job_id: str, issues: list[dict]) -> None:
        for i in issues:
            self._conn.execute(
                "INSERT INTO audit_issues (simulation_id, side, severity, issue_type, card_name, set_code,"
                " collector_number, message) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, i.get("side"), i["severity"], i["issue_type"], i.get("card_name", ""),
                 i.get("set_code", ""), i.get("collector_number", ""), i.get("message", "")),
            )

    def add_issues(self, job_id: str, issues: list[dict]) -> None:
        with self._lock:
            self._insert_issues(job_id, issues)

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE simulation_jobs SET {sets} WHERE id = ?", [*fields.values(), job_id])

    def add_game(self, job_id: str, game: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO games (simulation_id, game_number, winner_side, winner_name, turns, duration_ms,"
                " draw_reason) VALUES (?,?,?,?,?,?,?)",
                (job_id, game["game_number"], game["winner_side"], game["winner_name"], game["turns"],
                 game["duration_ms"], game["draw_reason"]),
            )
            col = {"a": "deck_a_wins", "b": "deck_b_wins"}.get(game["winner_side"], "draws")
            self._conn.execute(
                f"UPDATE simulation_jobs SET games_completed = games_completed + 1, {col} = {col} + 1 WHERE id = ?",
                (job_id,),
            )

    def claim(self, job_id: str) -> bool:
        cur = self._exec(
            "UPDATE simulation_jobs SET status = 'RUNNING', error = NULL WHERE id = ? AND status = 'QUEUED'", (job_id,)
        )
        return cur.rowcount == 1

    def cancel_queued(self, job_id: str) -> bool:
        cur = self._exec(
            "UPDATE simulation_jobs SET status = 'CANCELLED', completed_at = ?, error = 'Cancelled by user'"
            " WHERE id = ? AND status = 'QUEUED'",
            (now(), job_id),
        )
        return cur.rowcount == 1

    def next_queued(self) -> dict | None:
        rows = self._all(
            "SELECT * FROM simulation_jobs WHERE status = 'QUEUED' ORDER BY priority DESC, created_at, rowid LIMIT 1"
        )
        return rows[0] if rows else None

    def requeue_interrupted(self) -> list[str]:
        """Jobs left RUNNING by a crash/restart are re-run from scratch."""
        ids = [r["id"] for r in self._all("SELECT id FROM simulation_jobs WHERE status = 'RUNNING'")]
        for job_id in ids:
            with self._lock:
                self._conn.execute("DELETE FROM games WHERE simulation_id = ?", (job_id,))
                self._conn.execute(
                    "DELETE FROM audit_issues WHERE simulation_id = ? AND issue_type IN ('ai_fallback','no_ai',"
                    "'unsupported_card_log','forge_load')",
                    (job_id,),
                )
                self._conn.execute(
                    "UPDATE simulation_jobs SET status='QUEUED', games_completed=0, deck_a_wins=0, deck_b_wins=0,"
                    " draws=0, started_at=NULL, error='Re-queued after service restart' WHERE id = ?",
                    (job_id,),
                )
        return ids

    def queue_length(self) -> int:
        return self._all("SELECT COUNT(*) AS n FROM simulation_jobs WHERE status = 'QUEUED'")[0]["n"]

    def get_job(self, job_id: str) -> dict | None:
        rows = self._all("SELECT * FROM simulation_jobs WHERE id = ?", (job_id,))
        if not rows:
            return None
        job = self._present(rows[0])
        job["decks"] = self._all(
            "SELECT side, deck_name, deck_ref, deck_source, deck_version, deck_hash, forge_dck_hash, card_count"
            " FROM simulation_decks WHERE simulation_id = ? ORDER BY side",
            (job_id,),
        )
        job["issues"] = self._all(
            "SELECT side, severity, issue_type, card_name, set_code, collector_number, message FROM audit_issues"
            " WHERE simulation_id = ? ORDER BY CASE severity WHEN 'ERROR' THEN 0 WHEN 'RED' THEN 1"
            " WHEN 'YELLOW' THEN 2 ELSE 3 END, side, card_name",
            (job_id,),
        )
        return job

    def list_jobs(self, status: str | None, deck_ref: str | None, kind: str | None, limit: int) -> list[dict]:
        sql = "SELECT j.* FROM simulation_jobs j"
        where, params = [], []
        if deck_ref:
            sql += " JOIN simulation_decks d ON d.simulation_id = j.id"
            where.append("(d.deck_ref = ? OR d.deck_name = ?)")
            params += [deck_ref, deck_ref]
        if status:
            where.append("j.status = ?")
            params.append(status.upper())
        if kind:
            where.append("j.kind = ?")
            params.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY j.id ORDER BY j.created_at DESC, j.rowid DESC LIMIT ?"
        params.append(limit)
        jobs = [self._present(r) for r in self._all(sql, params)]
        if jobs:
            ids = [j["id"] for j in jobs]
            decks = self._all(
                f"SELECT simulation_id, side, deck_name, deck_ref FROM simulation_decks WHERE simulation_id IN"
                f" ({', '.join('?' * len(ids))})",
                ids,
            )
            by_id: dict[str, list] = {}
            for d in decks:
                by_id.setdefault(d.pop("simulation_id"), []).append(d)
            for j in jobs:
                j["decks"] = sorted(by_id.get(j["id"], []), key=lambda d: d["side"])
        return jobs

    def games(self, job_id: str) -> list[dict]:
        return self._all(
            "SELECT game_number, winner_side, winner_name, turns, duration_ms, draw_reason FROM games"
            " WHERE simulation_id = ? ORDER BY game_number",
            (job_id,),
        )

    def matchups(self) -> list[dict]:
        rows = self._all(
            "SELECT a.deck_name AS deck_a, b.deck_name AS deck_b, SUM(j.games_completed) AS games,"
            " SUM(j.deck_a_wins) AS a_wins, SUM(j.deck_b_wins) AS b_wins, SUM(j.draws) AS draws"
            " FROM simulation_jobs j"
            " JOIN simulation_decks a ON a.simulation_id = j.id AND a.side = 'a'"
            " JOIN simulation_decks b ON b.simulation_id = j.id AND b.side = 'b'"
            " WHERE j.kind = 'simulation' AND j.status = 'COMPLETED' AND a.deck_name <> b.deck_name"
            " GROUP BY a.deck_name, b.deck_name"
        )
        pairs: dict[tuple[str, str], dict] = {}
        for r in rows:
            x, y = sorted((r["deck_a"], r["deck_b"]))
            p = pairs.setdefault((x, y), {"deck_x": x, "deck_y": y, "games": 0, "x_wins": 0, "y_wins": 0, "draws": 0})
            p["games"] += r["games"]
            p["draws"] += r["draws"]
            if r["deck_a"] == x:
                p["x_wins"] += r["a_wins"]
                p["y_wins"] += r["b_wins"]
            else:
                p["x_wins"] += r["b_wins"]
                p["y_wins"] += r["a_wins"]
        out = []
        for p in pairs.values():
            p["x_win_rate"] = round(p["x_wins"] / p["games"], 4) if p["games"] else None
            out.append(p)
        return sorted(out, key=lambda p: (p["deck_x"], p["deck_y"]))

    def last_by_status(self, status: str) -> dict | None:
        rows = self._all(
            "SELECT id, completed_at FROM simulation_jobs WHERE status = ? ORDER BY completed_at DESC LIMIT 1",
            (status,),
        )
        return rows[0] if rows else None

    @staticmethod
    def _present(row: dict) -> dict:
        job = dict(row)
        job["priority"] = PRIORITY_NAMES.get(job["priority"], "NORMAL")
        job["result"] = json.loads(job.pop("result_json") or "null")
        done = job["games_completed"]
        job["progress"] = {"games_completed": done, "games_requested": job["games_requested"]}
        job["deck_a_win_rate"] = round(job["deck_a_wins"] / done, 4) if done else None
        return job
