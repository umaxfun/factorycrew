"""kc.bp: blueprint strings (codec, contents, transforms, ghosts, check). Pure Python, no game."""
from __future__ import annotations

import base64
import json
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import bp as blueprints  # noqa: E402
from kc.grid import Grid  # noqa: E402

EX = ROOT / "tests" / "data" / "blueprints"
N, E, S, W = 0, 4, 8, 12


def ex(name):
    return (EX / name).read_text()


def norm(items):
    return sorted((g["name"], g["x"], g["y"], g["direction"], g.get("type")) for g in items)


REAL_2X = ["accumulators-2.0.txt", "scattershot-2.0.txt", "circuit-connections-2.1.txt", "red-circuits-block.txt"]


@pytest.mark.parametrize("name", REAL_2X + ["blueprint-book.txt", "decon-planner.txt", "upgrade-planner.txt"])
def test_real_strings_round_trip(name):
    d = blueprints.decode(ex(name))
    s = blueprints.encode(d)
    assert s[0] == "0" and blueprints.decode(s) == d
    # our string is what the codec says: version byte, base64, zlib, compact JSON
    assert json.loads(zlib.decompress(base64.b64decode(s[1:]))) == d
    # line breaks and spaces in a pasted string are ignored, raw JSON is accepted too
    assert blueprints.decode(s[:40] + "\n  " + s[40:]) == d
    assert blueprints.decode(json.dumps(d)) == d


@pytest.mark.parametrize("name", REAL_2X)
def test_real_2x_strings_are_clean_except_known_notes(name):
    probs = blueprints.check(blueprints.decode(ex(name)))
    if name == "scattershot-2.0.txt":
        assert probs == ["blueprint: snap-to-grid {'x': 104, 'y': 62}; to_ghosts centres it like a free blueprint"]
    else:
        assert probs == []


def test_versions_and_summary():
    assert blueprints.version_string(281479278886912) == "1.1.110"  # upstream's own test value
    assert blueprints.version_number("2.0.11.3") == (2 << 48) | (11 << 16) | 3
    assert blueprints.version_string(blueprints.version_number((2, 0, 77))) == "2.0.77"
    s = blueprints.summary(blueprints.decode(ex("red-circuits-block.txt")))
    assert s["version"] == "2.0.11.3" and s["label"] == "Red Circuits Block" and s["entities"] == 254
    assert s["counts"]["transport-belt"] == 172 and s["counts"]["assembling-machine-2"] == 14
    assert s["recipes"]["advanced-circuit"] == 12 and s["wires"] == 13 and s["size"] == [32, 15]
    a = blueprints.summary(blueprints.decode(ex("accumulators-2.0.txt")))
    assert a["quality"] == {"legendary": 2} and a["size"] == [6, 2] and a["version"] == "2.0.47"


def test_books_and_planners():
    book = blueprints.decode(ex("blueprint-book.txt"))
    s = blueprints.summary(book)
    assert s["type"] == "blueprint_book" and s["label"] == "My Book" and s["version"] == "0.17.9.1"
    assert s["entries"][0]["label"] == "Science. It works, Bitches" and s["entries"][0]["counts"] == {"assembling-machine-2": 1}
    entries = blueprints.book_entries(book)
    assert [(e["path"], e["type"], e["entities"]) for e in entries] == [([0], "blueprint", 1)]
    # nested books are walked, with the path from the outer book
    outer = {"blueprint_book": {"item": "blueprint-book", "blueprints": [dict(book, index=3), dict(blueprints.decode(ex("decon-planner.txt")), index=5)]}}
    paths = [(e["path"], e["type"]) for e in blueprints.book_entries(outer)]
    assert paths == [([3], "blueprint_book"), ([3, 0], "blueprint"), ([5], "deconstruction_planner")]
    up = blueprints.summary(blueprints.decode(ex("upgrade-planner.txt")))
    assert up == {"type": "upgrade_planner", "label": "Furnaces", "version": "1.1.110", "settings": {"mappers": 1}}
    dp = blueprints.summary(blueprints.decode(ex("decon-planner.txt")))
    assert dp["settings"] == {"entity_filters": 1, "tile_selection_mode": 2}
    assert blueprints.check(blueprints.decode(ex("upgrade-planner.txt"))) == []
    with pytest.raises(ValueError):
        blueprints.to_ghosts(blueprints.decode(ex("upgrade-planner.txt")), (0, 0))


