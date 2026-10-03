from forge_engine.cards import CardIndex, normalise, parse_dck, structure_issues
from forge_engine.logparse import ForgeOutput


def load(forge_home):
    return CardIndex.load(forge_home / "res")


def test_index_names_and_editions(forge_home):
    idx = load(forge_home)
    assert idx.resolve("Gandalf, Goblins' Bane // Flameshape") == "Gandalf, Goblins' Bane"
    assert idx.resolve("Fire // Ice") == "Fire // Ice"
    assert idx.resolve("Gloin the Mighty") == "Gl\u00f3in the Mighty"
    assert idx.resolve("Totally Fake Card") is None
    assert idx.set_code("hbt") == "HOB"
    assert "1" not in idx.editions["HOB"]  # tokens are not cards
    assert idx.editions["LTR"]["99"] == "nasty end"


def test_normalise_fixes_names_sets_and_collectors(forge_home):
    deck = parse_dck(
        "[metadata]\nName=My Deck!\n[Main]\n"
        "1 Gandalf, Goblins' Bane // Flameshape|HOB|[96]\n"
        "1 Nasty End|ZZZ|[99]\n"
        "1 Nasty End|LTR|[12]\n"
        "1 Nasty End|HOB|[99]\n"
        "1 Totally Fake Card|LTR|[1]\n"
        "57 Mountain|LTR|[271]\n"
    )
    text, issues = normalise(deck, load(forge_home))
    assert text.splitlines()[:4] == ["[metadata]", "Name=My_Deck", "[Main]", "1 Gandalf, Goblins' Bane|HOB|[96]"]
    assert "1 Nasty End\n" in text  # unknown set dropped
    assert "1 Nasty End|LTR\n" in text  # wrong collector dropped, set kept
    types = sorted((i.severity, i.issue_type) for i in issues)
    assert ("RED", "unsupported_card") in types
    assert ("INFO", "name_normalised") in types
    assert ("INFO", "set_unknown") in types
    assert ("INFO", "collector_mismatch") in types
    assert ("INFO", "card_not_in_set") in types
    assert not any(i.severity == "RED" and i.card_name.startswith("Gandalf") for i in issues)


def test_structure_rules():
    cmd = parse_dck("[Commander]\n1 Frodo, Adventurous Hobbit|LTC|[2]\n[Main]\n98 Mountain\nbad line\n")
    kinds = {i.issue_type for i in structure_issues(cmd, "commander")}
    assert kinds == {"malformed_line", "deck_size"}
    ok = parse_dck("[Commander]\n1 Frodo, Adventurous Hobbit\n[Main]\n99 Mountain\n")
    assert structure_issues(ok, "commander") == []
    small = parse_dck("[Main]\n40 Mountain\n")
    assert [i.issue_type for i in structure_issues(small, "constructed")] == ["deck_size"]


def test_log_parser_real_lines():
    out = ForgeOutput()
    lines = [
        "14:20:35 [INFO ] GuiBase: APP: Forge v.2.0.15-SNAPSHOT-09.28",
        'An unsupported card was requested: "Gandalf, Goblins\' Bane // Flameshape" from "HOB". ',
        'An unsupported card was requested: "Gandalf, Goblins\' Bane // Flameshape" from "[N.A.]". ',
        "Warning: default (ie. inherited from base class) implementation of chooseSingleCard is used by "
        "Gollum's Bite for forge.ai.ability.AlwaysPlayAi. Consider declaring an overloaded method",
        "No AI assigned for API: Recruit",
        "Game Outcome: Turn 13",
        "Game Result: Game 1 ended in 11379 ms. Ai(2)-Rakdos_Orcs has won!",
        "Stopping slow match as draw",
        "Game Result: Game 2 ended in a Draw! Took 120000 ms.",
        "Could not load deck - x.dck, match cannot start",
    ]
    games = [g for g in (out.feed(ln) for ln in lines) if g]
    assert games[0] == {"game_number": 1, "winner_side": "b", "winner_name": "Ai(2)-Rakdos_Orcs",
                        "duration_ms": 11379, "turns": 13, "draw_reason": ""}
    assert games[1]["winner_side"] is None and games[1]["draw_reason"] == "clock"
    assert out.unsupported == {("Gandalf, Goblins' Bane // Flameshape", "HOB")}
    assert out.ai_warnings == {"Gollum's Bite": {"chooseSingleCard"}}
    assert out.no_ai == {"Recruit"}
    assert out.forge_build == "2.0.15-SNAPSHOT-09.28"
    assert out.load_failures == ["x.dck"]
