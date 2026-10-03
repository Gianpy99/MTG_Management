"""Runtime configuration (environment variables, no hard-coded host paths)."""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


FORGE_HOME = Path(os.environ.get("FORGE_HOME", "/opt/forge"))
FORGE_VERSION = os.environ.get("FORGE_VERSION", "")
JAVA_BIN = os.environ.get("JAVA_BIN", "java")
JAVA_OPTS = os.environ.get(
    "FORGE_JAVA_OPTS",
    "-Xmx2g -Dfile.encoding=UTF-8 -Dio.netty.tryReflectionSetAccessible=true",
).split()

DATA_DIR = Path(os.environ.get("FORGE_DATA_DIR", "/data"))
TEST_DECKS_DIR = Path(os.environ.get("FORGE_TEST_DECKS_DIR", "/srv/test_decks"))

MAX_GAMES = _int("FORGE_MAX_GAMES", 1000)
MAX_QUEUED_JOBS = _int("FORGE_MAX_QUEUED_JOBS", 50)
MAX_RUNTIME_SECONDS = _int("FORGE_MAX_RUNTIME_SECONDS", 12 * 3600)
# Forge's own per-game clock (-c): a game still running after this is called a draw.
GAME_CLOCK_SECONDS = {
    "constructed": _int("FORGE_GAME_CLOCK_CONSTRUCTED", 180),
    "commander": _int("FORGE_GAME_CLOCK_COMMANDER", 300),
}
# JVM start-up + card database load, added on top of the per-game budget.
STARTUP_GRACE_SECONDS = _int("FORGE_STARTUP_GRACE_SECONDS", 300)
MAX_DCK_BYTES = 64 * 1024


def forge_jar() -> Path | None:
    explicit = os.environ.get("FORGE_JAR")
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    link = FORGE_HOME / "forge.jar"
    if link.is_file():
        return link
    jars = sorted(FORGE_HOME.glob("forge-gui-desktop-*-jar-with-dependencies.jar"))
    return jars[-1] if jars else None


def db_path() -> Path:
    return DATA_DIR / "forge_results.db"
