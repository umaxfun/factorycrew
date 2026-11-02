"""Join many terminals into one connected network.

join(): one connected set of tiles (4-adjacent) joining every terminal over passable tiles; a
terminal may offer several alternative tiles and the join picks one. Use it for pipes, belts or
rails as a layout skeleton, landfill causeways, any tile network that must touch many points.
mst(): minimum spanning tree over points with any distance (Prim).
join_nodes(): Steiner tree on any graph given as nodes plus an edge predicate (poles linked by
wire reach, stations linked by track), using extra nodes only where they help.
unjoined() / is_connected(): validators for a tile network.

join() is the Steiner-tree heuristic of OilOutpostPlanner's SolveSteinerTree: flood-fill to keep
only the largest connected region holding terminal tiles, then grow a BFS front from every
terminal in parallel, level by level; the first time two fronts touch, the shortest path between
their two terminals becomes network, the two terminals merge into one whose front restarts from
all of its network, and the growth goes on until one terminal is left. Not the optimal tree, and
not the shortest path per pair; a good shared trunk in O(area) per merge.

Ported from https://github.com/Coppermine-factorio/OilOutpostPlanner (MIT, (c) 2024
Coppermine) at commit 72015903b0ed492c28b6b5fa7cae575873c9c9f5, layout.lua: `FloodFill` (L327)
and `SolveSteinerTree` (L372). Changes: Lua -> stdlib Python; tile tuples instead of `Pos2Str`;
the `choose_subtarget` callback became the returned `choice` list; targets iterate in index order
(deterministic); the entry caps became one `budget` on expanded tiles. License text:
skills/pipes/LICENSE-upstream. mst() and join_nodes() are textbook (Prim; Takahashi-Matsuyama
shortest-path heuristic), not from upstream.
"""
from __future__ import annotations

import math

from . import geo, search

_D4 = ((0, -1), (1, 0), (0, 1), (-1, 0))  # north, east, south, west: the order fixes tie-breaks


def _nb(p):
    return [(p[0] + dx, p[1] + dy) for dx, dy in _D4]


def options(t):
    """The alternative tiles of a terminal: a point, or {"options": [points]} (deduplicated, in order)."""
    if isinstance(t, dict) and "options" in t:
        out = []
        for o in t["options"]:
            o = geo.tile(o)
            if o not in out:
                out.append(o)
        return out
    return [geo.tile(t)]


