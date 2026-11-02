"""Graph search on any neighbour function, plus tile-grid helpers.

A graph is a function `neighbors(node) -> iterable of (next_node, step_cost)`. Nodes are any
hashable values (tile tuples, entity indices, (tile, direction) states...). The searches never
sort nodes, so nodes need not be comparable; their order comes from the order `neighbors`
yields them and the order the starts are given in, which makes every result deterministic.

`starts` may be one node or a list of nodes (multi-source). A tuple of two numbers or a point
dict counts as ONE node (a tile); to start from the integer nodes 1 and 2 pass the list [1, 2].

`goal` may be a node or a predicate `goal(node) -> bool`.

`budget` caps the number of nodes expanded (taken off the frontier); a search that hits it stops
with reason "budget" and returns what it found so far.

Results of bfs/dijkstra are dicts keyed by node ("dist", "parent", "source"): intermediate data
for path_to / tree_edges, not JSON. Paths and component lists are plain lists.

Tile helpers: grid_neighbors() turns a kc.grid.Grid, a passable(tile) predicate or a set of tiles
into a neighbour function (4 or 8 adjacency); grid_path() is A* between tiles; components() and
unreached() are validators on a tile set.
"""
from __future__ import annotations

import heapq
import math
from collections import deque

from . import geo

INF = math.inf
# fixed neighbour order: north, east, south, west, then north-east, south-east, south-west, north-west
_D4 = ((0, -1), (1, 0), (0, 1), (-1, 0))
_D8 = ((1, -1), (1, 1), (-1, 1), (-1, -1))


# ── inputs ───────────────────────────────────────────────────────────────

def _is_point(p):
    if isinstance(p, dict):
        return True
    return isinstance(p, tuple) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p)


def _sources(starts):
    """starts as a list of nodes (one node, or an iterable of nodes)."""
    if _is_point(starts) or isinstance(starts, (str, bytes)):
        return [starts]
    try:
        out = list(starts)
    except TypeError:
        return [starts]
    return out


def _goal_fn(goal):
    if goal is None:
        return None
    if callable(goal):
        return goal
    return lambda n: n == goal


def passable_fn(passable):
    """A predicate passable(tile) from a kc.grid.Grid, a callable, or a collection of tiles."""
    if hasattr(passable, "passable") and callable(passable.passable):
        return passable.passable
    if callable(passable):
        return passable
    free = {geo.tile(t) for t in passable}
    return free.__contains__


def is_finite(passable):
    """Whether the passable area is known to be finite (a tile collection, or a Grid with bounds)."""
    if hasattr(passable, "passable"):
        return getattr(passable, "bounds", None) is not None
    return not callable(passable)


# ── tile grids ───────────────────────────────────────────────────────────

def grid_neighbors(passable, diag=False, *, diag_cost=math.sqrt(2), cut_corners=False):
    """Neighbour function over tile centers: the 4 (or 8 with diag) adjacent tiles that are passable,
    each with cost 1 (diagonals `diag_cost`). passable: a kc.grid.Grid, a passable(tile) predicate,
    or a collection of tiles. Without cut_corners a diagonal step needs both orthogonal tiles free."""
    ok = passable_fn(passable)

    def nb(t):
        x, y = t
        out = []
        for dx, dy in _D4:
            n = (x + dx, y + dy)
            if ok(n):
                out.append((n, 1))
        if diag:
            for dx, dy in _D8:
                n = (x + dx, y + dy)
                if ok(n) and (cut_corners or (ok((x + dx, y)) and ok((x, y + dy)))):
                    out.append((n, diag_cost))
        return out
    return nb


def tile_heuristic(goal, diag=False, diag_cost=math.sqrt(2)):
    """Admissible A* heuristic toward one tile or the nearest of a few tiles: manhattan for 4
    adjacency, octile for 8 (chebyshev when diag_cost is 1)."""
    goals = [geo.tile(goal)] if _is_point(goal) else [geo.tile(g) for g in goal]

    def one(t, g):
        dx, dy = abs(t[0] - g[0]), abs(t[1] - g[1])
        if not diag:
            return dx + dy
        lo, hi = min(dx, dy), max(dx, dy)
        if diag_cost < 1:  # every step moves each axis by at most 1 and costs at least diag_cost
            return hi * diag_cost
        return hi + (min(diag_cost, 2) - 1) * lo
    return lambda t: min(one(t, g) for g in goals)


