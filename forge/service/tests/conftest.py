import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge_engine import config  # noqa: E402

HERE = Path(__file__).resolve().parent

CARDS = {
    "gandalf_goblins_bane.txt": "Name:Gandalf, Goblins' Bane\nManaCost:2 R\nAlternateMode:Adventure\n\nALTERNATE\n\nName:Flameshape\n",
    "fire_ice.txt": "Name:Fire\nAlternateMode:Split\n\nALTERNATE\n\nName:Ice\n",
    "nasty_end.txt": "Name:Nasty End\n",
    "gloin.txt": "Name:Gl\u00f3in the Mighty\n",
    "mountain.txt": "Name:Mountain\n",
    "frodo.txt": "Name:Frodo, Adventurous Hobbit\n",
}
EDITIONS = {
    "hobbit.txt": "[metadata]\nCode=HOB\nAlias=HBT\n[cards]\n96 M Gandalf, Goblins' Bane @Artist\n"
                  "99 R Gl\u00f3in the Mighty @A\n[tokens]\n1 goblin_r_1_1 @x\n",
    "ltr.txt": "[metadata]\nCode=LTR\n[cards]\n99 C Nasty End @Artist\n271 L Mountain @A\n"
               "[Common]\nBase=Common:fromSheet(\"LTR cards\")\n",
    "ltc.txt": "[metadata]\nCode=LTC\n[cards]\n2 M Frodo, Adventurous Hobbit @A\n",
    "dmr.txt": "[metadata]\nCode=DMR\n[cards]\n1 U Fire // Ice @A\n",
}


def make_constructed(name: str = "Deck_A", extra: str = "") -> str:
    return f"[metadata]\nName={name}\n[Main]\n4 Nasty End|LTR|[99]\n56 Mountain|LTR|[271]\n{extra}"


@pytest.fixture
def forge_home(tmp_path, monkeypatch):
    home = tmp_path / "forge"
    (home / "res" / "cardsfolder").mkdir(parents=True)
    (home / "res" / "editions").mkdir(parents=True)
    for name, text in CARDS.items():
        (home / "res" / "cardsfolder" / name).write_text(text, encoding="utf-8")
    for name, text in EDITIONS.items():
        (home / "res" / "editions" / name).write_text(text, encoding="utf-8")
    (home / "forge.jar").write_bytes(b"fake")
    decks = tmp_path / "test_decks"
    decks.mkdir()
    (decks / "Deck_A.dck").write_text(make_constructed("Deck_A"), encoding="utf-8")
    (decks / "Deck_B.dck").write_text(make_constructed("Deck_B"), encoding="utf-8")
    monkeypatch.setattr(config, "FORGE_HOME", home)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "TEST_DECKS_DIR", decks)
    monkeypatch.setattr(config, "JAVA_BIN", sys.executable)
    monkeypatch.setattr(config, "JAVA_OPTS", [str(HERE / "fake_forge.py")])
    monkeypatch.setattr(config, "FORGE_VERSION", "9.9.9")
    return home
