"""kc.atlas on the fixture dump (a player's save, docs/atlas.md "Fixture").

The dump comes from `scripts/atlas-fixture.sh dump`; without it these tests skip.
Coordinates below are places on that save.
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
from kc import atlas as A  # noqa: E402

DUMP = Path(os.environ.get("FC_ATLAS_OUT", ROOT / ".test-data" / "atlas" / "dump.txt"))
TETRA = "nullius-titanium-tetrachloride"


@pytest.fixture(scope="module")
def at():
    if not DUMP.exists():
        pytest.skip(f"no fixture dump at {DUMP}: run scripts/atlas-fixture.sh dump (docs/atlas.md, Fixture)")
    t = time.monotonic()
    a = A.Atlas(DUMP.read_text())
    a.build_seconds = time.monotonic() - t
    return a


def test_parse_counts_match_the_footer(at):
    d = A.parse(DUMP.read_text())
    assert d["footer"]["entities"] == len(d["entities"]) > 90000
    assert TETRA in d["recipes"]


def test_parse_refuses_a_cut_dump():
    text = "H|1|nauvis|1|300\nN|1|iron-chest|container|1|1\nE|1|1|1|0\nZ|2|1|1"
    with pytest.raises(ValueError, match="incomplete"):
        A.parse(text)


def test_whole_block_from_one_furnace_and_from_the_opposite_corner(at):
    one = at.blocks_in_box(-358.5, -124, -356, -121)
    other = at.blocks_in_box(-375, -136, -372, -133)
    assert one == other and len(one) == 1
    b = at.blocks[one[0]]
    assert b["recipe"] == TETRA and len(b["machines"]) == 32
    x0, y0, x1, y1 = b["bbox"]
    assert (round(x0), round(y0), round(x1), round(y1)) == (-375, -136, -348, -112)


def test_tetrachloride_inputs_come_from_two_stops_and_a_pipe(at):
    bi = at.block_at(-357.5, -122.5)
    node = ("B", bi)
    graphite = at.sources(node, "nullius-graphite")
    rutile = at.sources(node, "nullius-rutile")
    chlorine = at.sources(node, "nullius-chlorine")
    assert len(graphite) == 1 and next(iter(graphite)).startswith("S")
    assert len(rutile) == 1 and next(iter(rutile)).startswith("S")
    g_stop = at.stops[int(next(iter(graphite))[1:])]
    r_stop = at.stops[int(next(iter(rutile))[1:])]
    assert "nullius-graphite" in g_stop["s"] and "nullius-rutile" in r_stop["s"]
    assert chlorine and all(k == ["pipe"] for k in chlorine.values())
    assert all(n.startswith("B") for n in chlorine)


def test_fluids_only_by_pipe_items_never_by_pipe(at):
    for (a, b), items in at.edges.items():
        for it, kinds in items.items():
            if at.kind.get(it) == "fluid":
                assert "pipe" in kinds, (a, b, it, kinds)
            else:
                assert kinds - {"pipe"}, (a, b, it, kinds)


def test_shared_belt_does_not_join_unrelated_blocks(at):
    for (a, b), items in at.edges.items():
        if a[0] == "B" and b[0] == "B":
            assert items and set(items) <= at.blocks[a[1]]["pro"] & at.blocks[b[1]]["ing"]


def test_underground_reach_comes_from_the_prototype(at):
    assert at.ug["express-underground-belt"] == 15


def test_build_time(at):
    assert at.build_seconds < 20


def test_zone_view_names_the_block_and_its_sources(at):
    bi = at.block_at(-357.5, -122.5)
    v = at.zone_view(at.zone_of[bi], status={m["id"]: "item_ingredient_shortage" for m in at.blocks[bi]["machines"]})
    assert v["machines"] == 32 and v["blocks"] == 1
    assert v["status"] == {"item_ingredient_shortage": 32}
    srcs = {it: [s["from"] for s in v["inputs"][it]] for it in v["inputs"]}
    assert set(srcs) == {"nullius-graphite", "nullius-rutile", "nullius-chlorine"}
    assert srcs["nullius-rutile"][0].startswith("S") and "rutile" in v["inputs"]["nullius-rutile"][0]["stop"]
    assert v["no_source"] == []


def test_block_view_rates_at_full_speed(at):
    v = at.block_view(at.block_at(-357.5, -122.5))
    assert v["need_per_s"] == {"nullius-graphite": 44.8, "nullius-rutile": 25.6, "nullius-chlorine": 512.0}
    assert v["machines"] == 32 and v["machine"] == ["nullius-medium-furnace-2"]


def test_mcp_atlas_tool_on_a_fake_bridge(at, monkeypatch):
    sys.path.insert(0, str(ROOT / "mcp"))
    import factorio_mcp as m
    text = DUMP.read_text()
    calls = []

    def post(path, body, timeout=30.0):
        calls.append(path)
        if path == "/atlas":
            return {"ok": True, "text": text, "data": {"parts": 1}}
        if path == "/query":
            return {"ok": True, "data": ["item_ingredient_shortage"] * text.count("\n")}
        return {"ok": True}
    monkeypatch.setattr(m, "bridge_post", post)
    monkeypatch.setattr(m, "bridge_health", lambda: {"run_id": "r1", "tick": at.tick + 600})
    m.ATLAS_CACHE.clear()
    v = m.call_tool("factorio_atlas", {"box": {"x0": -358.5, "y0": -124, "x1": -356, "y1": -121}})
    assert v["ok"] and v["machines"] == 32, v
    assert v["block_list"][0]["selected"] is True and v["structure_age_s"] == 10
    v2 = m.call_tool("factorio_atlas", {"level": "block", "at": {"x": -357.5, "y": -122.5}})
    assert v2["ok"] and v2["need_per_s"]["nullius-rutile"] == 25.6
    assert calls.count("/atlas") == 1  # cached between calls
    v3 = m.call_tool("factorio_atlas", {"at": {"x": 0.5, "y": 0.5}})
    assert not v3["ok"] and v3["error"] == "not_found"


def test_the_dump_never_builds_json():
    """helpers.table_to_json of a whole base costs seconds in the game (docs/atlas.md, Reading the base)."""
    src = (ROOT / "mod" / "kc-companion" / "scripts" / "atlas.lua").read_text()
    code = "\n".join(line.split("--")[0] for line in src.splitlines())
    assert "table_to_json" not in code


def test_dump_matches_a_reference_read_of_the_same_save(at):
    """FC_ATLAS_REFERENCE: a JSON read of the same save made without the mod (tools/atlas_proto/dump.lua).
    Same names, positions and directions; positions are rounded to half tiles, so the few script
    entities mods place off the grid (474 turbine connectors on the fixture) only match after rounding."""
    import collections
    import json
    ref_path = os.environ.get("FC_ATLAS_REFERENCE")
    if not ref_path or not Path(ref_path).exists():
        pytest.skip("FC_ATLAS_REFERENCE not set")
    ref = json.loads(Path(ref_path).read_text())
    protos = ref["protos"]
    skip = {"character", "entity-ghost", "tile-ghost", "cargo-wagon", "fluid-wagon", "locomotive",
            "artillery-wagon", "car", "spider-vehicle", "construction-robot", "logistic-robot",
            "combat-robot", "item-entity", "item-request-proxy", "deconstructible-tile-proxy", "projectile"}
    half = lambda v: round(v * 2) / 2  # noqa: E731
    want = collections.Counter((e[0], half(e[1]), half(e[2]), e[3]) for e in ref["entities"]
                               if protos[e[0]]["type"] not in skip)
    got = collections.Counter((e["name"], e["x"], e["y"], e["dir"]) for e in at.ents)
    assert want == got


def test_belt_lanes_limit_the_tetrachloride_block(at):
    """Graphite and rutile are side-loaded from both sides onto one express belt at (-337.5, -105.5):
    each gets one lane, 22.5/s, while the block needs 44.8 graphite and 25.6 rutile."""
    v = at.block_view(at.block_at(-357.5, -122.5))
    s = v["belt_supply"]
    g, r = s["nullius-graphite"], s["nullius-rutile"]
    assert (g["lanes"], g["per_s"], g["one_lane_from"]) == (1, 22.5, [-337.5, -105.5])
    assert (r["lanes"], r["per_s"], r["one_lane_from"]) == (1, 22.5, [-337.5, -105.5])
    assert g["need_per_s"] == 44.8 and not g["enough"]
    assert r["need_per_s"] == 25.6 and not r["enough"]


def test_lanes_on_a_synthetic_belt():
    """An inserter north of an east-going belt drops on the far lane (right); a belt coming down from
    the north onto a belt that also has an input from behind side-loads onto the near lane (left)."""
    names = ("N|1|transport-belt|transport-belt|1|1|bs=0.03125\nN|2|inserter|inserter|1|1\n"
             "N|3|assembling-machine-1|assembling-machine|3|3")
    rows = [f"E|1|{2 * x + 1}|1|4" for x in range(7)]              # main belt, east, y = 0.5
    rows += [f"E|1|7|{2 * y + 1}|8" for y in (-3, -2, -1)]          # side belt, south, x = 3.5
    rows += ["E|3|1|-5|0|r=a|sp=1", "E|2|1|-1|0|p=1,-3|d=1,1"]       # A at (0.5, -2.5) -> main belt
    rows += ["E|3|7|-11|0|r=b|sp=1", "E|2|7|-7|0|p=7,-9|d=7,-5"]     # B at (3.5, -5.5) -> side belt
    recipes = "R|a|1||x:1:item\nR|b|1||y:1:item"
    text = "H|1|t|0|1\n" + names + "\n" + "\n".join(rows) + "\n" + recipes + f"\nZ|{len(rows)}|1|1"
    a = A.Atlas(text)
    st = a._lanes()
    end = [e for e in a.ents if e["type"] == "transport-belt" and e["x"] == 6.5][0]
    assert st[end["id"]]["x"][0] == {"R"}
    assert st[end["id"]]["y"][0] == {"L"} and st[end["id"]]["y"][2] == [3.5, 0.5]
