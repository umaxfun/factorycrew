"""kc.search: graph search on any neighbour function, tile-grid helpers, validators."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import search  # noqa: E402
from kc.grid import Grid  # noqa: E402


def wall(x, ys):
    return {(x + 0.5, y + 0.5) for y in ys}


def box_passable(blocked, n=10):
    return lambda t: t not in blocked and 0 <= t[0] < n and 0 <= t[1] < n


def line_graph(n):
    """0 - 1 - 2 ... n-1 with cost 1, plus a costly shortcut 0 - (n-1)."""
    def nb(i):
        out = []
        if i > 0:
            out.append((i - 1, 1))
        if i < n - 1:
            out.append((i + 1, 1))
        if i == 0:
            out.append((n - 1, 100))
        if i == n - 1:
            out.append((0, 100))
        return out
    return nb


def brute_dist(passable, start, n=10):
    """Reference BFS distances by repeated relaxation."""
    free = [(x + 0.5, y + 0.5) for x in range(n) for y in range(n) if passable((x + 0.5, y + 0.5))]
    d = {start: 0}
    changed = True
    while changed:
        changed = False
        for t in free:
            for u in ((t[0] + 1, t[1]), (t[0] - 1, t[1]), (t[0], t[1] + 1), (t[0], t[1] - 1)):
                if u in d and d[u] + 1 < d.get(t, math.inf):
                    d[t] = d[u] + 1
                    changed = True
    return d


def test_kc_exposes_search():
    assert kc.search is search


# ── generic graphs ──

def test_bfs_counts_steps_and_ignores_costs():
    r = search.bfs(0, line_graph(6))
    assert r["dist"][5] == 1 and r["dist"][3] == 3
    assert r["reason"] == "exhausted" and r["expanded"] == 6


def test_dijkstra_uses_costs_and_paths_reconstruct():
    r = search.dijkstra(0, line_graph(6))
    assert r["dist"][5] == 5
    assert search.path_to(r["parent"], 5) == [0, 1, 2, 3, 4, 5]
    assert search.path_to(r["parent"], 99) == []


def test_multi_source_labels_nearest_source():
    r = search.bfs([0, 9], line_graph(10), max_dist=None)
    assert r["source"][2] == 0 and r["source"][7] == 1
    assert r["dist"][4] == 4 and r["dist"][5] == 4  # 5 is 4 steps from 9 (shortcut 0-9 counts as 1 step)


def test_two_number_tuple_is_one_node_and_list_is_many():
    nb = search.grid_neighbors(lambda t: True)
    r = search.bfs((0.5, 0.5), nb, max_dist=1)
    assert set(r["dist"]) == {(0.5, 0.5), (0.5, -0.5), (1.5, 0.5), (0.5, 1.5), (-0.5, 0.5)}
    r = search.bfs([1, 2], line_graph(5), max_dist=0)
    assert set(r["dist"]) == {1, 2}


def test_goal_node_and_predicate_stop_early():
    r = search.bfs(0, line_graph(50), goal=3)
    assert r["found"] == 3 and r["reason"] == "goal" and r["dist"][3] == 3
    r = search.dijkstra(0, line_graph(50), goal=lambda i: i % 7 == 6)
    assert r["found"] == 6 and r["dist"][6] == 6


def test_budget_stops_and_says_so():
    r = search.bfs(0, line_graph(100), budget=5)
    assert r["reason"] == "budget" and r["expanded"] == 5
    r = search.dijkstra(0, line_graph(100), budget=5)
    assert r["reason"] == "budget" and len(r["order"]) == 5
    a = search.astar(0, 60, line_graph(100), budget=10)
    assert not a["ok"] and a["reason"] == "budget" and a["expanded"] == 10


def test_max_dist_and_max_cost_limit_the_area():
    assert search.reachable(0, line_graph(20), max_dist=2) == {0, 1, 2, 19, 18}
    r = search.dijkstra(0, line_graph(20), max_cost=3)
    assert set(r["order"]) == {0, 1, 2, 3}


def test_negative_cost_is_refused():
    def nb(i):
        return [(i + 1, -1)] if i < 3 else []
    for f in (lambda: search.dijkstra(0, nb), lambda: search.astar(0, 3, nb)):
        try:
            f()
        except ValueError:
            continue
        raise AssertionError("negative cost accepted")


def test_astar_with_inconsistent_heuristic_still_finds_cheapest_path():
    # a -> b (1) -> d (1); a -> c (1) -> d (1); heuristic overrates b a little
    g = {"a": [("b", 1), ("c", 1)], "b": [("d", 1)], "c": [("d", 1.5)], "d": []}
    r = search.astar("a", "d", lambda n: g[n], lambda n: {"b": 0.6, "c": 0, "a": 0, "d": 0}[n])
    assert r["ok"] and r["path"] == ["a", "b", "d"] and r["cost"] == 2


def test_astar_unreachable():
    r = search.astar(0, 9, lambda i: [])
    assert not r["ok"] and r["reason"] == "unreachable" and r["path"] == []


def test_shortest_path_tree_edges():
    r = search.shortest_path_tree(0, line_graph(5))
    assert r["edges"] == [(0, 1), (1, 2), (2, 3), (3, 4)]


def test_graph_components_on_a_node_subset():
    comps = search.graph_components([0, 1, 3, 4, 7], line_graph(10))
    assert comps == [[0, 1], [3, 4], [7]]


# ── tile grids ──

def test_grid_path_goes_around_a_wall_and_is_shortest():
    blocked = wall(3, range(0, 8))
    p = box_passable(blocked)
    r = search.grid_path((0.5, 0.5), (6.5, 0.5), p)
    assert r["ok"] and r["path"][0] == (0.5, 0.5) and r["path"][-1] == (6.5, 0.5)
    ref = brute_dist(p, (0.5, 0.5))
    assert r["cost"] == ref[(6.5, 0.5)] == len(r["path"]) - 1
    assert not any(t in blocked for t in r["path"])
    for a, b in zip(r["path"], r["path"][1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def test_bfs_matches_reference_distances_on_random_grids():
    rnd = random.Random(3)
    for _ in range(20):
        blocked = {(rnd.randrange(10) + 0.5, rnd.randrange(10) + 0.5) for _ in range(30)}
        blocked.discard((0.5, 0.5))
        p = box_passable(blocked)
        r = search.bfs((0.5, 0.5), search.grid_neighbors(p))
        assert r["dist"] == brute_dist(p, (0.5, 0.5))
        for t in list(r["dist"])[::7]:
            a = search.grid_path((0.5, 0.5), t, p)
            assert a["ok"] and a["cost"] == r["dist"][t]


def test_grid_neighbors_takes_a_grid_a_set_or_a_predicate():
    g = Grid(blocked=[(1.5, 0.5)], bounds=(0.5, 0.5, 3.5, 3.5))
    nb = search.grid_neighbors(g)
    assert [t for t, _ in nb((0.5, 0.5))] == [(0.5, 1.5)]
    nb = search.grid_neighbors({(0.5, 0.5), (1.5, 0.5)})
    assert nb((0.5, 0.5)) == [((1.5, 0.5), 1)]


def test_diagonals_cost_and_corner_cutting():
    p = box_passable(set())
    r = search.grid_path((0.5, 0.5), (3.5, 3.5), p, diag=True)
    assert len(r["path"]) == 4 and abs(r["cost"] - 3 * math.sqrt(2)) < 1e-9
    corner = {(1.5, 0.5), (0.5, 1.5)}
    nb = search.grid_neighbors(box_passable(corner), diag=True)
    assert (1.5, 1.5) not in [t for t, _ in nb((0.5, 0.5))]
    nb = search.grid_neighbors(box_passable(corner), diag=True, cut_corners=True)
    assert (1.5, 1.5) in [t for t, _ in nb((0.5, 0.5))]


def test_grid_path_to_a_set_of_tiles_takes_the_nearest():
    p = box_passable(set())
    r = search.grid_path((0.5, 0.5), [(9.5, 9.5), (2.5, 0.5)], p)
    assert r["path"][-1] == (2.5, 0.5) and r["cost"] == 2


def test_components_and_unreached():
    ts = {(0.5, 0.5), (1.5, 0.5), (5.5, 5.5), (6.5, 6.5)}
    assert search.components(ts) == [[(0.5, 0.5), (1.5, 0.5)], [(5.5, 5.5)], [(6.5, 6.5)]]
    assert search.components(ts, diag=True) == [[(0.5, 0.5), (1.5, 0.5)], [(5.5, 5.5), (6.5, 6.5)]]
    assert search.unreached(ts, (0.5, 0.5), [(1.5, 0.5), (5.5, 5.5)]) == [(5.5, 5.5)]
    assert search.unreached(ts, (5.5, 5.5), [(6.5, 6.5)], diag=True) == []
    assert search.unreached(ts, (9.5, 9.5), [(0.5, 0.5)]) == [(0.5, 0.5)]  # start off the network
    assert search.components([]) == []


def test_results_are_deterministic():
    rnd = random.Random(11)
    blocked = {(rnd.randrange(10) + 0.5, rnd.randrange(10) + 0.5) for _ in range(25)}
    blocked -= {(0.5, 0.5), (9.5, 9.5)}
    a = search.grid_path((0.5, 0.5), (9.5, 9.5), box_passable(blocked))
    b = search.grid_path((0.5, 0.5), (9.5, 9.5), box_passable(set(sorted(blocked, reverse=True))))
    assert a == b
