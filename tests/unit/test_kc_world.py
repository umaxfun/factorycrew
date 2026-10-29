"""kc.world: the Python half of the mod's world readers (kc.scan / kc.proto / kc.resources).

Fixtures in tests/unit/fixtures/kc_world/<version>/ are recorded from the real game by the level 1
tests in mod/kc-companion/tests/test-helpers.lua (they write script-output/kc-world/*.json) on
Factorio 2.0.77 and 2.1.23. Every test runs against both.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "skills" / "mining" / "lib"))
import kc  # noqa: E402
from kc import geo, world  # noqa: E402

FIX = ROOT / "tests" / "unit" / "fixtures" / "kc_world"
VERSIONS = sorted(p.name for p in FIX.iterdir() if p.is_dir())


def load(version, name):
    return json.loads((FIX / version / f"{name}.json").read_text())


@pytest.fixture(params=VERSIONS)
def ver(request):
    return request.param


def test_fixtures_cover_both_game_versions():
    assert VERSIONS == ["2.0", "2.1"]


# ── scan ─────────────────────────────────────────────────────────────────

def test_runs_expand_to_tile_centers():
    assert world.tiles_from_runs([[500, 530, 532], [501, 4, 4]]) == [
        (530.5, 500.5), (531.5, 500.5), (532.5, 500.5), (4.5, 501.5)]
    assert world.tiles_from_runs({}) == []  # an empty Lua table comes through as {}
    assert world.tiles_from_runs(None) == []


def test_grid_from_scan_places_entities_ghosts_and_terrain(ver):
    scan = load(ver, "scan")
    g = world.grid_from_scan(scan)
    am = g.occupant((505.5, 505.5))
    assert isinstance(am, int) and g.entities[am]["name"] == "assembling-machine-2"
    assert {g.occupant((505.5 + dx, 505.5 + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)} == {am}
    # steam engine facing east: 5 wide, 3 tall, centered on (525.5, 510.5)
    se = g.occupant((525.5, 510.5))
    assert g.entities[se]["name"] == "steam-engine"
    taken = {t for t, o in g.occ.items() if o == se}
    assert taken == geo.footprint((525.5, 510.5), 5, 3)
    assert g.free((525.5, 508.5)) and g.free((522.5, 510.5))
    # the 2x2 substation ghost sits on integers
    sub = g.occupant((519.5, 519.5))
    assert g.entities[sub]["ghost"] and {t for t, o in g.occ.items() if o == sub} == geo.footprint((520, 520), 2, 2)
    # terrain
    water = [t for t, o in g.occ.items() if o == "water"]
    assert sorted(water) == sorted((x + 0.5, y + 0.5) for x in range(530, 535) for y in (500, 501))
    assert any(o == "cliff" for o in g.occ.values())
    # bounds = the scanned box
    assert g.inside((500.5, 500.5)) and not g.inside((540.5, 520.5)) and not g.free((499.5, 520.5))


def test_grid_from_scan_options(ver):
    scan = load(ver, "scan")
    no_ghosts = world.grid_from_scan(scan, ghosts=False)
    assert no_ghosts.free((519.5, 519.5)) and no_ghosts.free((510.5, 515.5))
    unbounded = world.grid_from_scan(scan, bounds=False)
    assert unbounded.free((600.5, 600.5))
    no_terrain = world.grid_from_scan(scan, terrain_blocks=False, skip_types=("generator",))
    assert no_terrain.free((530.5, 500.5)) and no_terrain.free((525.5, 510.5))


def test_entities_and_blocked_tiles(ver):
    scan = load(ver, "scan")
    es = world.entities(scan)
    assert len(es) == scan["count"] == 8
    ug = [e for e in es if e["name"] == "underground-belt" and e.get("io") == "input"][0]
    assert ug["pair"] == (513.5, 510.5) and ug["direction"] == geo.E
    pipe = [e for e in es if e["name"] == "pipe"][0]
    assert pipe["fluid"] == "water"
    blocked = world.blocked_tiles(scan)
    g = world.grid_from_scan(scan, bounds=False)
    assert set(blocked) == g.blocked_tiles()
    assert len(blocked) == len(set(blocked))
    assert blocked == sorted(blocked, key=lambda t: (t[1], t[0]))
    # 9 assembler + 1 inserter + 1 pipe + 2 undergrounds + 15 steam engine + 1 ghost belt + 4 substation
    terrain = world.terrain(scan)
    assert len(blocked) == 33 + len(terrain["water"]) + len(terrain["cliff"])


def test_check_scan(ver):
    scan = load(ver, "scan")
    assert world.check_scan(scan) == []
    cut = dict(scan, truncated=True, count=900, entities=scan["entities"][:2])
    assert world.check_scan(cut) == ["truncated: 2 of 900 entities listed"]
    bad = {"area": [0, 0, 1, 1], "entities": [{"name": "pipe", "x": 0.5}]}
    assert world.check_scan(bad) == ["entity 0 (pipe) lacks y, w, h"]
    assert world.check_scan({}) == ["missing area", "missing entities"]


# ── prototypes ───────────────────────────────────────────────────────────

def test_specs_normalise_and_round_trip(ver):
    specs = world.specs_from_proto(load(ver, "proto"))
    assert specs["no-such-thing"] is None
    pj = specs["pumpjack"]
    assert pj["size"] == (3, 3) and set(pj["fluid_boxes"][0]["ports"]) == {0, 4, 8, 12}
    assert world.normalize_spec(pj) is pj
    again = world.specs_from_proto(json.loads(json.dumps(specs)))  # through JSON and back
    assert world.pole_spec(again["substation"]) == world.pole_spec(specs["substation"])
    assert world.specs_from_proto(load(ver, "proto")["pipe"]) == {"pipe": specs["pipe"]}


def test_pole_drill_belt_beacon_machine_specs(ver):
    specs = world.specs_from_proto(load(ver, "proto"))
    assert world.pole_spec(specs["substation"]) == {"name": "substation", "size": 2, "supply": 9.0, "reach": 18.0}
    assert world.pole_spec(specs["small-electric-pole"])["supply"] == 2.5
    d = world.drill_spec(specs["electric-mining-drill"])
    assert d["size"] == 3 and d["radius"] == 2.49 and d["categories"] == ("basic-solid",)
    assert d["area"] == (-2.49, -2.49, 2.49, 2.49)
    for k in (geo.E, geo.S, geo.W):  # the game's vector, rotated with the drill
        assert d["output"][k] == pytest.approx(geo.rotate(d["output"][geo.N], k // 4))
    assert d["output"][geo.N][1] < -1.5
    b = world.belt_spec(specs["transport-belt"], specs["underground-belt"])
    assert b == {"name": "transport-belt", "speed": 15.0, "belt_speed": 0.03125, "underground": "underground-belt",
                 "max_underground": 5}
    assert world.belt_spec(specs["fast-underground-belt"])["max_underground"] > 5
    bc = world.beacon_spec(specs["beacon"])
    assert bc["supply_area_distance"] == 3 and bc["distribution_effectivity"] == 1.5 and bc["profile"][0] == 1
    m = world.machine_spec(specs["assembling-machine-2"])
    assert m["crafting_speed"] == 0.75 and "crafting" in m["categories"] and m["module_slots"] == 2
    with pytest.raises(ValueError, match="has no supply_area_distance"):
        world.pole_spec(specs["pipe"])


def test_pipe_ports_point_out_of_the_entity(ver):
    specs = world.specs_from_proto(load(ver, "proto"))
    for name in ("pumpjack", "assembling-machine-2", "steam-engine", "electric-mining-drill", "pipe", "offshore-pump"):
        s = specs[name]
        for d in geo.CARDINALS:
            center = geo.snap((10.3, 20.7), s["w"], s["h"], d)
            fp = geo.footprint(center, s["w"], s["h"], d)
            ports = world.pipe_ports(s, d, center=center)
            assert ports, (name, d)
            for p in ports:
                assert geo.tile(p["inside"]) in fp, (name, d, p)
                assert geo.tile(p["target"]) not in fp, (name, d, p)
                assert p["target"] == pytest.approx(geo.move(p["inside"], p["direction"])), (name, d, p)
    out = world.pipe_ports(specs["pumpjack"], geo.N, production="output")
    assert len(out) == 1 and out[0]["flow"] == "output" and out[0]["box"] == 0
    under = world.pipe_ports(specs["pipe-to-ground"], geo.N, kind="underground")
    assert len(under) == 1 and under[0]["max"] == 10 and under[0]["direction"] == geo.S


def test_check_specs(ver):
    specs = world.specs_from_proto(load(ver, "proto"))
    assert world.check_specs(specs, {"substation": "pole", "pumpjack": "fluid", "transport-belt": "belt",
                                     "beacon": "beacon", "electric-mining-drill": "drill",
                                     "assembling-machine-2": "machine"}) == []
    assert world.check_specs(specs, {"pipe": "pole", "no-such-thing": "any", "substation": "fluid"}) == [
        "no-such-thing: unknown prototype",
        "pipe: pipe (pipe) has no supply_area_distance, max_wire_distance",
        "substation: no fluid boxes"]
    assert world.check_specs(specs, ["pipe", "nope"]) == ["nope: unknown prototype"]


# ── resources, and composing with a skill ────────────────────────────────

def test_resources(ver):
    res = load(ver, "resources")
    ore = world.ore_tiles(res, "iron-ore")
    assert len(ore) == 9 and sum(a for _, _, a in ore) == res["iron-ore"]["amount"] == 909
    assert ore[0] == (630.5, 510.5, 100.0)
    assert world.resource_names(res) == ["crude-oil", "iron-ore", "copper-ore"]
    assert world.ore_tiles(res, "stone") == []


def test_specs_feed_the_mining_planner(ver):
    import mining
    specs = world.specs_from_proto(load(ver, "proto"))
    ore = [(x + 0.5, y + 0.5, 500) for x in range(0, 12) for y in range(0, 12)]
    drill = world.drill_spec(specs["electric-mining-drill"])
    belt = world.belt_spec(specs["transport-belt"])
    r = mining.plan(ore, [], toward=(30, 6), drill=drill, belt=belt)
    assert r["entities"] and mining.check(r["entities"], ore, [], drill=drill, belt=belt, exit=r.get("exit")) == []


def test_python39_safe_source():
    src = (ROOT / "mcp" / "kc" / "world.py").read_text()
    assert "from __future__ import annotations" in src and "match " not in src
    assert kc.world is world or world.__name__ == "kc.world"
