"""kc.steiner: tile Steiner join, MST, graph Steiner join, connectivity validators."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import search, steiner  # noqa: E402
from kc.grid import Grid  # noqa: E402


def wall(x, ys):
    return {(x + 0.5, y + 0.5) for y in ys}


def tiles(r):
    return {tuple(t) for t in r["tiles"]}


def not_in(blocked):
    return lambda t: t not in blocked


def test_kc_exposes_steiner():
    assert kc.steiner is steiner


# ── join ──

def test_join_two_points_is_a_shortest_path():
    r = steiner.join([(0.5, 0.5), (6.5, 3.5)], not_in(set()))
    assert r["ok"] and r["size"] == 10 and r["choice"] == [0, 0]
    assert steiner.is_connected(r, [(0.5, 0.5), (6.5, 3.5)])


def test_join_goes_around_a_wall_inside_bounds():
    blocked = wall(3, range(-4, 4))
    r = steiner.join([(0.5, 0.5), (6.5, 0.5)], not_in(blocked))
    assert r["ok"] and not tiles(r) & blocked and steiner.is_connected(r, [(0.5, 0.5), (6.5, 0.5)])
    x0, y0, x1, y1 = -3.5, -3.5, 10.5, 4.5  # terminal box widened by margin 4
    assert all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in tiles(r))
    # the wall covers the whole default box: no way round
    r = steiner.join([(0.5, 0.5), (6.5, 0.5)], not_in(blocked), margin=3)
    assert not r["ok"] and r["unreachable"]


def test_join_shares_a_trunk():
    ts = [(0.5, 0.5), (0.5, 10.5), (10.5, 5.5)]
    r = steiner.join(ts, not_in(set()))
    assert r["ok"] and steiner.is_connected(r, ts)
    pairwise = sum(search.grid_path(ts[0], t, lambda _: True)["cost"] + 1 for t in ts[1:])
    assert r["size"] < pairwise


def test_join_options_pick_one_tile_per_terminal():
    far, near = (20.5, 0.5), (0.5, 4.5)
    r = steiner.join([{"options": [far, near]}, (0.5, 8.5)], not_in(set()))
    assert r["ok"] and r["choice"] == [1, 0] and near in tiles(r) and far not in tiles(r)


def test_join_on_a_finite_tile_set_and_a_grid():
    corridor = {(x + 0.5, 0.5) for x in range(30)} | {(29.5, y + 0.5) for y in range(30)}
    r = steiner.join([(0.5, 0.5), (29.5, 29.5)], corridor)  # finite: no margin box
    assert r["ok"] and tiles(r) == corridor
    g = Grid(blocked=wall(2, range(0, 9)), bounds=(0.5, 0.5, 9.5, 9.5))
    r = steiner.join([(0.5, 0.5), (4.5, 0.5)], g)
    assert r["ok"] and (2.5, 9.5) in tiles(r)  # around the wall's end, the only gap
    r = steiner.join([(0.5, 0.5), (4.5, 0.5)], g, bounds=(0.5, 0.5, 9.5, 8.5))
    assert not r["ok"]


def test_join_reports_blocked_and_cut_off_terminals():
    r = steiner.join([(0.5, 0.5), (1.5, 0.5)], not_in({(1.5, 0.5)}))
    assert not r["ok"] and r["unreachable"] == [1]
    box = wall(9, range(-6, 7)) | wall(11, range(-6, 7)) | {(10.5, -5.5), (10.5, 5.5)}
    r = steiner.join([(0.5, 0.5), (4.5, 0.5), (10.5, 0.5)], not_in(box))
    assert not r["ok"] and r["unreachable"] == [2]
    assert steiner.join([], not_in(set()))["reason"] == "no terminals"


def test_join_budget():
    r = steiner.join([(0.5, 0.5), (40.5, 40.5)], not_in(set()), budget=30)
    assert not r["ok"] and r["reason"] == "search budget spent"
    r = steiner.join([(0.5, 0.5), (40.5, 40.5)], not_in(set()))
    assert r["ok"] and r["expanded"] > 30


def test_join_random_fields_are_connected_and_deterministic():
    rnd = random.Random(5)
    for _ in range(30):
        blocked = {(rnd.randrange(20) + 0.5, rnd.randrange(20) + 0.5) for _ in range(60)}
        ts = []
        while len(ts) < 5:
            t = (rnd.randrange(20) + 0.5, rnd.randrange(20) + 0.5)
            if t not in blocked and t not in ts:
                ts.append(t)
        a = steiner.join(ts, not_in(blocked))
        b = steiner.join(list(ts), not_in(set(sorted(blocked, reverse=True))))
        assert a == b
        if a["ok"]:
            assert not tiles(a) & blocked and steiner.is_connected(a, ts)
            assert len(search.components(tiles(a))) == 1


# ── mst ──

def test_mst_on_points_and_a_forest_when_some_cannot_link():
    pts = [(0, 0), (1, 0), (5, 0), (1, 1)]
    r = steiner.mst(pts)
    assert r["ok"] and r["components"] == 1 and len(r["edges"]) == 3
    assert abs(r["cost"] - 6) < 1e-9
    assert sorted(tuple(sorted(e[:2])) for e in r["edges"]) == [(0, 1), (1, 2), (1, 3)]

    def reach3(a, b):
        d = math.dist(a, b)
        return d if d <= 3 else None
    r = steiner.mst(pts, reach3)
    assert not r["ok"] and r["components"] == 2 and len(r["edges"]) == 2
    assert steiner.mst([])["ok"]


def test_mst_matches_brute_force_on_small_sets():
    import itertools
    rnd = random.Random(2)
    for _ in range(10):
        pts = [(rnd.randrange(10), rnd.randrange(10)) for _ in range(5)]
        best = math.inf
        pairs = list(itertools.combinations(range(5), 2))
        for es in itertools.combinations(pairs, 4):
            comp = list(range(5))

            def f(i):
                while comp[i] != i:
                    i = comp[i]
                return i
            for a, b in es:
                comp[f(a)] = f(b)
            if len({f(i) for i in range(5)}) == 1:
                best = min(best, sum(math.dist(pts[a], pts[b]) for a, b in es))
        assert abs(steiner.mst(pts)["cost"] - best) < 1e-9


# ── join_nodes ──

def pole_linked(reach):
    return lambda a, b: math.dist(a, b) <= reach + 1e-9


def test_join_nodes_uses_relays_only_where_needed():
    # two poles 14 apart, reach 7.5: the relay at 7 is needed, the one off to the side is not
    nodes = [(0, 0), (14, 0), (7, 0), (7, 6), (30, 0)]
    r = steiner.join_nodes(nodes, pole_linked(7.5), terminals=[0, 1])
    assert r["ok"] and r["nodes"] == [0, 1, 2] and r["cost"] == 2
    assert sorted(sorted(e) for e in r["edges"]) == [[0, 2], [1, 2]]


def test_join_nodes_reports_unreachable_terminals_and_custom_cost():
    nodes = [(0, 0), (14, 0), (7, 0), (30, 0)]
    r = steiner.join_nodes(nodes, pole_linked(7.5), terminals=[0, 1, 3])
    assert not r["ok"] and r["unreachable"] == [3] and r["nodes"] == [0, 1, 2]
    # euclidean cost prefers the straight relay over a detour through two relays
    nodes = [(0, 0), (10, 0), (5, 0), (3, 4), (7, 4)]
    r = steiner.join_nodes(nodes, pole_linked(6), math.dist, terminals=[0, 1])
    assert r["nodes"] == [0, 1, 2] and abs(r["cost"] - 10) < 1e-9


def test_join_nodes_default_terminals_is_every_node():
    nodes = [(0, 0), (5, 0), (10, 0)]
    r = steiner.join_nodes(nodes, pole_linked(5))
    assert r["ok"] and r["nodes"] == [0, 1, 2] and len(r["edges"]) == 2


# ── validators ──

def test_unjoined_and_is_connected():
    net = [(0.5, 0.5), (1.5, 0.5), (2.5, 0.5), (8.5, 8.5)]
    assert steiner.is_connected(net, [(0.5, 0.5), (2.5, 0.5)])
    assert steiner.unjoined(net, [(0.5, 0.5), (8.5, 8.5), (2.5, 0.5)]) == [1]
    assert steiner.unjoined(net, [{"options": [(5.5, 5.5), (1.5, 0.5)]}, (0.5, 0.5)]) == []
    assert steiner.unjoined(net, [(4.5, 4.5)]) == [0]
    assert steiner.is_connected([], [])