PAIR = [{"name": "transport-belt", "x": 0.5, "y": 0.5, "direction": E},
        {"name": "transport-belt", "x": 1.5, "y": 0.5, "direction": E}]


def test_belt_pair_rotated_four_times_returns_to_start():
    bp = blueprints.from_entities(PAIR)
    start = blueprints.to_ghosts(bp, (10.5, 10.5))
    assert norm(start) == [("transport-belt", 10.5, 10.5, E, None), ("transport-belt", 11.5, 10.5, E, None)]
    cur, seen = bp, []
    for _ in range(4):  # rotate the print itself a quarter turn, four times
        cur = blueprints.from_entities(blueprints.transform(cur, E))
        seen.append(norm(blueprints.to_ghosts(cur, (10.5, 10.5))))
    assert seen[-1] == norm(start)
    assert seen[0] == [("transport-belt", 10.5, 10.5, S, None), ("transport-belt", 10.5, 11.5, S, None)]
    assert [x[3] for x in seen[1]] == [W, W] and [x[3] for x in seen[2]] == [N, N]
    # rotating at build time gives the same as rotating the print
    for k, d in enumerate((E, S, W, N)):
        assert norm(blueprints.to_ghosts(bp, (10.5, 10.5), d)) == seen[k]


def test_rotation_positions_and_centering():
    bp = blueprints.from_entities(PAIR)
    # 2x1, even width: the centre goes on the nearest tile corner, odd height on a tile centre
    assert norm(blueprints.to_ghosts(bp, (10.3, 10.7))) == [("transport-belt", 9.5, 10.5, E, None), ("transport-belt", 10.5, 10.5, E, None)]
    assert norm(blueprints.to_ghosts(bp, (10.6, 10.2))) == [("transport-belt", 10.5, 10.5, E, None), ("transport-belt", 11.5, 10.5, E, None)]
    # rotated east -> 1x2: x on a tile centre, y on a corner; the first belt (upstream) ends up on top
    g = blueprints.to_ghosts(bp, (10.3, 10.7), direction="east")
    assert norm(g) == [("transport-belt", 10.5, 10.5, S, None), ("transport-belt", 10.5, 11.5, S, None)]
    assert [x["y"] for x in g] == [10.5, 11.5]
    # half turn: order along the line reverses with the direction (2 wide: centre on corner x = 1)
    g = blueprints.to_ghosts(bp, (0.5, 0.5), direction=S)
    assert [(x["x"], x["direction"]) for x in g] == [(1.5, W), (0.5, W)]


def test_rotation_keeps_footprint_alignment():
    """A 3x3 sits on tile centres, a 2x2 and a turned splitter on corners, in every direction."""
    items = [{"name": "assembling-machine-1", "x": 1.5, "y": 1.5},
             {"name": "accumulator", "x": 4.0, "y": 1.0},
             {"name": "splitter", "x": 6.5, "y": 1.0, "direction": E}]
    bp = blueprints.from_entities(items)
    for d in (N, E, S, W):
        for anchor in ((0.2, 0.9), (7.0, -3.0), (-2.5, 4.5)):
            g = {x["name"]: x for x in blueprints.to_ghosts(bp, anchor, d)}
            a, c, sp = g["assembling-machine-1"], g["accumulator"], g["splitter"]
            assert a["x"] % 1 == 0.5 and a["y"] % 1 == 0.5, (d, anchor, a)
            assert c["x"] % 1 == 0 and c["y"] % 1 == 0, (d, anchor, c)
            if sp["direction"] in (E, W):
                assert sp["x"] % 1 == 0.5 and sp["y"] % 1 == 0, (d, sp)
            else:
                assert sp["x"] % 1 == 0 and sp["y"] % 1 == 0.5, (d, sp)
            assert sp["direction"] == (E + d) % 16


