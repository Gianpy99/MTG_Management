"""Database engine and session management (SQLite)."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

# Data directory is a Docker volume in production so the SQLite file persists.
DATA_DIR = Path(os.environ.get("MTG_DATA_DIR", Path(__file__).resolve().parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "mtg.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Lightweight in-place schema migration for the multi-deck upgrade.

    The production SQLite file predates the ``deck_id``/``board`` columns on
    ``deck_cards``. ``create_all`` never alters existing tables, so add the
    missing columns here. Back-filling legacy rows into the default deck is done
    with the ORM in ``ensure_default_decks`` (so column defaults apply).
    """
    insp = inspect(engine)
    if "deck_cards" not in insp.get_table_names():
        return  # brand-new DB: create_all builds the current schema

    cols = {c["name"] for c in insp.get_columns("deck_cards")}
    with engine.begin() as conn:
        if "deck_id" not in cols:
            conn.execute(text("ALTER TABLE deck_cards ADD COLUMN deck_id INTEGER"))
        if "board" not in cols:
            conn.execute(text("ALTER TABLE deck_cards ADD COLUMN board VARCHAR DEFAULT 'main'"))

    if "cards" in insp.get_table_names():
        card_cols = {c["name"] for c in insp.get_columns("cards")}
        with engine.begin() as conn:
            if "edition" not in card_cols:
                conn.execute(text("ALTER TABLE cards ADD COLUMN edition VARCHAR DEFAULT ''"))
