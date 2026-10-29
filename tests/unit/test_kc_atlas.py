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


def fake_status(lua, status):
    """what STATUS_LUA returns for every position in the query, all with one status"""
    import re as _re
    out = {}
    for name, xy in _re.findall(r'\["([^"]+)"\]=\{([^}]*)\}', lua):
        out[name] = [7] * (len(xy.split(",")) // 2)
    return {"ok": True, "data": {"s": out, "names": {"7": status}}}


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
            return fake_status(body["lua"], "item_ingredient_shortage")
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


def test_trace_rutile_up_through_the_train_to_the_flotation_cells(at):
    t = at.trace(("B", at.block_at(-357.5, -122.5)), "nullius-rutile", "up")
    assert t["need_per_s"] == 25.6
    (stop,) = t["next"]
    assert "rutile" in stop["stop"] and stop["at"] == [-355.0, -101.0]
    (train,) = stop["trains"]
    assert train["train"] == 371 and train["wagons"] == 1
    (load,) = train["next"]
    assert "signal-red" in load["stop"] and load["at"] == [243.0, 313.0]
    (src,) = load["next"]
    assert src["recipe"] == "nullius-rutile" and src["machines"] == 10 and src["make_per_s"] == 8.467


def test_trace_down_follows_the_pipe(at):
    t = at.trace(("B", at.block_at(-357.5, -122.5)), "nullius-titanium-tetrachloride", "down")
    users = [h for h in t["next"] if h["node"].startswith("B")]
    assert [u["recipe"] for u in users] == ["nullius-titanium-ingot"] and users[0]["by"] == ["pipe"]


def test_mcp_trace_with_live_trains(at, monkeypatch):
    import factorio_mcp as m

    def post(path, body, timeout=30.0):
        if path == "/atlas":
            return {"ok": True, "text": DUMP.read_text(), "data": {"parts": 1}}
        if path == "/query" and "train_state" in body["lua"]:
            return {"ok": True, "data": {"371": {"state": "wait_station", "cargo": {"nullius-rutile": 1861},
                                                  "at_stop": "x", "capacity": 2500}}}
        if path == "/query":
            return fake_status(body["lua"], "low_power")
        return {"ok": True}
    monkeypatch.setattr(m, "bridge_post", post)
    monkeypatch.setattr(m, "bridge_health", lambda: {"run_id": "r2", "tick": at.tick})
    m.ATLAS_CACHE.clear()
    v = m.call_tool("factorio_atlas", {"level": "trace", "item": "nullius-rutile", "at": {"x": -357.5, "y": -122.5}})
    assert v["ok"], v
    assert v["needed_per_s"] == 25.6 and v["made_per_s_at_sources"] == 8.467
    train = v["way"]["next"][0]["trains"][0]
    assert train["live"]["cargo"]["nullius-rutile"] == 1861
    assert train["next"][0]["next"][0]["status"] == {"low_power": 10}


def test_map_unfiltered_fits_in_a_turn(at):
    v = at.map_view(min_machines=3, limit=1000, live={})
    import json
    assert v["total"] > 100
    assert len(json.dumps(v)) / 4 < 3500, len(json.dumps(v))


def test_map_item_glass_returns_every_zone_with_a_glass_block(at):
    want = {f"Z{at.zone_of[b['id']]}" for b in at.blocks if "nullius-glass" in b["pro"] | b["ing"]}
    v = at.map_view(item="nullius-glass", limit=1000)
    got = {r.split()[0] for r in v["rows"]}
    assert got == want and all("glass" in r for r in v["rows"])


def test_map_status_sorted_and_stuck_reason_is_never_ok(at):
    live = {}
    for b in at.blocks:
        for i, m in enumerate(b["machines"]):
            live[m["id"]] = "low_power" if i % 3 == 0 else "working" if i % 3 == 1 else "full_output"
    v = at.map_view(status="low_power", limit=1000, live=live)
    counts = [sum(1 for m in at.blocks[0]["machines"])]  # noqa: F841
    assert v["rows"] and all("stuck" in r and "working" not in r.split("stuck")[1] for r in v["rows"])
    nums = [int(r.split("stuck ")[1].split(":")[0]) for r in v["rows"]]
    assert nums == sorted(nums, reverse=True)


def test_map_stations_by_item(at):
    v = at.map_view(kind="station", item="nullius-rutile")
    assert v["total"] == 2 and all("rutile" in r for r in v["rows"])
    t = at.map_view(kind="train", item="nullius-rutile")
    assert any(r.startswith("T371 ") for r in t["rows"])
    n = at.map_view(kind="network")
    assert n["rows"] and "hard-glass" in " ".join(n["rows"])


def test_mcp_map_reads_live_status_in_batches(at, monkeypatch):
    import factorio_mcp as m
    queries = []

    def post(path, body, timeout=30.0):
        if path == "/atlas":
            return {"ok": True, "text": DUMP.read_text(), "data": {"parts": 1}}
        if path == "/query":
            queries.append(body["lua"])
            return fake_status(body["lua"], "low_power")
        return {"ok": True}
    monkeypatch.setattr(m, "bridge_post", post)
    monkeypatch.setattr(m, "bridge_health", lambda: {"run_id": "r3", "tick": at.tick})
    m.ATLAS_CACHE.clear()
    v = m.call_tool("factorio_atlas", {"level": "map", "status": "stuck", "limit": 5})
    assert v["ok"] and len(v["rows"]) == 5 and "stuck" in v["rows"][0], v
    assert len(queries) == -(-sum(len(b["machines"]) for b in at.blocks) // m.STATUS_BATCH)
    assert max(len(q) for q in queries) < 60000


def test_robot_mall_is_its_own_zone_fed_by_robots(at):
    v = at.zone_view(at.zone_at(226, -25.5))
    assert v["feed"] == {"robots": v["blocks"]} and v["blocks"] >= 35, v["feed"]
    assert "nullius-hard-glass" in v["from_network_but_nothing_puts_it_in"]


def test_belt_mall_is_topped_up_by_robots(at):
    v = at.zone_view(at.zone_at(214, -5))
    assert v["feed"].get("belts", 0) > v["feed"].get("topped", 0) > 0, v["feed"]
    assert {"nullius-graphite", "iron-chest", "nullius-steel-plate", "nullius-steel-rod"} <= set(v["robots_top_up"])


def test_network_hands_out_what_nothing_puts_in(at):
    (row,) = at.map_view(kind="network")["rows"]
    assert "hard-glass" in row.split("nothing puts them in:")[1]


def test_no_network_edge_for_what_a_block_gets_by_belt(at):
    for (a, b), items in at.edges.items():
        if a[0] == "N" and b[0] == "B":
            assert not set(items) & at.phys_in[b[1]], (a, b, items)
