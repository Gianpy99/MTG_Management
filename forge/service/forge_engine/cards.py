"""Forge card database index + .dck parsing/normalisation.

Forge silently drops cards it cannot resolve and still plays the game with a
smaller deck, and silently falls back to another printing when a set code or
collector number is wrong. So every deck is checked against Forge's own card
data *before* it is run:

- names are resolved against ``res/cardsfolder`` (split cards keep the full
  ``A // B`` name; adventure / transform / MDFC cards use the front face);
- set codes and collector numbers are checked against ``res/editions``.
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


def norm_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", s).strip().casefold()


# --------------------------------------------------------------------------- #
# Card index
# --------------------------------------------------------------------------- #
_NAME_RE = re.compile(r"^Name:(.+?)\s*$", re.M)
_SPLIT_RE = re.compile(r"^AlternateMode:\s*Split\s*$", re.M)
_EDITION_CARD_RE = re.compile(r"^(\S+)\s+(?:[CURMSLTBNP]\s+)?(.+?)(?:\s+@.*)?$")
_NON_CARD_SECTIONS = {"metadata", "tokens", "other"}


class CardIndex:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # norm key -> canonical Forge name
        self.editions: dict[str, dict[str, str]] = {}  # SET -> collector -> name key
        self.edition_names: dict[str, set[str]] = {}  # SET -> name keys
        self.aliases: dict[str, str] = {}  # alias code -> SET

    @classmethod
    def load(cls, res_dir: Path) -> "CardIndex":
        idx = cls()
        idx._load_cards(res_dir / "cardsfolder")
        idx._load_editions(res_dir / "editions")
        return idx

    # -- cards ------------------------------------------------------------- #
    def add_card_script(self, text: str) -> None:
        faces = _NAME_RE.findall(text)
        if not faces:
            return
        if _SPLIT_RE.search(text) and len(faces) >= 2:
            canonical = f"{faces[0]} // {faces[1]}"
        else:
            canonical = faces[0]
        self.names[norm_key(canonical)] = canonical
        if len(faces) >= 2:
            self.names.setdefault(norm_key(f"{faces[0]} // {faces[1]}"), canonical)

    def _load_cards(self, folder: Path) -> None:
        for z in sorted(folder.glob("*.zip")):
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    if info.filename.endswith(".txt"):
                        self.add_card_script(zf.read(info).decode("utf-8", "replace"))
        for f in folder.rglob("*.txt"):
            self.add_card_script(f.read_text("utf-8", "replace"))

    # -- editions ---------------------------------------------------------- #
    def add_edition(self, text: str) -> None:
        section = ""
        codes: list[str] = []
        cards: list[tuple[str, str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"\[(.+)\]", line)
            if m:
                section = m.group(1).strip().lower()
                continue
            if section == "metadata":
                key, _, value = line.partition("=")
                if key in ("Code", "Code2", "Alias") and value.strip():
                    codes.append(value.strip().upper())
                continue
            if section in _NON_CARD_SECTIONS or "=" in line.split("@")[0]:
                continue
            cm = _EDITION_CARD_RE.match(line)
            if cm:
                cards.append((cm.group(1), cm.group(2).strip()))
        if not codes:
            return
        main = codes[0]
        coll = self.editions.setdefault(main, {})
        names = self.edition_names.setdefault(main, set())
        for number, name in cards:
            key = norm_key(self.names.get(norm_key(name), name))
            coll.setdefault(number, key)
            names.add(key)
        for alias in codes[1:]:
            self.aliases.setdefault(alias, main)

    def _load_editions(self, folder: Path) -> None:
        for f in sorted(folder.glob("*.txt")):
            self.add_edition(f.read_text("utf-8", "replace"))

    # -- lookups ----------------------------------------------------------- #
    def resolve(self, name: str) -> str | None:
        key = norm_key(name)
        if key in self.names:
            return self.names[key]
        if " // " in name:
            front = norm_key(name.split(" // ")[0])
            if front in self.names:
                return self.names[front]
        return None

    def set_code(self, code: str) -> str | None:
        code = code.upper()
        if code in self.editions:
            return code
        return self.aliases.get(code)

    def stats(self) -> dict:
        return {"cards": len(set(self.names.values())), "editions": len(self.editions)}


# --------------------------------------------------------------------------- #
# .dck parsing / normalisation
# --------------------------------------------------------------------------- #
CARD_SECTIONS = ("commander", "main", "sideboard")
_LINE_RE = re.compile(r"^(\d+)\s*x?\s+(.+)$", re.I)


@dataclass
class Issue:
    severity: str  # ERROR (structure) | RED (unsupported) | YELLOW (AI fallback) | INFO
    issue_type: str
    message: str
    card_name: str = ""
    set_code: str = ""
    collector_number: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeckLine:
    section: str
    qty: int
    name: str
    set_code: str = ""
    collector: str = ""
    art: str = ""


@dataclass
class ParsedDeck:
    name: str = ""
    lines: list[DeckLine] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)

    def count(self, section: str) -> int:
        return sum(ln.qty for ln in self.lines if ln.section == section)

    @property
    def has_commander(self) -> bool:
        return any(ln.section == "commander" for ln in self.lines)


def parse_dck(text: str) -> ParsedDeck:
    deck = ParsedDeck()
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[(.+)\]", line)
        if m:
            section = m.group(1).strip().lower()
            continue
        if section == "metadata":
            key, _, value = line.partition("=")
            if key.strip().lower() == "name":
                deck.name = value.strip()
            continue
        if section not in CARD_SECTIONS:
            continue
        lm = _LINE_RE.match(line)
        if not lm or int(lm.group(1)) <= 0:
            deck.malformed.append(line)
            continue
        parts = [p.strip() for p in lm.group(2).split("|")]
        dl = DeckLine(section=section, qty=int(lm.group(1)), name=parts[0])
        if len(parts) > 1:
            dl.set_code = parts[1].upper()
        if len(parts) > 2:
            cm = re.fullmatch(r"\[(.+)\]", parts[2])
            if cm:
                dl.collector = cm.group(1).strip()
            else:
                dl.art = parts[2]
        if not dl.name:
            deck.malformed.append(line)
            continue
        deck.lines.append(dl)
    return deck


def safe_deck_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned[:80] or "Deck"


def structure_issues(deck: ParsedDeck, fmt: str) -> list[Issue]:
    issues = [Issue("ERROR", "malformed_line", f"Malformed line: {ln}") for ln in deck.malformed]
    main, cmd = deck.count("main"), deck.count("commander")
    if fmt == "commander":
        if not 1 <= cmd <= 2:
            issues.append(Issue("ERROR", "commander_section", f"[Commander] must hold 1-2 cards, found {cmd}"))
        if main + cmd != 100:
            issues.append(Issue("ERROR", "deck_size", f"Commander deck must have 100 cards, found {main + cmd}"))
    else:
        if cmd:
            issues.append(Issue("ERROR", "commander_section", "Constructed deck must not have a [Commander] section"))
        if main < 60:
            issues.append(Issue("ERROR", "deck_size", f"Constructed deck needs at least 60 main cards, found {main}"))
    return issues


def normalise(deck: ParsedDeck, index: CardIndex | None) -> tuple[str, list[Issue]]:
    """Return a Forge-ready .dck plus the card issues found against Forge's database."""
    issues: list[Issue] = []
    out_lines: dict[str, list[str]] = {s: [] for s in CARD_SECTIONS}
    for ln in deck.lines:
        name, set_code, collector = ln.name, ln.set_code, ln.collector
        if index is not None:
            canonical = index.resolve(name)
            if canonical is None:
                issues.append(Issue("RED", "unsupported_card",
                                    "Forge cannot resolve this card; Forge would silently drop it",
                                    name, set_code, collector))
            else:
                if canonical != name:
                    issues.append(Issue("INFO", "name_normalised", f"Renamed to Forge name '{canonical}'",
                                        name, set_code, collector))
                name = canonical
                if set_code:
                    resolved_set = index.set_code(set_code)
                    key = norm_key(canonical)
                    if resolved_set is None:
                        issues.append(Issue("INFO", "set_unknown", "Set code unknown to Forge; default printing used",
                                            name, set_code, collector))
                        set_code, collector = "", ""
                    elif key not in index.edition_names.get(resolved_set, set()):
                        issues.append(Issue("INFO", "card_not_in_set", "Card not in this set in Forge; default printing used",
                                            name, set_code, collector))
                        set_code, collector = "", ""
                    else:
                        set_code = resolved_set
                        if collector and index.editions[resolved_set].get(collector) != key:
                            issues.append(Issue("INFO", "collector_mismatch",
                                                "Collector number not found for this card; set kept",
                                                name, set_code, collector))
                            collector = ""
        entry = f"{ln.qty} {name}"
        if set_code:
            entry += f"|{set_code}"
            if collector:
                entry += f"|[{collector}]"
            elif ln.art and ln.set_code == set_code:
                entry += f"|{ln.art}"
        out_lines[ln.section].append(entry)

    text = ["[metadata]", f"Name={safe_deck_name(deck.name)}"]
    for section, title in (("commander", "Commander"), ("main", "Main"), ("sideboard", "Sideboard")):
        if out_lines[section]:
            text += [f"[{title}]", *out_lines[section]]
    return "\n".join(text) + "\n", issues
