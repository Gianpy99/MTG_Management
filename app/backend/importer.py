"""Workbook / CSV import logic.

The Excel workbook is the authoritative seed. Import is transactional and
reports validation issues rather than silently changing data.

Rows are matched to existing cards by Set + Card Name (case-insensitive),
narrowed by Edition and Collector Number when provided. Only non-blank cells
are applied, so a file with just ``Set, Card Name, Quantity`` updates the owned
copies without touching any other field.

Quantity columns:
* ``Quantity`` sets the owned copies (duplicate rows in one file add up).
* ``Add Quantity`` adds (or, if negative, removes) copies, e.g. a new delivery.
* ``Owned?`` is only used when neither is present: ``Yes`` guarantees at least
  one copy; ``No`` never lowers an existing quantity.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from models import Card, ImportLog

# Canonical header -> Card attribute mapping (order-independent, case-insensitive).
HEADER_MAP = {
    "set": "set_name",
    "card name": "card_name",
    "collector number": "collector_number",
    "edition": "edition",
    "rarity": "rarity",
    "colour": "colour",
    "color": "colour",
    "mana cost": "mana_cost",
    "card type": "card_type",
    "subtype": "subtype",
    "power": "power",
    "toughness": "toughness",
    "oracle text / ability": "oracle_text",
    "oracle text": "oracle_text",
    "legendary?": "legendary",
    "creature type": "creature_type",
    "ring tempts you?": "ring_tempts",
    "food?": "food",
    "treasure?": "treasure",
    "ring / the one ring synergy?": "ring_synergy",
    "aragorn synergy (1-5)": "aragorn_synergy",
    "gandalf synergy (1-5)": "gandalf_synergy",
    "fellowship / legends synergy (1-5)": "fellowship_synergy",
    "commander role": "commander_role",
    "owned?": "_owned",
    "quantity": "_quantity",
    "qty": "_quantity",
    "add quantity": "_add_quantity",
    "add qty": "_add_quantity",
    "notes": "notes",
}

BOOL_FIELDS = {"legendary", "ring_tempts", "food", "treasure", "ring_synergy"}
INT_FIELDS = {"aragorn_synergy", "gandalf_synergy", "fellowship_synergy"}


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"yes", "y", "true", "1"}


def _to_int(value: str) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _to_quantity(value: str | None) -> int | None:
    """Parse a quantity cell; ``None`` when blank, ``ValueError`` when invalid."""
    text = str(value or "").strip()
    if not text:
        return None
    number = float(text.replace(",", "."))
    if not number.is_integer():
        raise ValueError(text)
    return int(number)


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    copies_delta: int = 0
    issues: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"added={self.added} updated={self.updated} "
            f"unchanged={self.unchanged} rejected={self.rejected} "
            f"copies_delta={self.copies_delta:+d} issues={len(self.issues)}"
        )


def _decode(data: bytes) -> str:
    # Excel's plain "CSV" save uses the Windows code page, not UTF-8.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _rows_from_csv(text: str) -> list[dict[str, str]]:
    # Excel with an Italian/European locale saves CSV with ';' separators.
    header = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=header.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [dict(row) for row in reader]


def _rows_from_xlsx(data: bytes) -> list[dict[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb["Collection"] if "Collection" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    first = next(rows, None)
    if first is None:
        return []
    headers = [str(h).strip() if h is not None else "" for h in first]
    out: list[dict[str, str]] = []
    for r in rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        out.append({headers[i]: ("" if c is None else str(c)) for i, c in enumerate(r) if i < len(headers)})
    return out


def _match(candidates: list[Card], collector: str, edition: str) -> tuple[Card | None, str | None]:
    """Pick the existing card for a row: (card, None), (None, None) = new card, or (None, error)."""
    if edition:
        same = [c for c in candidates if _norm(c.edition) == _norm(edition)]
        candidates = same or [c for c in candidates if not _norm(c.edition)]
    blank = [c for c in candidates if not _norm(c.collector_number)]
    if collector:
        exact = [c for c in candidates if _norm(c.collector_number) == _norm(collector)]
        if exact:
            return exact[0], None
        # A card stored without a collector number is taken to be this printing.
        return (blank[0], None) if len(blank) == 1 else (None, None)
    if len(candidates) <= 1:
        return (candidates[0] if candidates else None), None
    if len(blank) == 1:
        return blank[0], None
    numbers = ", ".join(sorted(c.collector_number or "?" for c in candidates))
    return None, f"matches {len(candidates)} printings (#{numbers}); add Collector Number or Edition"


def import_rows(db: Session, rows: list[dict[str, str]]) -> ImportResult:
    result = ImportResult()
    index: dict[tuple[str, str], list[Card]] = {}
    for card in db.query(Card).all():
        index.setdefault((_norm(card.set_name), _norm(card.card_name)), []).append(card)
    # Cards touched / given an absolute Quantity in THIS batch, so duplicate rows
    # are merged (quantities summed) instead of violating the unique constraint.
    seen: set[int] = set()
    quantity_set: set[int] = set()

    for idx, raw in enumerate(rows, start=2):
        mapped: dict[str, str] = {}
        for key, value in raw.items():
            attr = HEADER_MAP.get(str(key).strip().lower())
            if attr and value is not None and str(value).strip() != "":
                mapped[attr] = str(value).strip()

        name = mapped.get("card_name", "")
        set_name = mapped.get("set_name", "")
        if not name:
            result.rejected += 1
            result.issues.append(f"row {idx}: missing card name")
            continue
        if not set_name:
            result.rejected += 1
            result.issues.append(f"row {idx}: '{name}' missing set")
            continue
        try:
            quantity = _to_quantity(mapped.pop("_quantity", None))
            add_quantity = _to_quantity(mapped.pop("_add_quantity", None))
        except ValueError:
            result.rejected += 1
            result.issues.append(f"row {idx}: '{name}' invalid quantity")
            continue
        owned = mapped.pop("_owned", None)
        collector = mapped.get("collector_number", "")

        candidates = index.setdefault((_norm(set_name), _norm(name)), [])
        target, problem = _match(candidates, collector, mapped.get("edition", ""))
        if problem:
            result.rejected += 1
            result.issues.append(f"row {idx}: '{name}' ({set_name}) {problem}")
            continue

        is_new = target is None
        if target is None:
            target = Card(set_name=set_name, card_name=name, collector_number=collector, quantity=0)
            candidates.append(target)
            db.add(target)
        elif id(target) in seen:
            result.issues.append(f"row {idx}: duplicate identity '{name}' ({set_name} {collector}) merged")
        seen.add(id(target))

        changed = False
        for attr, value in mapped.items():
            if attr in ("set_name", "card_name"):
                continue
            if attr == "collector_number" and _norm(target.collector_number):
                continue
            if attr in BOOL_FIELDS:
                new_val = _to_bool(value)
            elif attr in INT_FIELDS:
                new_val = _to_int(value)
            else:
                new_val = value
            if getattr(target, attr, None) != new_val:
                setattr(target, attr, new_val)
                changed = True

        current = target.quantity or 0
        new_qty = current
        if quantity is not None:
            new_qty = current + quantity if id(target) in quantity_set else quantity
            quantity_set.add(id(target))
        if add_quantity is not None:
            new_qty += add_quantity
        if quantity is None and add_quantity is None and owned is not None and _to_bool(owned):
            new_qty = max(new_qty, 1)
        new_qty = max(0, new_qty)
        if new_qty != current:
            target.quantity = new_qty
            result.copies_delta += new_qty - current
            changed = True

        if is_new:
            result.added += 1
        elif changed:
            result.updated += 1
        else:
            result.unchanged += 1

    db.add(ImportLog(summary=result.summary()))
    db.commit()
    return result


def import_file(db: Session, filename: str, data: bytes) -> ImportResult:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        rows = _rows_from_xlsx(data)
    else:
        rows = _rows_from_csv(_decode(data))
    return import_rows(db, rows)
