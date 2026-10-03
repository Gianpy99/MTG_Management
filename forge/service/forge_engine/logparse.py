"""Incremental parser for Forge ``sim`` console output."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RESULT_WIN = re.compile(r"^Game Result: Game (\d+) ended in (\d+) ms\. (.+) has won!\s*$")
RESULT_DRAW = re.compile(r"^Game Result: Game (\d+) ended in a Draw! Took (\d+) ms\.\s*$")
TURN = re.compile(r"^Game Outcome: Turn (\d+)\s*$")
SLOW_DRAW = "Stopping slow match as draw"
UNSUPPORTED = re.compile(r'An unsupported card was requested: "(.+?)" from "(.+?)"')
AI_WARNING = re.compile(
    r"^Warning: default \(ie\. inherited from base class\) implementation of (\w+) is used by (.+?) for ([\w.$]+)\."
)
NO_AI = re.compile(r"^No AI assigned for API: (\S+)")
LOAD_FAIL = re.compile(r"Could not load deck - (.+?), match cannot start")
APP_VERSION = re.compile(r"APP: Forge v\.(\S+)")
AI_PLAYER = re.compile(r"^Ai\((\d+)\)-")
EXCEPTION = re.compile(r"(Exception|Error)\b.*|^\s*at [\w.$]+\(")
MAX_ERRORS = 30


@dataclass
class ForgeOutput:
    games: list[dict] = field(default_factory=list)
    unsupported: set[tuple[str, str]] = field(default_factory=set)
    ai_warnings: dict[str, set[str]] = field(default_factory=dict)  # card -> methods
    no_ai: set[str] = field(default_factory=set)
    load_failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    forge_build: str = ""
    _turns: int | None = None
    _slow: bool = False

    def feed(self, line: str) -> dict | None:
        """Consume one output line; return a game dict when a game finishes."""
        line = line.rstrip("\r\n")
        if m := TURN.match(line):
            self._turns = int(m.group(1))
            return None
        if SLOW_DRAW in line:
            self._slow = True
            return None
        if m := RESULT_WIN.match(line):
            winner = m.group(3).strip()
            pm = AI_PLAYER.match(winner)
            side = {"1": "a", "2": "b"}.get(pm.group(1)) if pm else None
            return self._game(int(m.group(1)), int(m.group(2)), side, winner, "")
        if m := RESULT_DRAW.match(line):
            return self._game(int(m.group(1)), int(m.group(2)), None, "", "clock" if self._slow else "draw")
        if m := UNSUPPORTED.search(line):
            if m.group(2) != "[N.A.]":
                self.unsupported.add((m.group(1), m.group(2)))
            elif not any(c == m.group(1) for c, _ in self.unsupported):
                self.unsupported.add((m.group(1), ""))
            return None
        if m := AI_WARNING.match(line):
            self.ai_warnings.setdefault(m.group(2).strip(), set()).add(m.group(1))
            return None
        if m := NO_AI.match(line):
            self.no_ai.add(m.group(1))
            return None
        if m := LOAD_FAIL.search(line):
            self.load_failures.append(m.group(1))
            return None
        if m := APP_VERSION.search(line):
            self.forge_build = m.group(1)
            return None
        if EXCEPTION.search(line) and "Error handling registered" not in line and len(self.errors) < MAX_ERRORS:
            self.errors.append(line.strip())
        return None

    def _game(self, number: int, ms: int, side: str | None, winner: str, draw_reason: str) -> dict:
        game = {
            "game_number": number,
            "winner_side": side,
            "winner_name": winner,
            "duration_ms": ms,
            "turns": self._turns,
            "draw_reason": draw_reason,
        }
        self._turns, self._slow = None, False
        self.games.append(game)
        return game

    def summary(self) -> dict:
        return {
            "forge_build": self.forge_build,
            "unsupported_cards": [{"card": c, "set": s} for c, s in sorted(self.unsupported)],
            "ai_warnings": [{"card": c, "methods": sorted(m)} for c, m in sorted(self.ai_warnings.items())],
            "no_ai_apis": sorted(self.no_ai),
            "load_failures": self.load_failures,
            "errors": self.errors,
        }
