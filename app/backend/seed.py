"""First-run seed loader.

On startup, if the collection is empty, import the committed workbook CSV so the
Raspberry Pi database is populated without a manual catalogue exercise.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from importer import import_file
from models import Card, Deck, DeckCard

SEED_CSV = Path(__file__).resolve().parent / "seed" / "collection_seed.csv"
MORDOR_DECKLIST = Path(__file__).resolve().parent / "seed" / "armies_of_mordor.txt"
SAURON_DECKLIST = Path(__file__).resolve().parent / "seed" / "sauron_dark_lord.txt"
FELLOWSHIP_DECKLIST = Path(__file__).resolve().parent / "seed" / "fellowship_free_peoples.txt"
BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest", "wastes"}


def seed_if_empty(db: Session) -> None:
    if db.query(Card).count() > 0:
        return
    if not SEED_CSV.exists():
        return
    result = import_file(db, SEED_CSV.name, SEED_CSV.read_bytes())
    print(f"[seed] imported workbook: {result.summary()}")


def ensure_default_decks(db: Session) -> None:
    """Guarantee the built-in Aragorn Commander deck exists and adopt legacy slots.

    Idempotent: creates the Aragorn deck once, then assigns any pre-multi-deck
    ``deck_cards`` rows (deck_id / board still NULL after the ALTER) to it.
    """
    deck = db.query(Deck).filter(Deck.slug == "aragorn").first()
    if deck is None:
        deck = Deck(
            slug="aragorn",
            name="Aragorn, the Uniter",
            format="commander",
            commander_name="Aragorn, the Uniter",
            allowed_colours="W,U,R,G",
            deck_size=100,
            max_copies=1,
            notes="Original four-colour Commander deck.",
        )
        db.add(deck)
        db.commit()
        db.refresh(deck)

    orphans = (
        db.query(DeckCard)
        .filter((DeckCard.deck_id.is_(None)) | (DeckCard.deck_id == 0))
        .all()
    )
    changed = False
    for slot in orphans:
        slot.deck_id = deck.id
        if not slot.board:
            slot.board = "main"
        changed = True
    if changed:
        db.commit()

    _seed_mordor_standard(db)
    _seed_sauron_commander(db)
    _seed_fellowship_commander(db)


def _seed_mordor_standard(db: Session) -> None:
    _seed_builtin_deck(
        db,
        slug="armies-of-mordor",
        name="Armies of Mordor",
        fmt="standard",
        colours="B",
        deck_size=60,
        max_copies=4,
        notes="Mono-black Amass midrange. House-rules Standard (LOTR-only).",
        commander_name="",
        path=MORDOR_DECKLIST,
    )


def _seed_sauron_commander(db: Session) -> None:
    _seed_builtin_deck(
        db,
        slug="sauron-dark-lord",
        name="Sauron, the Dark Lord",
        fmt="commander",
        colours="U,B,R",
        deck_size=100,
        max_copies=1,
        notes="Grixis Amass/army control. Middle-earth only (LTR + LTR Commander).",
        commander_name="Sauron, the Dark Lord",
        path=SAURON_DECKLIST,
    )


def _seed_fellowship_commander(db: Session) -> None:
    _seed_builtin_deck(
        db,
        slug="free-peoples",
        name="The Fellowship of the Free Peoples",
        fmt="commander",
        colours="G,W",
        deck_size=100,
        max_copies=1,
        notes="Selesnya Food/lifegain go-wide with a legendary hero army. Middle-earth only.",
        commander_name="Samwise Gamgee",
        path=FELLOWSHIP_DECKLIST,
    )


def _seed_builtin_deck(
    db: Session,
    *,
    slug: str,
    name: str,
    fmt: str,
    colours: str,
    deck_size: int,
    max_copies: int,
    notes: str,
    commander_name: str,
    path: Path,
) -> None:
    """Create and populate a built-in deck from a committed decklist file (once)."""
    if db.query(Deck).filter(Deck.slug == slug).first() is not None:
        return
    if not path.exists():
        return

    deck = Deck(
        slug=slug,
        name=name,
        format=fmt,
        commander_name=commander_name,
        allowed_colours=colours,
        deck_size=deck_size,
        max_copies=max_copies,
        notes=notes,
    )
    db.add(deck)
    db.commit()
    db.refresh(deck)

    commander_norm = commander_name.strip().lower()
    by_name: dict[str, Card] = {}
    for c in db.query(Card).all():
        by_name.setdefault(c.card_name.strip().lower(), c)

    board = "main"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            board = "side" if "side" in line.lower() else "main"
            continue
        if line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        qty = int(parts[0])
        card_name = parts[1].strip()
        norm = card_name.lower()
        card = by_name.get(norm)
        if card is None:
            card = Card(
                set_name="Unknown",
                card_name=card_name,
                card_type="Basic Land" if norm in BASIC_LANDS else "",
                quantity=0,
            )
            db.add(card)
            db.flush()
            by_name[norm] = card
        is_cmd = fmt == "commander" and board == "main" and norm == commander_norm
        db.add(
            DeckCard(
                deck_id=deck.id,
                card_id=card.id,
                quantity=qty,
                board=board,
                is_commander=is_cmd,
                role="Commander" if is_cmd else "",
                status="Owned" if card.quantity >= qty else "Need",
            )
        )
    db.commit()