# ── searches ─────────────────────────────────────────────────────────────

def _result(dist, parent, source, order, found, expanded, reason):
    return {"dist": dist, "parent": parent, "source": source, "order": order, "found": found,
            "expanded": expanded, "reason": reason}


def bfs(starts, neighbors, *, goal=None, max_dist=None, budget=None):
    """Breadth-first search counting steps (step costs ignored), from one or many starts.

    Returns {"dist": {node: steps}, "parent": {node: previous or None}, "source": {node: index of
    the start that reached it first}, "order": nodes in the order expanded, "found": the first goal
    node expanded or None, "expanded": n, "reason": "goal" | "exhausted" | "budget"}.
    max_dist: do not go further than this many steps."""
    is_goal = _goal_fn(goal)
    dist, parent, source, order = {}, {}, {}, []
    q = deque()
    for i, s in enumerate(_sources(starts)):
        if s not in dist:
            dist[s], parent[s], source[s] = 0, None, i
            q.append(s)
    expanded = 0
    while q:
        if budget is not None and expanded >= budget:
            return _result(dist, parent, source, order, None, expanded, "budget")
        cur = q.popleft()
        expanded += 1
        order.append(cur)
        if is_goal is not None and is_goal(cur):
            return _result(dist, parent, source, order, cur, expanded, "goal")
        d = dist[cur] + 1
        if max_dist is not None and d > max_dist:
            continue
        for n, _c in neighbors(cur):
            if n not in dist:
                dist[n], parent[n], source[n] = d, cur, source[cur]
                q.append(n)
    return _result(dist, parent, source, order, None, expanded, "exhausted")


def dijkstra(starts, neighbors, *, goal=None, max_cost=None, budget=None):
    """Cheapest-path search with non-negative step costs, from one or many starts (each at cost 0).

    Same result shape as bfs(), with "dist" the path cost. Only settled (expanded) nodes and the
    frontier appear in "dist"; a frontier node's cost may not be final. max_cost: ignore paths
    costing more."""
    is_goal = _goal_fn(goal)
    dist, parent, source, order, done = {}, {}, {}, [], set()
    heap, k = [], 0
    for i, s in enumerate(_sources(starts)):
        if s not in dist:
            dist[s], parent[s], source[s] = 0, None, i
            heap.append((0, k, s))
            k += 1
    heapq.heapify(heap)
    expanded = 0
    while heap:
        d, _, cur = heapq.heappop(heap)
        if cur in done or d > dist[cur]:
            continue
        if budget is not None and expanded >= budget:
            return _result(dist, parent, source, order, None, expanded, "budget")
        done.add(cur)
        expanded += 1
        order.append(cur)
        if is_goal is not None and is_goal(cur):
            return _result(dist, parent, source, order, cur, expanded, "goal")
        for n, c in neighbors(cur):
            if c < 0:
                raise ValueError(f"negative step cost {c} from {cur!r} to {n!r}")
            nd = d + c
            if n in done or (max_cost is not None and nd > max_cost):
                continue
            if nd < dist.get(n, INF):
                dist[n], parent[n], source[n] = nd, cur, source[cur]
                heapq.heappush(heap, (nd, k, n))
                k += 1
    return _result(dist, parent, source, order, None, expanded, "exhausted")


def astar(start, goal, neighbors, heuristic=None, *, budget=100_000):
    """A* from one or many starts to `goal` (a node or a predicate).

    heuristic(node) -> estimated remaining cost; must not overestimate for the path to be the
    cheapest (None: 0, i.e. Dijkstra). Returns {"ok": True, "path": [start ... goal], "cost": c,
    "expanded": n} or {"ok": False, "reason": "unreachable" | "budget", "path": [], "cost": None,
    "expanded": n}."""
    is_goal = _goal_fn(goal)
    h = heuristic or (lambda n: 0)
    g, parent, heap, k = {}, {}, [], 0
    for s in _sources(start):
        if s not in g:
            g[s], parent[s] = 0, None
            heap.append((h(s), k, 0, s))
            k += 1
    heapq.heapify(heap)
    expanded = 0
    while heap:
        _f, _, gc, cur = heapq.heappop(heap)
        if gc > g[cur]:  # stale entry: a cheaper way here was found later
            continue
        if is_goal(cur):
            return {"ok": True, "path": path_to(parent, cur), "cost": gc, "expanded": expanded}
        if expanded >= budget:
            return {"ok": False, "reason": "budget", "path": [], "cost": None, "expanded": expanded}
        expanded += 1
        for n, c in neighbors(cur):
            if c < 0:
                raise ValueError(f"negative step cost {c} from {cur!r} to {n!r}")
            ng = gc + c
            if ng < g.get(n, INF):
                g[n], parent[n] = ng, cur
                heapq.heappush(heap, (ng + h(n), k, ng, n))
                k += 1
    return {"ok": False, "reason": "unreachable", "path": [], "cost": None, "expanded": expanded}