def test_mirror():
    items = [{"name": "inserter", "x": 0.5, "y": 0.5, "direction": E},
             {"name": "transport-belt", "x": 2.5, "y": 0.5, "direction": N},
             {"name": "underground-belt", "x": 3.5, "y": 0.5, "direction": E, "type": "input"},
             {"name": "splitter", "x": 4.5, "y": 2.0, "direction": E, "extra": {"input_priority": "left", "output_priority": "right"}},
             {"name": "chemical-plant", "x": 1.5, "y": 3.5, "extra": {"recipe": "plastic-bar"}}]
    bp = blueprints.from_entities(items)
    plain = {g["name"]: g for g in blueprints.to_ghosts(bp, (0, 0))}
    h = {g["name"]: g for g in blueprints.to_ghosts(bp, (0, 0), mirror=True)}
    # horizontal flip: east <-> west, north stays, x order reverses, y unchanged
    assert h["inserter"]["direction"] == W and h["transport-belt"]["direction"] == N
    assert h["inserter"]["x"] > h["transport-belt"]["x"] and plain["inserter"]["x"] < plain["transport-belt"]["x"]
    assert h["inserter"]["y"] == plain["inserter"]["y"]
    assert h["underground-belt"]["type"] == "input" and h["underground-belt"]["direction"] == W
    assert h["splitter"]["extra"]["input_priority"] == "right" and h["splitter"]["extra"]["output_priority"] == "left"
    assert h["chemical-plant"]["extra"]["mirror"] is True and h["chemical-plant"]["extra"]["recipe"] == "plastic-bar"
    assert "mirror" not in plain["chemical-plant"]["extra"]
    v = {g["name"]: g for g in blueprints.to_ghosts(bp, (0, 0), mirror="vertical")}
    assert v["transport-belt"]["direction"] == S and v["inserter"]["direction"] == E
    assert v["inserter"]["y"] < v["chemical-plant"]["y"] if plain["inserter"]["y"] > plain["chemical-plant"]["y"] else v["inserter"]["y"] > v["chemical-plant"]["y"]
    # flipping twice gives the original back; H then V equals a half turn
    twice = blueprints.from_entities(blueprints.transform(blueprints.from_entities(blueprints.transform(bp, 0, True)), 0, True))
    assert norm(blueprints.to_ghosts(twice, (5, 5))) == norm(blueprints.to_ghosts(bp, (5, 5)))
    hv = blueprints.from_entities(blueprints.transform(blueprints.from_entities(blueprints.transform(bp, 0, "h")), 0, "v"))
    assert norm(blueprints.to_ghosts(hv, (5, 5))) == norm(blueprints.to_ghosts(bp, (5, 5), S))
    with pytest.raises(ValueError):
        blueprints.to_ghosts(blueprints.from_entities([{"name": "curved-rail-a", "x": 1, "y": 1}]), (0, 0), mirror=True)


def test_entities_that_cannot_turn_keep_their_direction():
    """build_blueprint leaves poles, chests, lamps at direction 0 (checked in the 2.1 stand)."""
    bp = blueprints.from_entities([{"name": "medium-electric-pole", "x": 0.5, "y": 0.5},
                                   {"name": "inserter", "x": 1.5, "y": 0.5},
                                   {"name": "nullius-pole", "x": 2.5, "y": 0.5}])
    for d in (E, S, W):
        for m in (False, "horizontal", "vertical"):
            g = {x["name"]: x["direction"] for x in blueprints.to_ghosts(bp, (0, 0), d, m)}
            assert g["medium-electric-pole"] == 0, (d, m)
            assert g["inserter"] == ((8 if m == "vertical" else 0) + d) % 16, (d, m)
            g2 = {x["name"]: x["direction"] for x in blueprints.to_ghosts(bp, (0, 0), d, m, fixed={"nullius-pole"})}
            # an explicit set replaces the vanilla default: now the vanilla pole turns like the inserter
            assert g2["nullius-pole"] == 0 and g2["medium-electric-pole"] == g["inserter"], (d, m, g2)


