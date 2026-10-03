"""Bridge between the collection app and the MTG Forge Engine service.

The collection DB stays the source of truth and is only *read* here: decks are
exported to Forge ``.dck`` text and sent to the Forge service, which owns its
own results database. Configure the service URL with ``FORGE_URL``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import Card, Deck, DeckCard

FORGE_URL = os.environ.get("FORGE_URL", "http://mtg-forge:8787").rstrip("/")
JOB_ID_RE = re.compile(r"^(sim|audit)_[0-9]{8}_[0-9]{6}_[0-9a-f]{4}$")
TEST_DECK_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# Cards imported without a Scryfall edition only carry the set name.
SET_NAME_CODES = {"the hobbit": "HOB", "the lord of the rings": "LTR"}
BASIC_LANDS = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
}

router = APIRouter()

Scope = Literal["all", "owned"]


# --------------------------------------------------------------------------- #
# .dck export
# --------------------------------------------------------------------------- #
def _printing(card: Card) -> tuple[str, str]:
    """(set code, collector number) for a Forge line; empty when unknown."""
    number = (card.collector_number or "").strip()
    prefixed = re.match(r"^([A-Za-z0-9]{2,5})-(.+)$", number)
    if prefixed:  # e.g. "LTR-77" stored under the LTC precon
        return prefixed.group(1).upper(), prefixed.group(2)
    code = (card.edition or "").strip().upper() or SET_NAME_CODES.get((card.set_name or "").strip().lower(), "")
    return code, (number if code else "")


def _is_basic(card: Card) -> bool:
    return "basic" in (card.card_type or "").lower() or (card.card_name or "").strip().lower() in BASIC_LANDS


def forge_deck_name(deck: Deck) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", deck.name or deck.slug).strip("_")[:80] or deck.slug


def build_forge_dck(db: Session, deck: Deck, scope: str = "all") -> str:
    """Forge .dck for a collection deck.

    ``scope="owned"`` keeps only physically owned copies (basic lands are always
    available), i.e. the PRD's REAL_COLLECTION view; ``all`` is the full list.
    """
    slots = (
        db.query(DeckCard).join(Card).filter(DeckCard.deck_id == deck.id)
        .order_by(DeckCard.is_commander.desc(), Card.card_name).all()
    )
    remaining = {s.card_id: (s.card.quantity or 0) for s in slots}
    sections: dict[str, dict[int, int]] = {"Commander": {}, "Main": {}, "Sideboard": {}}
    cards: dict[int, Card] = {}
    for s in slots:
        section = "Commander" if s.is_commander else ("Sideboard" if s.board == "side" else "Main")
        qty = s.quantity
        if scope == "owned" and not _is_basic(s.card):
            qty = min(qty, remaining[s.card_id])
            remaining[s.card_id] -= qty
        if qty <= 0:
            continue
        cards[s.card_id] = s.card
        sections[section][s.card_id] = sections[section].get(s.card_id, 0) + qty

    out = ["[metadata]", f"Name={forge_deck_name(deck)}"]
    for title, entries in sections.items():
        if not entries:
            continue
        out.append(f"[{title}]")
        for card_id, qty in sorted(entries.items(), key=lambda e: cards[e[0]].card_name.lower()):
            card = cards[card_id]
            line = f"{qty} {card.card_name.strip()}"
            code, number = _printing(card)
            if code:
                line += f"|{code}"
                if number:
                    line += f"|[{number}]"
            out.append(line)
    return "\n".join(out) + "\n"


def _forge_format(deck: Deck) -> str:
    return "commander" if deck.format == "commander" else "constructed"


def _deck_payload(db: Session, deck: Deck, scope: str) -> dict:
    return {"name": forge_deck_name(deck), "dck": build_forge_dck(db, deck, scope), "ref": deck.slug}


def _get_deck(db: Session, slug: str) -> Deck:
    deck = db.query(Deck).filter(Deck.slug == slug).first()
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return deck


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
def _call(method: str, path: str, body: dict | None = None, params: dict | None = None,
          raw: bool = False, timeout: int = 20):
    url = FORGE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(text)
        except ValueError:
            detail = text
        if isinstance(detail, dict) and set(detail) == {"detail"}:
            detail = detail["detail"]
        raise HTTPException(status_code=e.code, detail=detail)
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=503, detail=f"Forge engine not reachable at {FORGE_URL}: {e}")
    return payload.decode("utf-8", "replace") if raw else json.loads(payload)


def _job_path(job_id: str) -> str:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Invalid job id")
    return f"/api/simulations/{job_id}"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
class ForgeAuditIn(BaseModel):
    scope: Scope = "all"


class ForgeSimulationIn(BaseModel):
    deck: str = Field(min_length=1, max_length=120)
    opponent_deck: Optional[str] = Field(default=None, max_length=120)
    opponent_test_deck: Optional[str] = Field(default=None, max_length=80)
    games: int = Field(default=10, ge=1)
    scope: Scope = "all"
    priority: Literal["HIGH", "NORMAL", "LOW"] = "NORMAL"
    seed: Optional[int] = Field(default=None, ge=0)
    allow_invalid: bool = False


@router.get("/api/forge/status")
def forge_status() -> dict:
    try:
        return {"forge_url": FORGE_URL, **_call("GET", "/api/forge/status", timeout=6)}
    except HTTPException as exc:
        return {"status": "unavailable", "forge_url": FORGE_URL, "error": exc.detail}


@router.get("/api/forge/test-decks")
def forge_test_decks() -> list:
    return _call("GET", "/api/forge/test-decks")


@router.get("/api/forge/matchups")
def forge_matchups() -> list:
    return _call("GET", "/api/matchups")


@router.get("/api/decks/{slug}/forge.dck", response_class=PlainTextResponse)
def deck_forge_dck(slug: str, scope: Scope = "all", db: Session = Depends(get_db)) -> PlainTextResponse:
    deck = _get_deck(db, slug)
    return PlainTextResponse(
        build_forge_dck(db, deck, scope),
        headers={"Content-Disposition": f'attachment; filename="{forge_deck_name(deck)}.dck"'},
    )


@router.post("/api/decks/{slug}/forge/audit")
def deck_forge_audit(slug: str, payload: ForgeAuditIn, db: Session = Depends(get_db)) -> dict:
    deck = _get_deck(db, slug)
    return _call("POST", "/api/forge/audit",
                 {"deck": _deck_payload(db, deck, payload.scope), "format": _forge_format(deck)})


@router.post("/api/forge/simulations")
def forge_simulate(payload: ForgeSimulationIn, db: Session = Depends(get_db)) -> dict:
    deck = _get_deck(db, payload.deck)
    if bool(payload.opponent_deck) == bool(payload.opponent_test_deck):
        raise HTTPException(status_code=422, detail="Choose exactly one opponent (collection deck or test deck)")
    if payload.opponent_deck:
        opp = _get_deck(db, payload.opponent_deck)
        if _forge_format(opp) != _forge_format(deck):
            raise HTTPException(status_code=422, detail="Both decks must be the same format")
        opponent: dict | str = _deck_payload(db, opp, payload.scope)
    else:
        if not TEST_DECK_RE.match(payload.opponent_test_deck or ""):
            raise HTTPException(status_code=422, detail="Invalid test deck name")
        opponent = payload.opponent_test_deck
    return _call("POST", "/api/simulations", {
        "deck_a": _deck_payload(db, deck, payload.scope),
        "deck_b": opponent,
        "format": _forge_format(deck),
        "games": payload.games,
        "priority": payload.priority,
        "seed": payload.seed,
        "allow_invalid": payload.allow_invalid,
    })


@router.get("/api/forge/simulations")
def forge_list(deck: Optional[str] = None, limit: int = 20) -> list:
    return _call("GET", "/api/simulations", params={"deck": deck, "limit": max(1, min(limit, 200))})


@router.get("/api/forge/simulations/{job_id}")
def forge_job(job_id: str) -> dict:
    return _call("GET", _job_path(job_id))


@router.get("/api/forge/simulations/{job_id}/results")
def forge_job_results(job_id: str) -> dict:
    return _call("GET", _job_path(job_id) + "/results")


@router.get("/api/forge/simulations/{job_id}/log", response_class=PlainTextResponse)
def forge_job_log(job_id: str) -> PlainTextResponse:
    return PlainTextResponse(_call("GET", _job_path(job_id) + "/log", raw=True, timeout=60))


@router.post("/api/forge/simulations/{job_id}/cancel")
def forge_job_cancel(job_id: str) -> dict:
    return _call("POST", _job_path(job_id) + "/cancel")
