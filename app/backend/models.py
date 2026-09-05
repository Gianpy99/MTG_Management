"""SQLAlchemy models.

Pragmatic schema derived from the PRD data model. Card identity, ownership
(quantity), wishlist, and the Aragorn Commander deck are separated so that
unique-card completion stays independent from duplicate quantities.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Card(Base):
    """Canonical card identity + the synergy/role metadata from the workbook.

    ``quantity`` is the owned physical copies (collection). 0 = missing.
    """

    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("set_name", "collector_number", "card_name", name="uq_card_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_name: Mapped[str] = mapped_column(String, index=True)
    card_name: Mapped[str] = mapped_column(String, index=True)
    collector_number: Mapped[str] = mapped_column(String, default="")
    rarity: Mapped[str] = mapped_column(String, default="")
    colour: Mapped[str] = mapped_column(String, default="")
    mana_cost: Mapped[str] = mapped_column(String, default="")
    card_type: Mapped[str] = mapped_column(String, default="")
    subtype: Mapped[str] = mapped_column(String, default="")
    power: Mapped[str] = mapped_column(String, default="")
    toughness: Mapped[str] = mapped_column(String, default="")
    oracle_text: Mapped[str] = mapped_column(Text, default="")
    legendary: Mapped[bool] = mapped_column(Boolean, default=False)
    creature_type: Mapped[str] = mapped_column(String, default="")
    ring_tempts: Mapped[bool] = mapped_column(Boolean, default=False)
    food: Mapped[bool] = mapped_column(Boolean, default=False)
    treasure: Mapped[bool] = mapped_column(Boolean, default=False)
    ring_synergy: Mapped[bool] = mapped_column(Boolean, default=False)
    aragorn_synergy: Mapped[int] = mapped_column(Integer, default=0)
    gandalf_synergy: Mapped[int] = mapped_column(Integer, default=0)
    fellowship_synergy: Mapped[int] = mapped_column(Integer, default=0)
    commander_role: Mapped[str] = mapped_column(String, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    quantity: Mapped[int] = mapped_column(Integer, default=0)

    wishlist_items: Mapped[list["WishlistItem"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )
    deck_cards: Mapped[list["DeckCard"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class WishlistItem(Base):
    __tablename__ = "wishlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String, default="deck")  # deck | collection | collector
    priority: Mapped[str] = mapped_column(String, default="P2")  # P1 | P2 | P3 | P4 | Watch
    target_price: Mapped[float] = mapped_column(Float, default=0.0)
    max_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="open")  # open | bought | dropped
    notes: Mapped[str] = mapped_column(Text, default="")

    card: Mapped[Card] = relationship(back_populates="wishlist_items")


class DeckCard(Base):
    """A slot in the single Aragorn Commander deck."""

    __tablename__ = "deck_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    role: Mapped[str] = mapped_column(String, default="")  # Ramp, Draw, Removal, ...
    is_commander: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="Owned")  # Owned | Need | Maybe
    notes: Mapped[str] = mapped_column(Text, default="")

    card: Mapped[Card] = relationship(back_populates="deck_cards")


class ImportLog(Base):
    __tablename__ = "import_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    summary: Mapped[str] = mapped_column(Text, default="")