def test_to_ghosts_keeps_settings_and_16_way_directions():
    d = blueprints.decode(ex("red-circuits-block.txt"))
    g = blueprints.to_ghosts(d, (0, 0))
    assert len(g) == 254
    ug = [x for x in g if x["name"] == "underground-belt"]
    assert {x["type"] for x in ug} == {"input", "output"}
    asm = [x for x in g if x["name"] == "assembling-machine-2"]
    assert all(x["extra"].get("recipe") for x in asm)
    assert {x["extra"]["entity_number"] for x in g} == set(range(1, 255))
    assert all(set(x) <= {"name", "x", "y", "direction", "type", "extra"} for x in g)
    # odd 16-way values (rails, 2.0 diagonals) rotate by +4 like the cardinal ones
    bp = blueprints.from_entities([{"name": "rail-signal", "x": 0.5, "y": 0.5, "direction": 3}])
    assert blueprints.to_ghosts(bp, (0.5, 0.5), W)[0]["direction"] == 15
    with pytest.raises(ValueError):
        blueprints.to_ghosts(bp, (0, 0), direction=2)
    # lamp colour / always_on and quality ride in extra
    lamp = blueprints.from_entities([{"name": "small-lamp", "x": 0.5, "y": 0.5,
                                      "extra": {"color": {"r": 1, "g": 0, "b": 0}, "always_on": True, "quality": "rare"}}])
    x = blueprints.to_ghosts(lamp, (3.2, 3.2))[0]
    assert (x["x"], x["y"]) == (3.5, 3.5) and x["extra"]["always_on"] and x["extra"]["quality"] == "rare"


def test_1x_directions_are_doubled():
    old = {"blueprint": {"item": "blueprint", "version": blueprints.version_number("1.1.110"),
                         "entities": [{"entity_number": 1, "name": "transport-belt", "position": {"x": 0.5, "y": 0.5}, "direction": 2}]}}
    assert blueprints.to_ghosts(old, (0.5, 0.5))[0]["direction"] == E
    assert any("1.1.110" in p for p in blueprints.check(old))


def test_from_entities_round_trip_and_wires():
    items = [{"name": "small-lamp", "x": 100.5, "y": -50.5, "extra": {"always_on": True}},
             {"name": "wooden-chest", "x": 103.5, "y": -50.5},
             {"name": "underground-belt", "x": 104.5, "y": -48.5, "direction": W, "type": "output"}]
    bp = blueprints.from_entities(items, label="t", wires=[[1, 1, 2, 1]])
    ents = bp["blueprint"]["entities"]
    assert [e["entity_number"] for e in ents] == [1, 2, 3] and ents[0]["position"] == {"x": 0.5, "y": 1.5}
    assert ents[0]["always_on"] is True and ents[2]["type"] == "output" and ents[2]["direction"] == W
    assert "direction" not in ents[0]
    assert blueprints.check(bp) == [] and blueprints.wires(bp) == [[1, 1, 2, 1]]
    back = blueprints.decode(blueprints.encode(bp))
    assert back == bp and blueprints.summary(back)["version"] == "2.0.77"
    # placing it back where it came from gives the same items (extra adds entity_number)
    g = blueprints.to_ghosts(back, (102.5, -49.5))
    assert norm(g) == norm([dict(i, direction=i.get("direction", 0)) for i in items])
    # a bare body is wrapped
    assert blueprints.kind(blueprints.decode(blueprints.encode({"entities": ents}))) == "blueprint"


def test_check_reports_unknown_structure():
    bp = blueprints.from_entities(PAIR + [{"name": "constant-combinator", "x": 5.5, "y": 0.5}], wires=[[1, 1, 9, 1], [1, 7, 2, 1]])
    body = bp["blueprint"]
    body["entities"][1]["entity_number"] = 1
    body["entities"][0]["direction"] = 17
    body["entities"][2]["sparkle"] = 1
    body["entities"][2]["control_behavior"] = {"sections": {"sections": [{"filters": [{"name": "parameter-0"}]}]}}
    body["weird"] = True
    bp["extra_top"] = 1
    probs = blueprints.check(bp)
    for want in ("unknown top-level keys ['extra_top']", "unknown blueprint keys ['weird']", "entity_number 1 is used twice",
                 "direction 17", "unknown keys ['sparkle']", "missing entity 9", "connector 7", "parameterised"):
        assert any(want in p for p in probs), (want, probs)
    assert blueprints.check({"foo": 1})[0].startswith("blueprint: no blueprint")
    assert any("does not exist in this save" in p for p in blueprints.check(blueprints.from_entities(PAIR), known={"nullius-belt"}))
    old = {"blueprint": {"item": "blueprint", "entities": [{"entity_number": 1, "name": "small-electric-pole",
                                                            "position": {"x": 0.5, "y": 0.5}, "neighbours": [2]}]}}
    assert any("1.x wire field" in p for p in blueprints.check(old))
    with pytest.raises(ValueError):
        blueprints.decode("1abc")


