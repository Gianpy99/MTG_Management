"""MTG Management — FastAPI backend.

Single-port service (API + static UI) for the Raspberry Pi. Serves the
Middle-earth MTG collection, set completion, wishlist and the Aragorn
Commander deck builder.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database import Base, DB_PATH, engine, get_db
from importer import import_file
from models import Card, DeckCard, ImportLog, WishlistItem
from schemas import (
    CardOut,
    DeckCardIn,
    DeckCardOut,
    DeckImportIn,
    QuantityUpdate,
    WishlistIn,
    WishlistOut,
)
from seed import seed_if_empty

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Aragorn, the Uniter — {R}{G}{W}{U}: four-colour identity (everything but black).
COMMANDER_NAME = "Aragorn, the Uniter"
ALLOWED_COLOUR_IDENTITY = {"W", "U", "R", "G"}
DECK_SIZE = 100

app = FastAPI(title="Middle-earth MTG Management", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed_if_empty(db)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Sets & completion
# --------------------------------------------------------------------------- #
@app.get("/api/sets")
def list_sets(db: Session = Depends(get_db)) -> list[dict]:
    owned_expr = func.sum(case((Card.quantity > 0, 1), else_=0))
    rows = (
        db.query(Card.set_name, func.count(Card.id), owned_expr)
        .group_by(Card.set_name)
        .all()
    )
    out = []
    for set_name, total, owned in rows:
        owned = int(owned or 0)
        out.append(
            {
                "name": set_name,
                "canonical_total": total,
                "unique_owned": owned,
                "completion": round(owned / total * 100, 1) if total else 0.0,
            }
        )
    return sorted(out, key=lambda s: s["name"])


@app.get("/api/sets/{set_name}/completion")
def set_completion(set_name: str, db: Session = Depends(get_db)) -> dict:
    total = db.query(func.count(Card.id)).filter(Card.set_name == set_name).scalar() or 0
    owned = (
        db.query(func.count(Card.id))
        .filter(Card.set_name == set_name, Card.quantity > 0)
        .scalar()
        or 0
    )
    if total == 0:
        raise HTTPException(status_code=404, detail="Unknown set")
    return {
        "name": set_name,
        "canonical_total": total,
        "unique_owned": owned,
        "missing": total - owned,
        "completion": round(owned / total * 100, 1),
    }


# --------------------------------------------------------------------------- #
# Cards / collection
# --------------------------------------------------------------------------- #
@app.get("/api/cards", response_model=list[CardOut])
def list_cards(
    set: str | None = None,
    owned: bool | None = None,
    q: str | None = None,
    rarity: str | None = None,
    db: Session = Depends(get_db),
) -> list[Card]:
    query = db.query(Card)
    if set:
        query = query.filter(Card.set_name == set)
    if owned is True:
        query = query.filter(Card.quantity > 0)
    elif owned is False:
        query = query.filter(Card.quantity == 0)
    if rarity:
        query = query.filter(Card.rarity == rarity)
    if q:
        like = f"%{q}%"
        query = query.filter(Card.card_name.ilike(like) | Card.oracle_text.ilike(like))
    return query.order_by(Card.set_name, Card.card_name).all()


@app.patch("/api/collection/{card_id}", response_model=CardOut)
def update_quantity(card_id: int, payload: QuantityUpdate, db: Session = Depends(get_db)) -> Card:
    card = db.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    card.quantity = max(0, payload.quantity)
    db.commit()
    db.refresh(card)
    return card


@app.get("/api/collection/summary")
def collection_summary(db: Session = Depends(get_db)) -> dict:
    total = db.query(func.count(Card.id)).scalar() or 0
    owned = db.query(func.count(Card.id)).filter(Card.quantity > 0).scalar() or 0
    copies = db.query(func.coalesce(func.sum(Card.quantity), 0)).scalar() or 0

    missing_by_rarity = dict(
        db.query(Card.rarity, func.count(Card.id))
        .filter(Card.quantity == 0)
        .group_by(Card.rarity)
        .all()
    )
    missing_by_colour = dict(
        db.query(Card.colour, func.count(Card.id))
        .filter(Card.quantity == 0)
        .group_by(Card.colour)
        .all()
    )
    missing_key_aragorn = (
        db.query(func.count(Card.id))
        .filter(
            Card.quantity == 0,
            Card.rarity.in_(["R", "M", "Rare", "Mythic"]),
            Card.aragorn_synergy >= 3,
        )
        .scalar()
        or 0
    )

    deck_slots = db.query(func.coalesce(func.sum(DeckCard.quantity), 0)).scalar() or 0
    deck_need = (
        db.query(func.coalesce(func.sum(DeckCard.quantity), 0))
        .filter(DeckCard.status == "Need")
        .scalar()
        or 0
    )
    wishlist_value = (
        db.query(func.coalesce(func.sum(WishlistItem.target_price), 0.0))
        .filter(WishlistItem.status == "open")
        .scalar()
        or 0.0
    )
    last_import = db.query(ImportLog).order_by(ImportLog.created_at.desc()).first()

    return {
        "unique_total": total,
        "unique_owned": owned,
        "unique_missing": total - owned,
        "total_copies": int(copies),
        "completion": round(owned / total * 100, 1) if total else 0.0,
        "missing_by_rarity": missing_by_rarity,
        "missing_by_colour": missing_by_colour,
        "missing_key_aragorn": missing_key_aragorn,
        "wishlist_value": round(float(wishlist_value), 2),
        "deck_slots_filled": int(deck_slots),
        "deck_size_target": DECK_SIZE,
        "deck_to_buy": int(deck_need),
        "last_import": last_import.summary if last_import else None,
    }


# --------------------------------------------------------------------------- #
# Wishlist
# --------------------------------------------------------------------------- #
@app.get("/api/wishlist", response_model=list[WishlistOut])
def list_wishlist(db: Session = Depends(get_db)) -> list[WishlistItem]:
    return db.query(WishlistItem).order_by(WishlistItem.priority).all()


@app.post("/api/wishlist", response_model=WishlistOut)
def add_wishlist(payload: WishlistIn, db: Session = Depends(get_db)) -> WishlistItem:
    if db.get(Card, payload.card_id) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    item = WishlistItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.patch("/api/wishlist/{item_id}", response_model=WishlistOut)
def update_wishlist(item_id: int, payload: WishlistIn, db: Session = Depends(get_db)) -> WishlistItem:
    item = db.get(WishlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/api/wishlist/{item_id}")
def delete_wishlist(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(WishlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}


# --------------------------------------------------------------------------- #
# Aragorn Commander deck
# --------------------------------------------------------------------------- #
@app.get("/api/decks/aragorn", response_model=list[DeckCardOut])
def list_deck(db: Session = Depends(get_db)) -> list[DeckCard]:
    return db.query(DeckCard).join(Card).order_by(DeckCard.is_commander.desc(), Card.card_name).all()


@app.post("/api/decks/aragorn/cards", response_model=DeckCardOut)
def add_deck_card(payload: DeckCardIn, db: Session = Depends(get_db)) -> DeckCard:
    card = db.get(Card, payload.card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    existing = db.query(DeckCard).filter(DeckCard.card_id == payload.card_id).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Card already in deck (singleton)")
    slot = DeckCard(**payload.model_dump())
    # Auto owned/need status from current collection.
    if not payload.status or payload.status == "Owned":
        slot.status = "Owned" if card.quantity >= payload.quantity else "Need"
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


@app.patch("/api/decks/aragorn/cards/{slot_id}", response_model=DeckCardOut)
def update_deck_card(slot_id: int, payload: DeckCardIn, db: Session = Depends(get_db)) -> DeckCard:
    slot = db.get(DeckCard, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Deck slot not found")
    for key, value in payload.model_dump().items():
        setattr(slot, key, value)
    db.commit()
    db.refresh(slot)
    return slot


@app.delete("/api/decks/aragorn/cards/{slot_id}")
def delete_deck_card(slot_id: int, db: Session = Depends(get_db)) -> dict:
    slot = db.get(DeckCard, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Deck slot not found")
    db.delete(slot)
    db.commit()
    return {"deleted": slot_id}


_DECK_LINE = re.compile(r"^\s*(?:(\d+)\s*[xX]?\s+)?(.+?)\s*$")
_SKIP_PREFIXES = ("//", "#", "deck", "commander:", "sideboard", "sb:", "maybeboard", "about")
BASIC_LANDS = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
}


def _parse_decklist(text: str) -> list[tuple[int, str]]:
    """Parse lines like '1 Card Name', '1x Card Name', 'Card Name'.

    Ignores empty lines, comments and section headers. Strips a trailing set
    hint in parentheses, e.g. 'Aragorn, the Uniter (LTR) 192'.
    """
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        m = _DECK_LINE.match(line)
        if not m:
            continue
        # Skip pure section headers ("Deck", "Commander:", "Sideboard").
        if m.group(1) is None and (low in ("deck", "sideboard", "maybeboard") or low.endswith(":")):
            continue
        qty = int(m.group(1)) if m.group(1) else 1
        name = m.group(2).strip()
        # Drop trailing set/collector hints: "(LTR) 192" or "[LTR]".
        name = re.sub(r"\s*[\(\[][A-Za-z0-9]{2,5}[\)\]]\s*\d*\s*$", "", name).strip()
        if name:
            out.append((qty, name))
    return out


@app.post("/api/decks/aragorn/import")
def import_deck(payload: DeckImportIn, db: Session = Depends(get_db)) -> dict:
    parsed = _parse_decklist(payload.text)
    if not parsed:
        raise HTTPException(status_code=400, detail="No card lines found in decklist")

    if payload.replace:
        db.query(DeckCard).delete()
        db.flush()

    # Preload the catalogue indexed by a Python-normalised name. SQLite's lower()
    # is ASCII-only, so accented Middle-earth names (Éomer, Andúril, ...) must be
    # matched in Python to avoid missed lookups and duplicate inserts.
    existing_by_name: dict[str, Card] = {}
    for c in db.query(Card).all():
        existing_by_name.setdefault(c.card_name.strip().lower(), c)

    # Aggregate requested quantities per unique card (keeps basic-land copies).
    order: list[str] = []
    agg: dict[str, list] = {}  # norm -> [display_name, qty]
    for qty, name in parsed:
        norm = name.strip().lower()
        if norm not in agg:
            agg[norm] = [name, 0]
            order.append(norm)
        agg[norm][1] += qty

    matched = 0
    created = 0
    slots = 0
    for norm in order:
        display, qty = agg[norm]
        card = existing_by_name.get(norm)
        if card is None:
            card = Card(
                set_name="Unknown",
                card_name=display,
                collector_number="",
                card_type="Basic Land" if norm in BASIC_LANDS else "",
                quantity=0,
            )
            db.add(card)
            db.flush()
            existing_by_name[norm] = card
            created += 1
        else:
            matched += 1

        is_cmd = norm == COMMANDER_NAME.lower()
        db.add(
            DeckCard(
                card_id=card.id,
                quantity=qty,
                is_commander=is_cmd,
                role="Commander" if is_cmd else "",
                status="Owned" if card.quantity >= qty else "Need",
            )
        )
        slots += 1

    db.commit()
    return {
        "unique_cards": slots,
        "total_cards": sum(v[1] for v in agg.values()),
        "matched_in_collection": matched,
        "created_as_need": created,
        "replaced": payload.replace,
    }


# --------------------------------------------------------------------------- #
# Scryfall enrichment (fix "Unknown" set on imported cards)
# --------------------------------------------------------------------------- #
_MIDDLE_EARTH_CODES = {"ltr", "ltc", "rex", "pltr", "pltc", "altr"}
_SCRY_HEADERS = {"User-Agent": "MTGManagement/1.0 (personal Raspberry Pi app)", "Accept": "application/json"}


def _scryfall_prints(name: str) -> list[dict]:
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": f'!"{name}"', "unique": "prints", "order": "released", "dir": "asc"}
    )
    req = urllib.request.Request(url, headers=_SCRY_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r).get("data", [])
        if data:
            return data
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    except Exception:
        raise
    # Fuzzy fallback: resolve the card by fuzzy name, then fetch all its prints.
    furl = "https://api.scryfall.com/cards/named?" + urllib.parse.urlencode({"fuzzy": name})
    freq = urllib.request.Request(furl, headers=_SCRY_HEADERS)
    with urllib.request.urlopen(freq, timeout=8) as r:
        card = json.load(r)
    purl = card.get("prints_search_uri")
    if purl:
        preq = urllib.request.Request(purl, headers=_SCRY_HEADERS)
        with urllib.request.urlopen(preq, timeout=8) as r:
            return json.load(r).get("data", [card])
    return [card]


def _is_hobbit(p: dict) -> bool:
    return "hobbit" in (p.get("set_name") or "").lower()


def _is_middle_earth(p: dict) -> bool:
    sn = (p.get("set_name") or "").lower()
    sc = (p.get("set") or "").lower()
    return (
        "lord of the rings" in sn
        or "middle-earth" in sn
        or "middle earth" in sn
        or sc in _MIDDLE_EARTH_CODES
    )


def _classify_printing(prints: list[dict]) -> tuple[str | None, dict | None]:
    """Classify a card into the two project sets.

    Prints are ordered oldest-first, so prints[0] is the ORIGINAL printing —
    that decides the canonical set. If the original is not a Middle-earth set
    (e.g. a generic staple), fall back to any Middle-earth printing the card has.
    """
    if not prints:
        return None, None
    first = prints[0]
    if _is_hobbit(first):
        return "The Hobbit", first
    if _is_middle_earth(first):
        return "The Lord of the Rings", first
    # Original isn't Middle-earth: prefer a LOTR printing, then a Hobbit one.
    lotr = next((p for p in prints if _is_middle_earth(p)), None)
    if lotr is not None:
        return "The Lord of the Rings", lotr
    hobbit = next((p for p in prints if _is_hobbit(p)), None)
    if hobbit is not None:
        return "The Hobbit", hobbit
    return first.get("set_name") or "Unknown", first


_RARITY_MAP = {"common": "C", "uncommon": "U", "rare": "R", "mythic": "M", "special": "S", "bonus": "B"}


def _colour_label(colors: list[str] | None) -> str:
    colors = colors or []
    if len(colors) == 0:
        return "C"
    if len(colors) == 1:
        return colors[0]
    return "Multicolour"


@app.post("/api/cards/enrich")
def enrich_cards(only_unknown: bool = True, limit: int = 400, db: Session = Depends(get_db)) -> dict:
    """Fill real set/metadata from Scryfall for cards (default: only 'Unknown').

    Maps Middle-earth printings to the two project sets so imported deck cards
    stop showing as 'Unknown'. Throttled to respect Scryfall's rate limits.
    """
    query = db.query(Card)
    if only_unknown:
        query = query.filter(Card.set_name == "Unknown")
    cards = query.limit(limit).all()

    # Basic lands are always in scope; normalise their set without hitting
    # Scryfall (they have too many printings to resolve a Middle-earth one).
    for b in db.query(Card).filter(func.lower(Card.card_name).in_(BASIC_LANDS)).all():
        if b.set_name not in PROJECT_SETS:
            b.set_name = "The Lord of the Rings"

    updated = 0
    to_hobbit = 0
    to_lotr = 0
    other_set = 0
    unmatched: list[str] = []

    for card in cards:
        try:
            prints = _scryfall_prints(card.card_name)
        except Exception:
            unmatched.append(card.card_name)
            time.sleep(0.12)
            continue
        label, p = _classify_printing(prints)
        if not label or p is None:
            unmatched.append(card.card_name)
            time.sleep(0.12)
            continue

        card.set_name = label
        if label == "The Hobbit":
            to_hobbit += 1
        elif label == "The Lord of the Rings":
            to_lotr += 1
        else:
            other_set += 1

        # Fill metadata only where empty (never overwrite curated workbook data).
        if not card.collector_number:
            card.collector_number = str(p.get("collector_number") or "")
        if not card.rarity:
            card.rarity = _RARITY_MAP.get((p.get("rarity") or "").lower(), "")
        if not card.colour:
            card.colour = _colour_label(p.get("color_identity") or p.get("colors"))
        if not card.mana_cost:
            face = (p.get("card_faces") or [{}])[0]
            card.mana_cost = p.get("mana_cost") or face.get("mana_cost") or ""
        if not card.card_type:
            card.card_type = p.get("type_line") or ""
        if not card.oracle_text:
            face = (p.get("card_faces") or [{}])[0]
            card.oracle_text = p.get("oracle_text") or face.get("oracle_text") or ""
        if not card.legendary:
            card.legendary = "legendary" in (p.get("type_line") or "").lower()

        updated += 1
        time.sleep(0.12)  # be polite to Scryfall

    db.commit()
    return {
        "checked": len(cards),
        "updated": updated,
        "to_hobbit": to_hobbit,
        "to_lord_of_the_rings": to_lotr,
        "other_real_set": other_set,
        "unmatched": unmatched,
    }


PROJECT_SETS = ("The Hobbit", "The Lord of the Rings")


@app.post("/api/cards/cleanup")
def cleanup_non_scope(db: Session = Depends(get_db)) -> dict:
    """Delete every card whose set is not one of the two project sets.

    Removing a card also removes its deck slots and wishlist entries (cascade),
    keeping the collection and the Aragorn deck within the Tolkien project scope.
    """
    doomed = db.query(Card).filter(
        Card.set_name.notin_(PROJECT_SETS),
        func.lower(Card.card_name).notin_(BASIC_LANDS),
    ).all()
    removed = [{"name": c.card_name, "set": c.set_name} for c in doomed]
    for c in doomed:
        db.delete(c)
    db.commit()
    return {"deleted": len(removed), "cards": removed}


def _colour_identity(card: Card) -> set[str]:
    letters = set(re.findall(r"[WUBRG]", (card.mana_cost or "").upper()))
    return letters


@app.get("/api/decks/aragorn/validation")
def validate_deck(db: Session = Depends(get_db)) -> dict:
    slots = db.query(DeckCard).join(Card).all()
    errors: list[str] = []
    warnings: list[str] = []

    total = sum(s.quantity for s in slots)
    if total != DECK_SIZE:
        errors.append(f"Deck has {total} cards, must be exactly {DECK_SIZE} (including commander).")

    commanders = [s for s in slots if s.is_commander]
    if len(commanders) == 0:
        errors.append("No commander set. Mark Aragorn, the Uniter as commander.")
    elif len(commanders) > 1:
        errors.append("More than one commander marked.")

    # Singleton (non-basic lands): each card_id appears once.
    seen: dict[int, int] = {}
    for s in slots:
        seen[s.card_id] = seen.get(s.card_id, 0) + s.quantity
    for card_id, qty in seen.items():
        card = db.get(Card, card_id)
        name_l = (card.card_name or "").strip().lower() if card else ""
        is_basic = card and (
            "basic" in (card.card_type or "").lower() or name_l in BASIC_LANDS
        )
        if qty > 1 and not is_basic:
            errors.append(f"Singleton violation: {card.card_name if card else card_id} x{qty}.")

    # Colour identity + project set restriction + owned check.
    for s in slots:
        card = s.card
        outside = _colour_identity(card) - ALLOWED_COLOUR_IDENTITY
        if outside:
            warnings.append(
                f"{card.card_name}: colour identity {sorted(outside)} outside Bant (W/U/G)."
            )
        setn = (card.set_name or "").strip().lower()
        # Only warn when the set is KNOWN and clearly outside the Middle-earth
        # project scope. Uncatalogued (Unknown) cards and basic lands are exempt.
        name_l = (card.card_name or "").strip().lower()
        in_scope = (
            setn in ("", "unknown")
            or name_l in BASIC_LANDS
            or "hobbit" in setn
            or "lord of the rings" in setn
            or "middle-earth" in setn
            or "middle earth" in setn
        )
        if not in_scope:
            warnings.append(f"{card.card_name}: outside project set scope ({card.set_name}).")
        if card.quantity < s.quantity and s.status != "Need":
            warnings.append(f"{card.card_name}: marked {s.status} but only {card.quantity} owned.")

    owned_slots = sum(s.quantity for s in slots if s.card.quantity >= s.quantity)
    return {
        "valid": len(errors) == 0,
        "total_cards": total,
        "target": DECK_SIZE,
        "owned_slots": owned_slots,
        "need_slots": total - owned_slots,
        "errors": errors,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Import / Export / Backup
# --------------------------------------------------------------------------- #
@app.post("/api/import")
async def import_upload(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    data = await file.read()
    result = import_file(db, file.filename or "upload.csv", data)
    return {
        "added": result.added,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "rejected": result.rejected,
        "issues": result.issues[:100],
    }


@app.get("/api/export/collection.csv")
def export_collection(db: Session = Depends(get_db)) -> PlainTextResponse:
    import csv

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Set", "Card Name", "Collector Number", "Rarity", "Colour", "Mana Cost",
            "Card Type", "Subtype", "Power", "Toughness", "Oracle Text / Ability",
            "Legendary?", "Creature Type", "Ring Tempts You?", "Food?", "Treasure?",
            "Ring / The One Ring Synergy?", "Aragorn Synergy (1-5)", "Gandalf Synergy (1-5)",
            "Fellowship / Legends Synergy (1-5)", "Commander Role", "Owned?", "Quantity", "Notes",
        ]
    )
    for c in db.query(Card).order_by(Card.set_name, Card.card_name).all():
        writer.writerow(
            [
                c.set_name, c.card_name, c.collector_number, c.rarity, c.colour, c.mana_cost,
                c.card_type, c.subtype, c.power, c.toughness, c.oracle_text,
                "Yes" if c.legendary else "No", c.creature_type,
                "Yes" if c.ring_tempts else "No", "Yes" if c.food else "No",
                "Yes" if c.treasure else "No", "Yes" if c.ring_synergy else "No",
                c.aragorn_synergy, c.gandalf_synergy, c.fellowship_synergy,
                c.commander_role, "Yes" if c.quantity > 0 else "No", c.quantity, c.notes,
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=collection.csv"},
    )


@app.get("/api/backup")
def backup_db() -> StreamingResponse:
    if not Path(DB_PATH).exists():
        raise HTTPException(status_code=404, detail="Database not found")
    buf = io.BytesIO()
    with open(DB_PATH, "rb") as f:
        shutil.copyfileobj(f, buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=mtg-backup.db"},
    )


# --------------------------------------------------------------------------- #
# Static frontend (served last so /api routes take precedence)
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
