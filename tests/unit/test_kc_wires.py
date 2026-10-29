"""kc.wires: link rule, groups, validators, relay chains. Pure Python, no game."""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import wires  # noqa: E402


def gaps_ok(points, reach, slack):
    """Every consecutive gap within the shorter reach minus slack."""
    def r(p):
        return p[2] if len(p) > 2 else reach
    return all(math.dist(a[:2], b[:2]) <= min(reach, r(a), r(b)) - slack + 1e-9
               for a, b in zip(points, points[1:]))


# ── the rule ──

def test_substations_18_03_apart_with_reach_18_do_not_link():
    """The real failure: two substations 18.03 apart split an oil field into two networks."""
    a, b = (0.0, 0.0, 18.0), (18.0, 1.0, 18.0)
    assert math.hypot(18, 1) > 18
    assert not wires.can_link(a, b)
    assert wires.can_link(a, (18.0, 0.0, 18.0))  # exactly at the limit: linked
    assert not wires.can_link(a, (18.0, 0.0, 18.0), slack=0.5)
    assert wires.components([a, b]) == [[0], [1]]
    assert wires.unwired([b], [a]) == [0]
    assert wires.too_long([(a, b)]) == [{"wire": 0, "length": round(math.hypot(18, 1), 6), "limit": 18.0}]


def test_shorter_reach_decides_and_node_forms():
    big = {"x": 0, "y": 0, "reach": 30}
    small = {"position": {"x": 8, "y": 0}, "wire_reach": 7.5}
    assert not wires.can_link(big, small)  # 8 > 7.5 even though the big pole reaches 30
    assert wires.can_link(big, {"x": 7.5, "y": 0, "max_wire_distance": 7.5})
    assert wires.can_link([0, 0, 9], (0, 9, 30))
    assert wires.node((1, 2), reach=5) == (1.0, 2.0, 5.0)
    try:
        wires.node((1, 2))
        raise AssertionError("a node without a reach was accepted")
    except ValueError:
        pass


def test_float_noise_at_the_limit_still_links():
    a = (0.1 + 0.2, 0.0, 9.0)  # 0.30000000000000004
    assert wires.can_link(a, (9.3, 0.0, 9.0))


# ── groups and validators ──

def test_links_and_components():
    nodes = [(0.0, 0.0, 9.0), (9.0, 0.0, 9.0), (18.0, 0.0, 9.0), (50.0, 0.0, 9.0), (54.0, 0.0, 30.0)]
    assert wires.links(nodes) == [[0, 1], [1, 2], [3, 4]]
    assert wires.components(nodes) == [[0, 1, 2], [3, 4]]
    assert wires.components([]) == []
    json.dumps(wires.links(nodes))


def test_unwired_roots_and_no_roots():
    grid = [(0.0, 0.0, 30.0), (100.0, 0.0, 30.0)]  # assumed one network
    poles = [(25.0, 0.0, 30.0), (52.0, 0.0, 30.0), (75.0, 0.0, 30.0), (200.0, 0.0, 30.0)]
    assert wires.unwired(poles, grid) == [3]  # 52 links through 75 to the second grid pole
    assert wires.unwired(poles) == [3]  # no roots: connected to poles[0]
    assert wires.unwired([], grid) == []
    assert wires.unwired(poles, [(300.0, 0.0, 30.0)]) == [0, 1, 2, 3]


def test_unwired_agrees_with_kc_compat_unwired():
    rng = random.Random(3)
    for _ in range(300):
        grid = (0.0, 0.0, rng.choice((7.5, 9.0, 18.0, 30.0)))
        poles = []
        for _ in range(rng.randrange(1, 8)):
            o = rng.choice((0.0, 0.5))
            poles.append((rng.randrange(-40, 40) + o, rng.randrange(-40, 40) + o,
                          rng.choice((7.5, 9.0, 18.0, 30.0))))
        old = {tuple(p) for p in kc.unwired(poles, grid)}
        new = {poles[i] for i in wires.unwired(poles, [grid])}
        assert old == new, (grid, poles)


def test_too_long_with_index_pairs():
    nodes = [(0.0, 0.0, 9.0), (9.0, 0.0, 9.0), (20.0, 0.0, 30.0)]
    assert wires.too_long([(0, 1), (1, 2)], nodes) == [{"wire": 1, "length": 11.0, "limit": 9.0}]
    assert wires.too_long([(0, 1)], nodes, slack=0.5) == [{"wire": 0, "length": 9.0, "limit": 8.5}]


# ── relay chains ──

def test_relay_chain_fewest_relays_and_gaps():
    # 100 apart with reach 30 and slack 0.5: 3 hops of 29.5 fall short (88.5), 4 hops need 3 relays
    r = wires.relay_chain((0.0, 0.0), (100.0, 0.0), 30, size=2)
    assert r["ok"] and r["count"] == 3, r
    pts = [r["source"]] + [p + [30.0] for p in r["relays"]] + [r["target"]]
    assert gaps_ok(pts, 30, 0.5)
    assert all(x == int(x) and y == int(y) for x, y in r["relays"])  # size 2: integers
    r1 = wires.relay_chain((0.5, 0.5), (40.5, 0.5), 9, size=1)
    assert r1["ok"] and all(x % 1 == 0.5 and y % 1 == 0.5 for x, y in r1["relays"])
    assert r1["count"] == math.ceil(40 / 8.5) - 1
    assert wires.relay_chain((0.0, 0.0), (10.0, 0.0), 30)["count"] == 0