def join(terminals, passable, bounds=None, *, margin=4, budget=400_000):
    """One connected network of tiles joining every terminal over passable tiles.

    terminals: points, or {"options": [points]} for a terminal that may use any one of several
        tiles (the choice is returned). Terminal tiles must be passable themselves.
    passable: a kc.grid.Grid, a passable(tile) predicate, or a collection of free tiles.
    bounds: (x0, y0, x1, y1) on tile centers, inclusive (as kc.grid.Grid bounds). None: no extra
        box when `passable` is finite (a tile collection or a Grid with bounds), otherwise the
        terminal tiles' bounding box widened by `margin` tiles.
    budget: most tiles expanded by the fronts.
    Returns {"ok": True, "tiles": [[x, y], ...] sorted, "choice": [option index per terminal],
    "size": n, "expanded": n} or {"ok": False, "reason": ..., "unreachable": [terminal indices]}.
    """
    ok_tile = search.passable_fn(passable)
    targets = [options(t) for t in terminals]
    if not targets:
        return {"ok": False, "reason": "no terminals", "unreachable": []}
    if bounds is None and not search.is_finite(passable):
        pts = [p for opts in targets for p in opts]
        bounds = (min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin,
                  max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin)
    if bounds is not None:
        x0, y0, x1, y1 = bounds

        def free(t):
            return x0 <= t[0] <= x1 and y0 <= t[1] <= y1 and ok_tile(t)
    else:
        free = ok_tile

    # keep only options that are free and in the largest region holding options
    live = [[(i, o) for i, o in enumerate(opts) if free(o)] for opts in targets]
    bad = [k for k, opts in enumerate(live) if not opts]
    if bad:
        return {"ok": False, "reason": "every tile of these terminals is blocked or out of bounds", "unreachable": bad}
    remaining = sorted({o for opts in live for _, o in opts})
    best = None
    nb_free = search.grid_neighbors(free)
    while remaining:
        region = search.reachable([remaining[0]], nb_free)
        inside = [o for o in remaining if o in region]
        if best is None or len(inside) > best[0]:
            best = (len(inside), region)
        remaining = [o for o in remaining if o not in region]
    reach = best[1]
    live = [[(i, o) for i, o in opts if o in reach] for opts in live]
    bad = [k for k, opts in enumerate(live) if not opts]
    if bad:
        return {"ok": False, "reason": "these terminals cannot reach the others", "unreachable": bad}

    choice = [None] * len(targets)
    if len(targets) == 1:
        choice[0] = live[0][0][0]
        return {"ok": True, "tiles": [list(live[0][0][1])], "choice": choice, "size": 1, "expanded": 0}

    # claim[tile] = (target, previous tile on the way back to the target or None, option or None)
    claim, hood, hidx, paths = {}, {}, {}, set()

    def clear(t):
        for pos, _ in hood.pop(t, []):
            claim.pop(pos, None)
        hidx.pop(t, None)

    for t, opts in enumerate(live):
        hood[t], hidx[t] = [], 0
        for i, o in opts:
            if o in claim:
                # two terminals share a starting tile: both take it, and they are one terminal now
                choice[t] = i
                clear(t)
                other, _, oi = claim[o]
                if oi is not None:
                    choice[other] = oi
                    clear(other)
                    claim[o] = (other, None, None)
                    hood[other], hidx[other] = [(o, 1)], 0
                    paths.add(o)
                break
            claim[o] = (t, None, i)
            hood[t].append((o, 1))

    expanded = 0

    def find_merge():
        nonlocal expanded
        level = 1
        while True:
            for t in sorted(hood):
                h, idx = hood[t], hidx[t]
                while True:
                    if idx >= len(h):
                        return None
                    pos, dist = h[idx]
                    if dist > level:
                        break
                    expanded += 1
                    if expanded > budget:
                        return "budget"
                    for c in _nb(pos):
                        if c not in reach:
                            continue
                        cl = claim.get(c)
                        if cl is None:
                            claim[c] = (t, pos, None)
                            h.append((c, level + 1))
                        elif cl[0] != t:
                            return (t, cl[0], pos, c)
                    idx += 1
                hidx[t] = idx
            level += 1

    while len(hood) > 1:
        m = find_merge()
        if m is None or m == "budget":
            why = "search budget spent" if m == "budget" else "a terminal's front ran out of room"
            return {"ok": False, "reason": why, "unreachable": []}
        t1, t2, p1, p2 = m
        path, chosen = [], {}
        for pos in (p1, p2):
            while True:
                path.append(pos)
                tgt, prev, oi = claim[pos]
                if prev is not None:
                    pos = prev
                    continue
                if oi is not None:
                    choice[tgt] = oi
                    chosen[tgt] = oi
                break
        old = []
        for t in (t1, t2):
            for pos, dist in hood[t]:
                if dist > 1:
                    break
                oi = claim[pos][2]
                if oi is None or oi == chosen.get(t):
                    old.append((pos, 1))
        clear(t1)
        clear(t2)
        hood[t1], hidx[t1] = old, 0
        for pos, _ in old:
            claim[pos] = (t1, None, None)
        for pos in path:
            paths.add(pos)
            if pos not in claim:
                old.append((pos, 1))
                claim[pos] = (t1, None, None)
    for t, opts in enumerate(live):
        if choice[t] is None:  # the last terminal standing, never merged into another
            for i, o in opts:
                if o in paths:
                    choice[t] = i
                    break
    tiles = sorted(paths)
    return {"ok": True, "tiles": [list(p) for p in tiles], "choice": choice, "size": len(tiles), "expanded": expanded}