def test_deterministic():
    d = blueprints.decode(ex("red-circuits-block.txt"))
    assert blueprints.encode(d) == blueprints.encode(blueprints.decode(ex("red-circuits-block.txt")))
    assert blueprints.to_ghosts(d, (3.3, 4.4), E, True) == blueprints.to_ghosts(d, (3.3, 4.4), E, True)


def test_package_surface_and_geo_conventions():
    assert kc.bp is blueprints
    bp = blueprints.from_entities(PAIR)
    # words, 16-way numbers and kc.geo direction names all work as the build direction
    assert norm(blueprints.to_ghosts(bp, (0, 0), "e")) == norm(blueprints.to_ghosts(bp, (0, 0), 4))
    assert norm(blueprints.to_ghosts(bp, (0, 0), 20)) == norm(blueprints.to_ghosts(bp, (0, 0), kc.E))
    with pytest.raises(ValueError):
        blueprints.to_ghosts(bp, (0, 0), "northeast")  # 2: not a quarter turn
    # anchors in every kc point form
    want = norm(blueprints.to_ghosts(bp, (3.2, 4.7)))
    for a in ([3.2, 4.7], {"x": 3.2, "y": 4.7}, {"position": {"x": 3.2, "y": 4.7}}):
        assert norm(blueprints.to_ghosts(bp, a)) == want
    # every ghost of a real print sits where kc.geo.snap puts an entity of its size
    d = blueprints.decode(ex("red-circuits-block.txt"))
    for anchor in ((0.3, 0.6), (5.0, -2.0)):
        for g in blueprints.to_ghosts(d, anchor, kc.S):
            w, h = blueprints.VANILLA_SIZES.get(g["name"], (1, 1))
            assert kc.snap(g, w, h, g["direction"]) == (g["x"], g["y"]), g
    # outputs are plain JSON
    assert json.loads(json.dumps(blueprints.to_ghosts(d, (0, 0)))) == blueprints.to_ghosts(d, (0, 0))


def test_sized_and_collisions_with_grid():
    items = [{"name": "assembling-machine-1", "x": 1.5, "y": 1.5},
             {"name": "splitter", "x": 4.5, "y": 1.0, "direction": E}]
    g = blueprints.to_ghosts(blueprints.from_entities(items), (10.5, 10.5))
    s = blueprints.sized(g)
    assert [(x["w"], x["h"]) for x in s] == [(3, 3), (2, 1)] and "w" not in g[0]
    assert blueprints.collisions(g, Grid()) == []
    asm = g[0]
    assert blueprints.collisions(g, Grid(blocked=[(asm["x"] + 1, asm["y"] - 1)])) == [0]
    # the turned splitter covers 1x2 tiles: block the lower one
    sp = g[1]
    assert blueprints.collisions(g, Grid(blocked=[(sp["x"], sp["y"] + 0.5)])) == [1]
    assert blueprints.collisions(g, Grid(blocked=[(sp["x"] + 1, sp["y"] + 0.5)])) == []
    # overlapping items inside one list are caught too; the grid itself is not changed
    grid = Grid()
    assert blueprints.collisions(g + [dict(g[0])], grid) == [2] and grid.blocked_tiles() == set()
    # sizes from prototypes win over the vanilla table (a modded 5x5 named like a vanilla 3x3)
    assert blueprints.sized(g[:1], {"assembling-machine-1": {"tile_width": 5, "tile_height": 5}})[0]["w"] == 5