def test_relay_chain_endpoint_reach_limits_the_last_hop():
    # target is a small pole (7.5): the last gap must be <= 7.0 even though relays reach 30, so
    # one relay cannot do it (it would need x >= 33 and x <= 29.5): two
    r = wires.relay_chain((0.0, 0.0, 30.0), (40.0, 0.0, 7.5), 30, size=2)
    assert r["ok"] and r["count"] == 2, r
    x, y = r["relays"][-1]
    assert math.hypot(40 - x, y) <= 7.0 + 1e-9
    assert [40.0, 0.0] not in r["relays"]  # never on the endpoint itself
    assert gaps_ok([r["source"]] + [p + [30.0] for p in r["relays"]] + [r["target"]], 30, 0.5)
    # 33 apart: one relay
    assert wires.relay_chain((0.0, 0.0, 30.0), (33.0, 0.0, 7.5), 30, size=2)["count"] == 1


def test_relay_chain_avoids_blocked_positions_grid_and_callable():
    wall = [(x + 0.5, y + 0.5) for x in range(10, 30) for y in range(-50, 51)]  # 20 wide
    g = kc.Grid(blocked=wall)
    r = wires.relay_chain((0.5, 0.5), (40.5, 0.5), 9, g)
    assert not r["ok"]  # medium poles cannot hop 20 tiles
    # big poles: one relay left of the wall (x <= 9) reaches 36 (27 <= 29.5); 40 needs two
    r = wires.relay_chain((0.0, 0.0), (36.0, 0.0), 30, g, size=2)
    assert r["ok"] and r["count"] == 1 and r["relays"][0][0] <= 9, r
    r = wires.relay_chain((0.0, 0.0), (40.0, 0.0), 30, g, size=2)
    assert r["ok"] and r["count"] == 2, r
    for x, y in r["relays"]:
        assert g.can_place((x, y), 2, 2)
    banned = {(20.0, 0.0)}
    rc = wires.relay_chain((0.0, 0.0), (40.0, 0.0), 30, lambda x, y: (x, y) not in banned, size=2)
    assert rc["ok"] and [20.0, 0.0] not in rc["relays"]
    assert not wires.relay_chain((0.0, 0.0), (40.0, 0.0), 30, lambda x, y: False)["ok"]


def test_relay_chain_margin_and_budget():
    band = kc.Grid(blocked=[(x + 0.5, y + 0.5) for x in range(20, 60) for y in range(-30, 31)])
    assert not wires.relay_chain((0.0, 0.0), (80.0, 0.0), 30, band, size=2)["ok"]
    wide = wires.relay_chain((0.0, 0.0), (80.0, 0.0), 30, band, size=2, margin=40)
    assert wide["ok"], wide
    r = wires.relay_chain((0.0, 0.0), (400.0, 0.0), 9, budget=5)
    assert not r["ok"] and "budget" in r["reason"]
    assert not wires.relay_chain((0, 0), (50, 0), 9, slack=9)["ok"]


def test_chain_picks_the_cheapest_target():
    targets = [(0.0, 30.0, 7.5), (0.0, -34.0, 18.0)]
    r = wires.chain([(0.0, 0.0, 18.0)], targets, 30, size=2)
    assert r["ok"] and r["target"] == [0.0, -34.0, 18.0] and r["count"] == 1, r


def test_join_groups_makes_one_network():
    a = [(0.5, 0.5, 9.0), (8.5, 0.5, 9.0)]
    b = [(80.5, 0.5, 9.0)]
    c = [(40.5, 40.5, 9.0)]
    r = wires.join_groups([a, b, c], 9)
    assert r["ok"], r
    allnodes = a + b + c + [tuple(p) + (9.0,) for p in r["relays"]]
    assert len(wires.components(allnodes, slack=0.5)) == 1
    assert r["count"] == len(r["relays"])
    # relays never overlap each other (size 2)
    r2 = wires.join_groups([a, b, c], 30, size=2)
    tiles = [t for p in r2["relays"] for t in kc.footprint(p, 2, 2)]
    assert len(tiles) == len(set(tiles))
    # groups that already link count as one: nothing to add
    assert wires.join_groups([[(0.0, 0.0, 9.0)], [(5.0, 0.0, 9.0)]], 9)["relays"] == []


def test_join_groups_reports_partial_relays_on_failure():
    a, b = [(0.5, 0.5, 9.0)], [(30.5, 0.5, 9.0)]
    wall = kc.Grid(blocked=[(x + 0.5, y + 0.5) for x in range(12, 20) for y in range(-60, 61)])
    r = wires.join_groups([a, b], 9, wall)
    assert not r["ok"] and r["relays"] == [] and "walled off" in r["reason"]


def test_deterministic():
    rng = random.Random(1)
    groups = [[(rng.randrange(0, 120) + 0.5, rng.randrange(0, 120) + 0.5, 9.0)] for _ in range(6)]
    assert wires.join_groups(groups, 9) == wires.join_groups(groups, 9)


def test_kc_exposes_the_module():
    assert kc.wires is wires