def path_to(parent, node):
    """Path [source, ..., node] from a parent map (bfs/dijkstra "parent"), or [] if node is not in it."""
    if node not in parent:
        return []
    out, seen = [], set()
    while node is not None:
        if node in seen:
            raise ValueError("parent map has a cycle")
        seen.add(node)
        out.append(node)
        node = parent[node]
    return out[::-1]


def reachable(starts, neighbors, *, max_dist=None, budget=None):
    """Set of nodes reachable from the starts (the starts included), optionally within max_dist steps."""
    return set(bfs(starts, neighbors, max_dist=max_dist, budget=budget)["dist"])


def shortest_path_tree(starts, neighbors, *, max_cost=None, budget=None):
    """Cheapest-path tree from the starts: dijkstra() result plus "edges": [(parent, child), ...] in
    settle order. Only settled nodes are in the tree."""
    r = dijkstra(starts, neighbors, max_cost=max_cost, budget=budget)
    r["edges"] = tree_edges(r["parent"], r["order"])
    return r


def tree_edges(parent, order=None):
    """(parent, child) pairs of a parent map, in `order` (default: the map's insertion order)."""
    nodes = order if order is not None else list(parent)
    return [(parent[n], n) for n in nodes if parent.get(n) is not None]


def graph_components(nodes, neighbors):
    """Connected components of the subgraph on `nodes` (neighbours outside it are ignored).
    Components come in the order of their first node in `nodes`, members in BFS order."""
    nodes = list(nodes)
    inside = set(nodes)
    seen, out = set(), []
    for s in nodes:
        if s in seen:
            continue
        seen.add(s)
        comp, q = [s], deque([s])
        while q:
            cur = q.popleft()
            for n, _c in neighbors(cur):
                if n in inside and n not in seen:
                    seen.add(n)
                    comp.append(n)
                    q.append(n)
        out.append(comp)
    return out


# ── tile conveniences and validators ─────────────────────────────────────

def grid_path(start, goal, passable, diag=False, *, budget=100_000, diag_cost=math.sqrt(2)):
    """Cheapest tile path from start to goal (a tile, a collection of tiles, or a predicate) over
    passable tiles. The start need not be passable; the goal tile must be. Same result as astar()."""
    nb = grid_neighbors(passable, diag, diag_cost=diag_cost)
    if callable(goal):
        g, h = goal, None
    elif _is_point(goal):
        gt = geo.tile(goal)
        g, h = gt, tile_heuristic(gt, diag, diag_cost)
    else:
        goals = {geo.tile(t) for t in goal}
        g, h = goals.__contains__, (tile_heuristic(sorted(goals), diag, diag_cost) if goals else None)
    starts = [geo.tile(s) for s in _sources(start)]
    return astar(starts, g, nb, h, budget=budget)


def components(tiles, diag=False):
    """Connected groups of a tile set (4 adjacency, or 8 with diag), largest first; each group a
    sorted list of tile tuples. One group = the tiles are connected."""
    ts = sorted({geo.tile(t) for t in tiles})
    comps = [sorted(c) for c in graph_components(ts, grid_neighbors(set(ts), diag, cut_corners=True))]
    comps.sort(key=lambda c: (-len(c), c[0]))
    return comps


def unreached(tiles, start, targets, diag=False):
    """Targets NOT connected to `start` through adjacent tiles of `tiles` ([] = all reached). A
    start outside `tiles` reaches nothing. Targets come back as tile tuples, in the given order."""
    ts = {geo.tile(t) for t in tiles}
    starts = [geo.tile(s) for s in _sources(start)]
    starts = [s for s in starts if s in ts]
    seen = reachable(starts, grid_neighbors(ts, diag, cut_corners=True)) if starts else set()
    return [geo.tile(t) for t in _sources(targets) if geo.tile(t) not in seen]
