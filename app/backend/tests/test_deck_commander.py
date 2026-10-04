import pytest
from fastapi.testclient import TestClient

import main
from models import Card


@pytest.fixture
def client(db):
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def _commander_deck(client, name="Gandalf"):
    return client.post("/api/decks", json={"name": name, "format": "commander"}).json()["slug"]


def _filler(n):
    return "\n".join(f"1 Filler Card {i}" for i in range(n))


def test_import_marks_card_under_commander_header(client):
    slug = _commander_deck(client)
    text = "Commander\n1 Gandalf the White (LTR) 19\n\nDeck\n" + _filler(98) + "\n1 Island"
    r = client.post(f"/api/decks/{slug}/import", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["commander"] == "Gandalf the White"
    assert r.json()["total_cards"] == 100

    val = client.get(f"/api/decks/{slug}/validation").json()
    assert val["valid"], val["errors"]
    names = [s["card"]["card_name"] for s in client.get(f"/api/decks/{slug}/cards").json()]
    assert "Commander" not in names and "Deck" not in names


@pytest.mark.parametrize("hint", ["Commander: Gandalf the White", "// Commander: Gandalf the White"])
def test_import_commander_line_hint(client, hint):
    slug = _commander_deck(client)
    text = f"{hint}\n1 Gandalf the White\n" + _filler(99)
    r = client.post(f"/api/decks/{slug}/import", json={"text": text})
    assert r.json()["commander"] == "Gandalf the White"
    names = [s["card"]["card_name"] for s in client.get(f"/api/decks/{slug}/cards").json()]
    assert not any("ommander" in n for n in names)
    assert client.get(f"/api/decks/{slug}/validation").json()["valid"]


def test_import_without_commander_then_toggle(client, db):
    slug = _commander_deck(client)
    client.post(f"/api/decks/{slug}/import", json={"text": "1 Gandalf the White\n" + _filler(99)})
    val = client.get(f"/api/decks/{slug}/validation").json()
    assert not val["valid"] and any("No commander set" in e for e in val["errors"])

    slots = {s["card"]["card_name"]: s for s in client.get(f"/api/decks/{slug}/cards").json()}
    gandalf, other = slots["Gandalf the White"]["id"], slots["Filler Card 0"]["id"]

    r = client.post(f"/api/decks/{slug}/cards/{gandalf}/commander")
    assert r.status_code == 200 and r.json()["is_commander"]
    assert client.get(f"/api/decks/{slug}/validation").json()["valid"]

    # Picking another card moves the crown instead of creating two commanders.
    client.post(f"/api/decks/{slug}/cards/{other}/commander")
    flags = {s["card"]["card_name"]: s["is_commander"] for s in client.get(f"/api/decks/{slug}/cards").json()}
    assert flags["Filler Card 0"] and not flags["Gandalf the White"]
    deck = next(d for d in client.get("/api/decks").json() if d["slug"] == slug)
    assert deck["commander_name"] == "Filler Card 0"

    # Clicking again removes it.
    assert not client.post(f"/api/decks/{slug}/cards/{other}/commander").json()["is_commander"]
    assert not client.get(f"/api/decks/{slug}/validation").json()["valid"]


def test_toggle_rejected_for_standard_deck(client, db):
    slug = client.post("/api/decks", json={"name": "Std", "format": "standard"}).json()["slug"]
    client.post(f"/api/decks/{slug}/import", json={"text": "4 Nasty End"})
    slot = client.get(f"/api/decks/{slug}/cards").json()[0]["id"]
    assert client.post(f"/api/decks/{slug}/cards/{slot}/commander").status_code == 400
    assert db.query(Card).filter(Card.card_name == "Nasty End").count() == 1
