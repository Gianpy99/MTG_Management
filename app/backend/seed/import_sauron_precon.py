"""One-time, resumable import of the received Sauron precon into a running server.

Preview by default; --apply writes the collection and creates a separate owned
deck. Keep the generated journal until the import has been verified.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DECKLIST = Path(__file__).with_name("sauron_lord_of_rings_collection.txt")
DECK_NAME = "Sauron, Lord of the Rings (Owned)"
SET_NAME = "The Lord of the Rings"
HEADERS = {"User-Agent": "MTGManagement/1.0 (personal collection import)", "Accept": "application/json"}
LTR_ONLY = {
    "Bitter Downfall", "Fiery Inscription", "Grishnákh, Brash Instigator",
    "Island", "Mountain", "Swamp", "The Mouth of Sauron", "Treason of Isengard",
    "Troll of Khazad-dûm", "Voracious Fell Beast",
}


def request(url: str, data: bytes | None = None, content_type: str = "application/json",
            method: str | None = None):
    headers = {**HEADERS, "Content-Type": content_type}
    with urllib.request.urlopen(
        urllib.request.Request(url, data=data, headers=headers, method=method), timeout=30
    ) as response:
        return json.load(response)


def api(base: str, path: str, payload: dict | None = None, method: str | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    return request(base + path, data, method=method)


def printing_number(card: dict) -> tuple[int, str]:
    number = card["collector_number"]
    match = re.match(r"\d+", number)
    return (int(match.group()) if match else 99999, number)


def scryfall_prints(set_code: str, names: set[str]) -> dict[str, dict]:
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": f"set:{set_code}", "unique": "prints"}
    )
    found: dict[str, dict] = {}
    while url:
        data = request(url)
        for card in data["data"]:
            name = card["name"]
            if name in names and (name not in found or printing_number(card) < printing_number(found[name])):
                found[name] = card
        url = data.get("next_page")
        if url:
            time.sleep(0.12)
    missing = names - found.keys()
    if missing:
        raise ValueError(f"No {set_code} printing for: {sorted(missing)}")
    return found


def csv_row(card: dict, name: str, source: str) -> dict[str, str]:
    faces = card.get("card_faces") or []
    oracle = card.get("oracle_text") or "\n//\n".join(f.get("oracle_text", "") for f in faces)
    colours = card.get("colors") or []
    colour = "Multicolour" if len(colours) > 1 else (colours[0] if colours else "Land" if "Land" in card["type_line"] else "Colourless")
    number = card["collector_number"]
    return {
        "Set": SET_NAME,
        "Card Name": name,
        "Collector Number": number if source == "ltc" else f"LTR-{number}",
        "Edition": "ltc",
        "Rarity": {"common": "C", "uncommon": "U", "rare": "R", "mythic": "M"}[card["rarity"]],
        "Colour": colour,
        "Mana Cost": card.get("mana_cost") or (faces[0].get("mana_cost", "") if faces else ""),
        "Card Type": card["type_line"],
        "Oracle Text / Ability": oracle,
        "Legendary?": "Yes" if "Legendary" in card["type_line"] else "No",
        "Owned?": "No",
        "Notes": "" if source == "ltc" else "LTR printing grouped under LOTR Commander for this received deck.",
    }


def upload_csv(base: str, rows: list[dict[str, str]]) -> None:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    boundary = "mtg-sauron-precon-import"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sauron-precon.csv\"\r\n"
        "Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8") + out.getvalue().encode("utf-8") + f"\r\n--{boundary}--\r\n".encode("ascii")
    result = request(base + "/api/import", body, f"multipart/form-data; boundary={boundary}")
    if result["rejected"] or result["issues"] or result["added"] != len(rows):
        raise RuntimeError(f"Catalogue import did not add all {len(rows)} records: {result}")


def run(base: str, journal_path: Path, apply: bool) -> None:
    lines = DECKLIST.read_text(encoding="utf-8").splitlines()
    quantities = {}
    for line in lines:
        if line == "Deck":
            continue
        qty, name = line.split(" ", 1)
        if name in quantities:
            raise ValueError(f"Duplicate deck entry: {name}")
        quantities[name] = int(qty)
    if len(quantities) != 84 or sum(quantities.values()) != 100:
        raise ValueError("The received decklist must contain 84 names and 100 cards")

    decks = api(base, "/api/decks")
    deck = next((d for d in decks if d["name"] == DECK_NAME), None)
    if deck and not journal_path.exists():
        raise RuntimeError(f"{DECK_NAME} exists without an import journal; refusing to add copies again")
    cards = api(base, "/api/cards")
    by_name: dict[str, list[dict]] = {}
    for card in cards:
        by_name.setdefault(card["card_name"].casefold(), []).append(card)
    selected: dict[str, dict] = {}
    missing: set[str] = set()
    for name in quantities:
        matches = [
            c for c in by_name.get(name.casefold(), [])
            if c["set_name"] == SET_NAME and c["edition"] == "ltc"
        ]
        if matches:
            selected[name] = min(matches, key=printing_number)
        else:
            missing.add(name)

    print(f"Deck: {sum(quantities.values())} cards / {len(quantities)} names; "
          f"existing LOTR Commander records: {len(selected)}; new: {len(missing)}")
    if not apply:
        return

    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if journal["server"] != base or set(journal["targets"]) != set(quantities):
            raise RuntimeError(f"Journal {journal_path} is for another server or decklist")
    else:
        targets = {
            name: selected[name]["quantity"] + qty if name in selected else qty
            for name, qty in quantities.items()
        }
        journal = {"server": base, "targets": targets}
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("x", encoding="utf-8") as output:
            json.dump(journal, output, ensure_ascii=False, indent=2)

    if missing:
        ltc_names = missing - LTR_ONLY
        source = scryfall_prints("ltc", ltc_names) if ltc_names else {}
        ltr_names = missing & LTR_ONLY
        source.update(scryfall_prints("ltr", ltr_names) if ltr_names else {})
        upload_csv(base, [csv_row(source[name], name, "ltr" if name in LTR_ONLY else "ltc")
                          for name in sorted(missing)])
        cards = api(base, "/api/cards")
        for card in cards:
            if card["card_name"] in missing and card["edition"] == "ltc" and card["set_name"] == SET_NAME:
                selected[card["card_name"]] = card

    if set(selected) != set(quantities):
        raise RuntimeError(f"Missing catalogue records: {sorted(set(quantities) - set(selected))}")
    for name, card in selected.items():
        target = journal["targets"][name]
        if card["quantity"] < target:
            updated = api(base, f"/api/collection/{card['id']}", {"quantity": target}, method="PATCH")
            if updated["quantity"] != target:
                raise RuntimeError(f"Could not update quantity for {name}")
        elif card["quantity"] > target:
            raise RuntimeError(f"Quantity for {name} exceeds journal target; check concurrent edits")

    if not deck:
        deck = api(base, "/api/decks", {
            "name": DECK_NAME, "format": "commander",
            "commander_name": "Sauron, Lord of the Rings", "allowed_colours": "U,B,R",
            "notes": "Received Sauron LOTR Commander precon; all cards filed under LOTR Commander.",
        })
    existing_slots = {slot["card"]["card_name"]: slot for slot in api(base, f"/api/decks/{deck['slug']}/cards")}
    for name, qty in quantities.items():
        if name in existing_slots:
            slot = existing_slots[name]
            if slot["quantity"] != qty or slot["card_id"] != selected[name]["id"]:
                raise RuntimeError(f"Unexpected existing deck slot for {name}: {slot}")
            continue
        api(base, f"/api/decks/{deck['slug']}/cards", {
            "card_id": selected[name]["id"], "quantity": qty,
            "is_commander": name == "Sauron, Lord of the Rings",
        })
    validation = api(base, f"/api/decks/{deck['slug']}/validation")
    if not validation["valid"] or validation["main_cards"] != 100 or validation["need_slots"]:
        raise RuntimeError(f"Deck validation failed: {validation}")
    print(f"Imported {DECK_NAME}: {validation['main_cards']} cards, "
          f"{validation['owned_slots']} owned, validation passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", help="Base URL of the MTG server, e.g. http://127.0.0.1:8094")
    parser.add_argument("--apply", action="store_true", help="Import cards and create the owned deck")
    parser.add_argument("--journal", type=Path, help="Resume journal (defaults to backend/data/ per server)")
    args = parser.parse_args()
    base = args.server.rstrip("/")
    journal = args.journal or DECKLIST.parent.parent / "data" / (
        "sauron_precon_" + re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_") + ".json"
    )
    run(base, journal, args.apply)
