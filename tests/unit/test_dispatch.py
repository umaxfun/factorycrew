"""Dispatcher pieces that need no gateway."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dispatcher"))
import factorio_dispatch as d  # noqa: E402


def test_reply_language_from_the_question_text():
    assert d.reply_language("draw kirocrew ghost logo with colored lamps") == "English"
    assert d.reply_language("Сколько всего жёлтых сундуков?") == "Russian or another Cyrillic language"
    assert d.reply_language("find the storage-chest near (12, -4)") == "English"
    assert d.reply_language("12345 ?!") is None
    assert d.reply_language("où est le dépôt") is None  # Latin with accents: let the model decide


def test_header_says_the_language_and_not_the_run_name():
    msg = d.render({"run_id": "r", "run_name": "Ран 1", "chat": "Build", "tick": 5, "text": "draw a logo",
                    "player": {"name": "p", "surface": "nauvis", "x": 1, "y": 2}})
    assert msg.startswith('[Factorio · run "Ран 1" · tab "Build" · tick 5 · player p at (1, 2) on nauvis · reply in English]')


def test_pointers_go_as_lines_between_header_and_question():
    msg = d.render({"run_id": "r", "run_name": "R", "chat": "Build", "tick": 5, "text": "why is @1 slow? build like @2",
                    "player": {"name": "p", "surface": "nauvis", "x": 1, "y": 2},
                    "pointers": [
                        {"n": 1, "kind": "point", "surface": "nauvis", "x": 12.5, "y": -30.5, "tile": "grass-1",
                         "entity": {"name": "assembling-machine-2", "type": "assembling-machine", "x": 12.5,
                                    "y": -30.5, "recipe": "electronic-circuit", "status": "item_ingredient_shortage"},
                         "resource": "iron-ore"},
                        {"n": 2, "kind": "box", "surface": "nauvis", "area": [10, -40, 40, -20], "w": 30, "h": 20,
                         "count": 42, "counts": [{"name": "transport-belt", "n": 30}, {"name": "train-stop", "n": 1}],
                         "recipes": [{"name": "electronic-circuit", "n": 8}],
                         "resources": [{"name": "iron-ore", "amount": 120000.0}]},
                        {"n": 3, "kind": "point", "surface": "nauvis", "x": 0.0, "y": 3.0, "tile": "sand-1"},
                    ]})
    lines = msg.split("\n")
    assert lines[0].startswith("[Factorio · run")
    assert lines[1] == ("[@1: click at (12.5, -30.5) on nauvis; assembling-machine-2 (assembling-machine) at (12.5, -30.5), "
                        "recipe electronic-circuit, status item_ingredient_shortage, on iron-ore]")
    assert lines[2] == ("[@2: box (10, -40)-(40, -20), 30×20 on nauvis; 42 entities: 30 transport-belt, 1 train-stop; "
                        "recipes: 8 electronic-circuit; ore: iron-ore 120000]")
    assert lines[3] == "[@3: click at (0, 3) on nauvis; empty ground (sand-1)]"
    assert lines[4] == "why is @1 slow? build like @2"


def test_no_pointers_no_extra_lines():
    msg = d.render({"run_id": "r", "chat": "T", "tick": 1, "text": "hi"})
    assert msg.split("\n")[1] == "hi"