def mst(points, dist=None):
    """Minimum spanning tree (Prim, O(n²) distance calls) over points.

    dist(a, b) -> cost, or None / math.inf when a and b cannot be linked. Default: euclidean
    distance of point forms. When some points cannot be linked the result is a spanning forest.
    Returns {"ok": one tree spans all points, "edges": [[i, j, cost], ...] (i already in the tree),
    "cost": total, "components": number of trees}. Ties go to the lower index."""
    dist = dist or geo.euclid
    n = len(points)
    in_tree, best, via = [False] * n, [math.inf] * n, [-1] * n
    edges, total, comps = [], 0, 0
    for root in range(n):
        if in_tree[root]:
            continue
        comps += 1
        best[root], via[root] = 0, -1
        while True:
            j, bj = -1, math.inf
            for k in range(n):
                if not in_tree[k] and best[k] < bj:
                    j, bj = k, best[k]
            if j < 0:
                break
            in_tree[j] = True
            if via[j] >= 0:
                edges.append([via[j], j, bj])
                total += bj
            for k in range(n):
                if not in_tree[k]:
                    d = dist(points[j], points[k])
                    if d is not None and d < best[k]:
                        best[k], via[k] = d, j
    return {"ok": comps <= 1, "edges": edges, "cost": total, "components": comps}


def join_nodes(nodes, edge_ok, cost=None, *, terminals=None, budget=None):
    """Steiner tree on a graph: connect the `terminals` (indices into `nodes`) using any other nodes
    as relays. Shortest-path heuristic: start from the first terminal, repeatedly add the cheapest
    path from the tree to the nearest terminal not yet in it.

    nodes: anything (pole candidates, stations...). edge_ok(a, b) -> bool: whether two nodes can be
        linked (assumed symmetric; called once per pair, O(n²)). cost(a, b) -> edge cost, default 1
        per edge (so the tree uses the fewest relays). terminals: default every node.
    Returns {"ok": bool, "nodes": sorted indices in the tree, "edges": [[i, j], ...], "cost": total,
    "unreachable": terminal indices left out} ("reason" when not ok).
    """
    n = len(nodes)
    terms = list(range(n)) if terminals is None else list(dict.fromkeys(int(t) for t in terminals))
    for t in terms:
        if not 0 <= t < n:
            raise ValueError(f"terminal index {t} out of range for {n} nodes")
    if not terms:
        return {"ok": True, "nodes": [], "edges": [], "cost": 0, "unreachable": []}
    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if edge_ok(nodes[i], nodes[j]):
                adj[i].append(j)
                adj[j].append(i)
    weight = {}

    def w(i, j):
        key = (i, j) if i < j else (j, i)
        if key not in weight:
            weight[key] = 1 if cost is None else cost(nodes[key[0]], nodes[key[1]])
        return weight[key]

    def nb(i):
        return [(j, w(i, j)) for j in adj[i]]

    tree, edges, total = [terms[0]], [], 0
    in_tree = {terms[0]}
    left = set(terms[1:])
    while left:
        r = search.dijkstra(tree, nb, goal=left.__contains__, budget=budget)
        if r["found"] is None:
            out = sorted(left)
            reason = "search budget spent" if r["reason"] == "budget" else "these terminals cannot reach the others"
            return {"ok": False, "reason": reason, "nodes": sorted(tree), "edges": edges, "cost": total,
                    "unreachable": out}
        path = search.path_to(r["parent"], r["found"])
        for a, b in zip(path, path[1:]):
            edges.append([a, b])
            total += w(a, b)
        for p in path:
            if p not in in_tree:
                in_tree.add(p)
                tree.append(p)
            left.discard(p)
    return {"ok": True, "nodes": sorted(tree), "edges": edges, "cost": total, "unreachable": []}


def _network_tiles(network):
    if isinstance(network, dict) and "tiles" in network:
        network = network["tiles"]
    return {geo.tile(t) for t in network}


def unjoined(network, terminals, diag=False):
    """Indices of terminals the network does not join ([] = joined). network: tiles (or a join()
    result). A terminal (point or {"options": [...]}) is joined when one of its tiles lies in the
    network's component that holds the most terminals (ties: the one holding the lowest index)."""
    ts = _network_tiles(network)
    opts = [options(t) for t in terminals]
    if not opts:
        return []
    best = set()
    best_key = None
    for comp in search.components(ts, diag):
        cs = set(comp)
        hit = [k for k, o in enumerate(opts) if any(p in cs for p in o)]
        key = (-len(hit), hit[0] if hit else math.inf)
        if best_key is None or key < best_key:
            best, best_key = cs, key
    return [k for k, o in enumerate(opts) if not any(p in best for p in o)]


def is_connected(network, terminals, diag=False):
    """Whether one connected component of the network tiles touches a tile of every terminal."""
    return not unjoined(network, terminals, diag)
