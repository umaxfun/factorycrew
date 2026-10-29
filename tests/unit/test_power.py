"""Pole planner (skills/power/lib/power.py): pure Python, no game."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "power" / "lib"))
import power  # noqa: E402

SUB, BIG, MED, SMALL = power.SUBSTATION, power.BIG_POLE, power.MEDIUM_POLE, power.SMALL_POLE


def pos(r):
    return [(e["x"], e["y"]) for e in r["entities"]]


def test_substations_18_03_apart_with_reach_18_do_not_link():
    """The real failure: two substations 18.03 apart split an oil field into two networks."""
    a = dict(SUB, x=0.0, y=0.0)
    b = dict(SUB, x=18.0, y=1.0)  # 18.028 apart
    assert math.hypot(18, 1) > 18
    probs = power.check([], [a, b])
    assert any("not wired" in p for p in probs), probs
    # exactly at the limit the game links them; the planner never plans that close to it
    assert power.check([], [a, dict(SUB, x=18.0, y=0.0)]) == []
    assert any("not wired" in p for p in power.check([], [a, dict(SUB, x=18.0, y=0.0)], slack=0.5))
    # wired through the grid: the grid substation at 18.03 from the new one does not link either
    probs = power.check([], [b], grid_poles=[(0.0, 0.0, 18.0)])
    assert any("not wired to the grid" in p for p in probs), probs


def test_planner_never_plans_a_link_at_the_limit():
    """Two lamp clusters whose ideal substations would sit 18.03 apart: the plan must not rely on it."""
    lamps = [(x + 0.5, y + 0.5) for x in range(-4, 4) for y in range(-4, 4)]
    lamps += [(x + 18.5, y + 1.5) for x in range(-4, 4) for y in range(-4, 4)]
    r = power.cover(lamps, SUB)
    assert r["ok"], r
    assert power.check(lamps, r["entities"], slack=0.5) == []
    nodes = [(e["x"], e["y"], e["reach"]) for e in r["entities"]]
    for i, a in enumerate(nodes):
        others = [math.hypot(a[0] - b[0], a[1] - b[1]) for j, b in enumerate(nodes) if j != i]
        assert others and min(others) <= 17.5 + 1e-9, nodes


def test_single_consumer_one_pole_on_its_grid():
    for spec in (SUB, BIG):  # even size: integer coordinates
        r = power.cover([(10.5, 10.5)], spec)
        assert r["ok"] and r["poles"] == 1, r
        x, y = pos(r)[0]
        assert x == int(x) and y == int(y)
    for spec in (MED, SMALL):  # odd size: tile centers
        r = power.cover([{"x": 10.5, "y": 10.5}], spec)
        assert r["ok"] and r["poles"] == 1, r
        x, y = pos(r)[0]
        assert x % 1 == 0.5 and y % 1 == 0.5
        assert (x, y) != (10.5, 10.5)  # not on the lamp


def test_supply_edge_contact_does_not_count():
    # medium pole at 0.5: supply square -3..4 covers tiles 3 away, not 4
    pole = dict(MED, x=0.5, y=0.5)
    assert power.check([(3.5, 0.5)], [pole]) == []
    assert any("outside" in p for p in power.check([(4.5, 0.5)], [pole]))
    # a 3x3 pumpjack counts with its whole footprint: at 4.5 its west tile is 3 away, at 5.5 it is 4
    assert power.check([{"x": 4.5, "y": 0.5, "w": 3, "h": 3}], [pole]) == []
    assert any("outside" in p for p in power.check([{"x": 5.5, "y": 0.5, "w": 3, "h": 3}], [pole]))


def test_lamp_row_two_substations_one_group():
    lamps = [(x + 0.5, 0.5) for x in range(0, 36, 3)]
    r = power.cover(lamps, SUB)
    assert r["ok"] and r["poles"] == 2 and r["join_poles"] == 0, r
    assert power.check(lamps, r["entities"], slack=0.5) == []


def test_block_of_drills_not_more_than_a_grid():
    # 8x8 electric drills (3x3) in pairs with a 2-tile lane between pairs (where poles go):
    # 30x30 tiles, a substation grid needs ceil(30/18)^2 = 4
    drills = [{"x": 1.5 + 3 * i + 2 * (i // 2), "y": 1.5 + 3 * j + 2 * (j // 2), "w": 3, "h": 3}
              for i in range(8) for j in range(8)]
    r = power.cover(drills, SUB)
    assert r["ok"] and r["cover_poles"] <= 4, r
    assert power.check(drills, r["entities"], slack=0.5) == []  # incl. no pole on a drill


def test_blocked_tiles_are_avoided_and_uncoverable_reported():
    lamp = (0.5, 0.5)
    ring = {(x + 0.5, y + 0.5) for x in range(-5, 6) for y in range(-5, 6)} - {lamp}
    r = power.cover([lamp], MED, ring)  # every spot within 3 tiles is blocked
    assert not r["ok"] and r["uncoverable"] == [0], r
    r = power.cover([lamp], SUB, ring)  # a substation reaches it from outside the ring
    assert r["ok"] and r["poles"] == 1, r
    assert power.check([lamp], r["entities"], blocked=ring) == []


def test_far_clusters_are_joined_into_one_group():
    a = [(x + 0.5, 0.5) for x in range(3)]
    b = [(x + 80.5, 0.5) for x in range(3)]
    r = power.cover(a + b, MED)
    assert r["ok"] and r["cover_poles"] == 2 and r["join_poles"] >= 9, r  # 80 tiles / 8.5 per hop
    assert power.check(a + b, r["entities"], slack=0.5) == []
    # with big poles as joiners: far fewer extra poles
    r2 = power.cover(a + b, MED, join=BIG)
    assert r2["ok"] and r2["join_poles"] < r["join_poles"], r2
    assert {e["name"] for e in r2["entities"] if e["role"] == "join"} == {"big-electric-pole"}
    assert power.check(a + b, r2["entities"], slack=0.5) == []


def test_link_uses_the_fewest_reach_poles():
    group = [dict(SUB, x=100.0, y=0.0, role="cover")]
    grid = [(0.0, 0.0, 30.0)]  # a big pole
    r = power.link(group, grid, reach_pole=BIG)
    # 100 tiles: substation -> big <= 17.5, big -> big <= 29.5, big -> grid <= 29.5:
    # 2 poles reach 17.5 + 29.5 + 29.5 = 76.5 < 100, 3 reach 106: 3 is the minimum
    assert r["ok"] and r["poles"] == 3, r
    assert r["target"] == [0.0, 0.0, 30.0]
    assert power.check([], group + r["entities"], grid, slack=0.5) == []


def test_link_already_in_reach_needs_nothing_and_picks_the_cheapest_target():
    group = [dict(MED, x=0.5, y=0.5)]
    assert power.link(group, [(8.5, 0.5, 9.0)])["poles"] == 0
    # the nearest grid pole is a small pole 30 away (17.5 + 7 < 30: two big poles); a substation
    # 34 away takes one (17.5 + 17.5 >= 34)
    grid = [(0.0, 30.0, 7.5), (0.0, -34.0, 18.0)]
    r = power.link([dict(SUB, x=0.0, y=0.0)], grid, reach_pole=BIG)
    assert r["ok"] and r["target"][2] == 18.0 and r["poles"] == 1, r
    r1 = power.link([dict(SUB, x=0.0, y=0.0)], grid, reach_pole=BIG, candidates=1)
    assert r1["ok"] and r1["target"][2] == 7.5 and r1["poles"] == 2, r1


def test_link_goes_around_a_band_too_wide_to_wire_over():
    # wires pass over anything, but no pole may stand in a 40-tile band: big poles (30) cannot hop it
    band = {(x + 0.5, y + 0.5) for x in range(20, 60) for y in range(-30, 31)}
    group = [dict(BIG, x=0.0, y=0.0)]
    r = power.link(group, [(80.0, 0.0, 30.0)], band, reach_pole=BIG)
    assert not r["ok"], r  # the search box (reach margin) does not reach around the band's ends
    wide = power.link(group, [(80.0, 0.0, 30.0)], band, reach_pole=BIG, margin=40)
    assert wide["ok"], wide  # a larger margin goes around its end
    assert power.check([], group + wide["entities"], [(80.0, 0.0, 30.0)], band, slack=0.5) == []
    narrow = {(x + 0.5, y + 0.5) for x in range(20, 45) for y in range(-200, 201)}
    r = power.link(group, [(80.0, 0.0, 30.0)], narrow, reach_pole=BIG)
    assert r["ok"], r
    assert power.check([], group + r["entities"], [(80.0, 0.0, 30.0)], narrow, slack=0.5) == []


def test_plan_cover_and_link_check_clean():
    lamps = [(100 + x + 0.5, 50 + y + 0.5) for x in range(0, 30, 4) for y in range(0, 30, 4)]
    grid = [(0.5, 0.5, 9.0)]
    r = power.plan(lamps, SUB, grid_poles=grid, reach_pole=BIG)
    assert r["ok"] and r["link_poles"] >= 1, r
    assert power.check(lamps, r["entities"], grid, slack=0.5) == []
    assert power.check(lamps, r["entities"], grid) == []


def test_check_catches_bad_plans():
    lamps = [(0.5, 0.5)]
    good = power.cover(lamps, SUB)["entities"]
    assert power.check(lamps, good) == []
    g = good[0]
    assert any("overlaps consumer" in p for p in power.check(lamps, [dict(g, x=1.0, y=1.0)]))
    assert any("integer coordinates" in p for p in power.check(lamps, [dict(g, x=g["x"] + 0.5)]))
    assert any("blocked" in p for p in power.check(lamps, good, blocked=[(g["x"] - 0.5, g["y"] - 0.5)]))
    assert any("overlaps pole" in p for p in power.check(lamps, [g, dict(g, x=g["x"] + 1)]))
    assert any("overlaps grid pole" in p for p in power.check(lamps, good, [(g["x"] + 0.5, g["y"] + 0.5, 9.0)]))
    assert any("outside" in p for p in power.check([(40.5, 0.5)], good))


def test_spec_and_point_forms():
    kc_pole = {"name": "my-sub", "item": "my-sub", "supply": 9, "reach": 18, "size": 2, "craftable": True}
    proto = {"name": "my-sub", "tile_width": 2, "supply_area_distance": 9, "wire_reach": 18}
    lamps = [{"position": {"x": 3.5, "y": 3.5}, "width": 1, "height": 1}, (20.5, 3.5, 1, 1)]
    a, b = power.cover(lamps, kc_pole), power.cover(lamps, proto)
    assert a["ok"] and pos(a) == pos(b) and a["entities"][0]["name"] == "my-sub"
    r = power.link(a["entities"], [{"position": {"x": 60, "y": 3}, "reach": 30, "size": 2}],
                   reach_pole={"name": "nl-big", "size": 2, "max_wire_distance": 30})
    assert r["ok"] and {e["name"] for e in r["entities"]} == {"nl-big"}, r


def test_deterministic_and_order_independent():
    rng = random.Random(7)
    lamps = sorted({(rng.randrange(0, 60) + 0.5, rng.randrange(0, 60) + 0.5) for _ in range(40)})
    r1, r2 = power.cover(lamps, MED), power.cover(lamps, MED)
    assert r1 == r2
    shuffled = lamps[:]
    rng.shuffle(shuffled)
    assert sorted(pos(power.cover(shuffled, MED))) == sorted(pos(r1))


def test_random_layouts_always_check_clean():
    rng = random.Random(11)
    for case in range(25):
        spec = (SUB, MED, SMALL)[case % 3]
        cons, used = [], set()
        for _ in range(rng.randrange(3, 25)):
            w = rng.choice((1, 2, 3))
            x0, y0 = rng.randrange(0, 70), rng.randrange(0, 70)
            tiles = {(x0 + i + 0.5, y0 + j + 0.5) for i in range(w) for j in range(w)}
            if tiles & used:
                continue
            used |= tiles
            cons.append({"x": x0 + w / 2, "y": y0 + w / 2, "w": w, "h": w})
        blocked = {(rng.randrange(0, 70) + 0.5, rng.randrange(0, 70) + 0.5) for _ in range(300)} - used
        grid = [(-20.0, 35.0, 30.0, 2.0, 2)]
        r = power.plan(cons, spec, blocked, grid, reach_pole=BIG)
        assert r["ok"], (case, r.get("reason"))
        assert power.check(cons, r["entities"], grid, blocked, slack=0.5) == [], case


def test_check_wiring_agrees_with_kc_unwired():
    """check() and kc.unwired (mcp/kc_compute.py) apply the same shorter-reach rule."""
    sys.path.insert(0, str(ROOT / "mcp"))
    import kc_compute as kc
    rng = random.Random(3)
    for _ in range(200):
        grid = (0.0, 0.0, rng.choice((7.5, 9.0, 18.0, 30.0)))
        poles = []
        for _ in range(rng.randrange(1, 6)):
            spec = rng.choice((SUB, BIG, MED, SMALL))
            o = 0.5 if spec["size"] % 2 else 0.0
            poles.append(dict(spec, x=rng.randrange(-40, 40) + o, y=rng.randrange(-40, 40) + o))
        bad = {(p[0], p[1]) for p in kc.unwired([(p["x"], p["y"], p["reach"]) for p in poles], grid)}
        probs = [p for p in power.check([], poles, [grid]) if "not wired" in p]
        mine = {(poles[int(p.split("#")[1].split()[0])]["x"], poles[int(p.split("#")[1].split()[0])]["y"])
                for p in probs}
        assert mine == bad, (grid, poles)
