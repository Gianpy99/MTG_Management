import io

import openpyxl
import pytest

from importer import import_file
from models import Card

LTR = "The Lord of the Rings"


@pytest.fixture
def frodo(db):
    card = Card(set_name=LTR, card_name="Frodo Baggins", collector_number="1", edition="ltr",
                rarity="U", notes="keep me", legendary=True, quantity=1)
    db.add(card)
    db.commit()
    return card


def _csv(db, text, name="x.csv", encoding="utf-8"):
    return import_file(db, name, text.encode(encoding))


def test_quantity_only_updates_matched_card(db, frodo):
    r = _csv(db, "Set,Card Name,Quantity\nThe Lord of the Rings,frodo baggins,4\n")
    assert (r.added, r.updated, r.rejected, r.copies_delta) == (0, 1, 0, 3)
    assert db.query(Card).count() == 1
    assert (frodo.quantity, frodo.notes, frodo.rarity, frodo.legendary) == (4, "keep me", "U", True)


def test_add_quantity_increments(db, frodo):
    r = _csv(db, "Set,Card Name,Add Quantity\nThe Lord of the Rings,Frodo Baggins,2\n")
    assert frodo.quantity == 3 and r.copies_delta == 2
    _csv(db, "Set,Card Name,Add Quantity\nThe Lord of the Rings,Frodo Baggins,-5\n")
    assert frodo.quantity == 0


def test_quantity_plus_add_quantity(db, frodo):
    _csv(db, "Set,Card Name,Quantity,Add Quantity\nThe Lord of the Rings,Frodo Baggins,2,3\n")
    assert frodo.quantity == 5


def test_duplicate_rows_sum(db, frodo):
    r = _csv(db, "Set,Card Name,Quantity\n" + "The Lord of the Rings,Frodo Baggins,1\n" * 3)
    assert frodo.quantity == 3
    assert sum("merged" in i for i in r.issues) == 2


def test_blank_cells_do_not_overwrite(db, frodo):
    r = _csv(db, "Set,Card Name,Collector Number,Notes,Rarity,Legendary?\nThe Lord of the Rings,Frodo Baggins,1,,,\n")
    assert r.unchanged == 1
    assert (frodo.notes, frodo.rarity, frodo.legendary) == ("keep me", "U", True)


def test_owned_flag_never_lowers_quantity(db, frodo):
    frodo.quantity = 3
    db.commit()
    _csv(db, "Set,Card Name,Owned?\nThe Lord of the Rings,Frodo Baggins,No\n")
    assert frodo.quantity == 3
    frodo.quantity = 0
    db.commit()
    _csv(db, "Set,Card Name,Owned?\nThe Lord of the Rings,Frodo Baggins,Yes\n")
    assert frodo.quantity == 1


def test_new_card_is_added_with_quantity(db, frodo):
    r = _csv(db, "Set,Card Name,Edition,Add Quantity\nThe Lord of the Rings,Galadriel,ltc,2\n")
    assert r.added == 1
    card = db.query(Card).filter_by(card_name="Galadriel").one()
    assert (card.quantity, card.edition, card.notes) == (2, "ltc", "")


def test_new_card_owned_flag(db):
    _csv(db, "Set,Card Name,Owned?\nThe Hobbit,Bilbo,Yes\nThe Hobbit,Smaug,No\n")
    assert {c.card_name: c.quantity for c in db.query(Card)} == {"Bilbo": 1, "Smaug": 0}


def test_ambiguous_printings_rejected_then_resolved(db, frodo):
    db.add(Card(set_name=LTR, card_name="Frodo Baggins", collector_number="99", edition="ltc", quantity=0))
    db.commit()
    r = _csv(db, "Set,Card Name,Quantity\nThe Lord of the Rings,Frodo Baggins,4\n")
    assert r.rejected == 1 and "printings" in r.issues[0]
    assert frodo.quantity == 1

    _csv(db, "Set,Card Name,Collector Number,Quantity\nThe Lord of the Rings,Frodo Baggins,1,4\n")
    assert frodo.quantity == 4
    _csv(db, "Set,Card Name,Edition,Quantity\nThe Lord of the Rings,Frodo Baggins,LTC,2\n")
    ltc = db.query(Card).filter_by(collector_number="99").one()
    assert (frodo.quantity, ltc.quantity) == (4, 2)


def test_other_edition_creates_new_printing(db, frodo):
    r = _csv(db, "Set,Card Name,Edition,Quantity\nThe Lord of the Rings,Frodo Baggins,ltc,1\n")
    assert r.added == 1
    assert frodo.edition == "ltr" and frodo.quantity == 1


def test_collector_number_fills_blank_card(db):
    card = Card(set_name=LTR, card_name="Sam", collector_number="", quantity=1)
    db.add(card)
    db.commit()
    r = _csv(db, "Set,Card Name,Collector Number,Quantity\nThe Lord of the Rings,Sam,7,2\n")
    assert r.added == 0 and (card.collector_number, card.quantity) == ("7", 2)


def test_invalid_quantity_rejected(db, frodo):
    r = _csv(db, "Set,Card Name,Quantity\nThe Lord of the Rings,Frodo Baggins,lots\n")
    assert r.rejected == 1 and frodo.quantity == 1


def test_excel_semicolon_cp1252_csv(db):
    db.add(Card(set_name=LTR, card_name="Éowyn, Fearless Knight", collector_number="201", quantity=0))
    db.commit()
    r = _csv(db, 'Set;Card Name;Quantity\r\nThe Lord of the Rings;"Éowyn, Fearless Knight";2\r\n',
             encoding="cp1252")
    assert (r.updated, r.added) == (1, 0)
    assert db.query(Card).one().quantity == 2


def test_xlsx_import(db, frodo):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Collection"
    ws.append(["Set", "Card Name", "Collector Number", "Quantity"])
    ws.append([LTR, "Frodo Baggins", 1, 3])
    buf = io.BytesIO()
    wb.save(buf)
    r = import_file(db, "edit.xlsx", buf.getvalue())
    assert r.updated == 1 and frodo.quantity == 3


def test_export_roundtrip_is_unchanged(db, frodo):
    from fastapi.testclient import TestClient

    import main

    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        for fmt in ("csv", "xlsx"):
            data = client.get(f"/api/export/collection.{fmt}").content
            r = import_file(db, f"collection.{fmt}", data)
            assert (r.added, r.updated, r.unchanged, r.copies_delta) == (0, 0, 1, 0), fmt
    finally:
        main.app.dependency_overrides.clear()
