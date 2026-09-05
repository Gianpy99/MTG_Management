"""Pydantic schemas for the API."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    set_name: str
    card_name: str
    collector_number: str
    rarity: str
    colour: str
    mana_cost: str
    card_type: str
    subtype: str
    power: str
    toughness: str
    oracle_text: str
    legendary: bool
    creature_type: str
    ring_tempts: bool
    food: bool
    treasure: bool
    ring_synergy: bool
    aragorn_synergy: int
    gandalf_synergy: int
    fellowship_synergy: int
    commander_role: str
    notes: str
    quantity: int


class QuantityUpdate(BaseModel):
    quantity: int


class WishlistIn(BaseModel):
    card_id: int
    purpose: str = "deck"
    priority: str = "P2"
    target_price: float = 0.0
    max_price: float = 0.0
    status: str = "open"
    notes: str = ""


class WishlistOut(WishlistIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    card: CardOut


class DeckCardIn(BaseModel):
    card_id: int
    quantity: int = 1
    role: str = ""
    is_commander: bool = False
    status: str = "Owned"
    notes: str = ""


class DeckCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    card_id: int
    quantity: int
    role: str
    is_commander: bool
    status: str
    notes: str
    card: CardOut


class DeckImportIn(BaseModel):
    text: str
    replace: bool = True
