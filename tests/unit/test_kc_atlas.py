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
