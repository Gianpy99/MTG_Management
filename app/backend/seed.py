"""First-run seed loader.

On startup, if the collection is empty, import the committed workbook CSV so the
Raspberry Pi database is populated without a manual catalogue exercise.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from importer import import_file
from models import Card

SEED_CSV = Path(__file__).resolve().parent / "seed" / "collection_seed.csv"


def seed_if_empty(db: Session) -> None:
    if db.query(Card).count() > 0:
        return
    if not SEED_CSV.exists():
        return
    result = import_file(db, SEED_CSV.name, SEED_CSV.read_bytes())
    print(f"[seed] imported workbook: {result.summary()}")
