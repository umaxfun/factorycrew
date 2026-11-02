"""Wire graphs for copper and circuit wires: which nodes link, groups, relays between them.

A node is anything with a position and a wire reach: (x, y, reach), [x, y, reach], or a dict
{x, y, reach} / {position: {x, y}, reach} (wire_reach / max_wire_distance are read as reach too).

Factorio's rule: two nodes link when their distance is at most the SHORTER of their two reaches.
Substations (reach 18) 18.03 apart do NOT link. A 1e-9 tolerance absorbs float noise, as
kc.unwired does. `slack` keeps planned links that far under the limit (0 = the game's rule).

Pieces:
  node()          (x, y, reach) of any node form
  can_link()      the rule for one pair
  links()         every linking pair of a node list
  components()    wire groups (lists of indices)
  unwired()       validator: nodes not connected to any root
  too_long()      validator: given wires longer than their ends allow
  chain()         fewest relay poles from any source to any target (A*)
  relay_chain()   chain() between two points
  join_groups()   relays that join separate groups into one network

Relay positions follow the pole's size: odd sizes on tile centers, even sizes on integers. Where a
relay may stand is `passable`: a kc.grid.Grid (can_place for the relay's footprint) or a callable
(x, y) -> bool; None = anywhere.

Written for factorycrew (MIT). The A* chain and the group joining are extracted from
skills/power/lib/power.py, itself written from scratch from an idea only (bridging pole groups in
wire-reach hops as in demipixel/factorio-generators power.js and Ben-Ramchandani/PlannerCore;
neither has a usable license, so no code was read into or copied from them).
"""
from __future__ import annotations

import heapq
import math

from . import geo

EPS = 1e-9


# ── nodes and the link rule ──────────────────────────────────────────────

def node(p, reach=None):
    """(x, y, reach) floats of a node. `reach` is the fallback for a plain point / a dict
    without one; a node with neither raises ValueError."""
    if type(p) is tuple and len(p) == 3:
        x, y, r = p
        if type(x) is float and type(y) is float and type(r) is float:
            return p  # already normalised (the hot path inside the planners)
    if isinstance(p, dict):
        x, y = geo.xy(p)
        r = next((p[k] for k in ("reach", "wire_reach", "max_wire_distance") if p.get(k) is not None), reach)
    else:
        p = list(p)
        x, y = float(p[0]), float(p[1])
        r = p[2] if len(p) > 2 else reach
    if r is None:
        raise ValueError(f"node needs a reach: {p!r}")
    return (x, y, float(r))


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _link(a, b, slack):
    return _dist(a, b) <= min(a[2], b[2]) - slack + EPS


def can_link(a, b, slack=0.0):
    """Whether two nodes link: distance <= the shorter reach - slack (+1e-9)."""
    return _link(node(a), node(b), float(slack))


def _cells(nodes, slack):
    """Spatial hash: cell size >= any link length, so links stay within 3x3 cells."""
    size = max([n[2] for n in nodes] + [1.0]) + max(0.0, -slack) + 1e-6
    cells = {}
    for i, n in enumerate(nodes):
        cells.setdefault((math.floor(n[0] / size), math.floor(n[1] / size)), []).append(i)

    def near(i):
        n = nodes[i]
        cx, cy = math.floor(n[0] / size), math.floor(n[1] / size)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.extend(cells.get((cx + dx, cy + dy), ()))
        return sorted(out)
    return near


def links(nodes, slack=0.0):
    """Every linking pair [i, j] (i < j) of the nodes, sorted."""
    ns = [node(p) for p in nodes]
    near = _cells(ns, slack)
    return [[i, j] for i in range(len(ns)) for j in near(i) if j > i and _link(ns[i], ns[j], slack)]


def _reach_from(ns, seeds, slack, near=None):
    near = near or _cells(ns, slack)
    seen, todo = set(seeds), list(seeds)
    while todo:
        k = todo.pop()
        for j in near(k):
            if j not in seen and _link(ns[k], ns[j], slack):
                seen.add(j)
                todo.append(j)
    return seen


def components(nodes, slack=0.0):
    """Wire groups: lists of node indices, each sorted, ordered by their smallest index (so the
    first group holds node 0)."""
    ns = [node(p) for p in nodes]
    near = _cells(ns, slack)
    seen, out = set(), []
    for i in range(len(ns)):
        if i in seen:
            continue
        comp = _reach_from(ns, [i], slack, near)
        seen |= comp
        out.append(sorted(comp))
    return out


def unwired(nodes, roots=(), slack=0.0):
    """Validator: indices of the nodes NOT connected to any root (directly or through other
    nodes and roots). roots: nodes of the existing network (e.g. grid poles). With no roots the
    nodes must form one group: the ones not connected to nodes[0] are returned. [] = all wired."""
    rs = [node(p) for p in roots]
    ns = rs + [node(p) for p in nodes]
    if len(ns) == len(rs):
        return []
    seeds = list(range(len(rs))) if rs else [0]
    seen = _reach_from(ns, seeds, float(slack))
    return [i - len(rs) for i in range(len(rs), len(ns)) if i not in seen]


def too_long(wires, nodes=None, slack=0.0):
    """Validator: the given wires that the rule does not allow. wires: pairs of nodes, or pairs
    of indices into `nodes`. Returns [{"wire": k, "length": d, "limit": shorter reach - slack}]
    (lengths rounded to 1e-6); [] = every wire is buildable."""
    out = []
    for k, (a, b) in enumerate(wires):
        if nodes is not None and isinstance(a, int) and isinstance(b, int):
            a, b = nodes[a], nodes[b]
        na, nb = node(a), node(b)
        if not _link(na, nb, float(slack)):
            out.append({"wire": k, "length": round(_dist(na, nb), 6),
                        "limit": min(na[2], nb[2]) - float(slack)})
    return out


# ── relay chains (A*) ────────────────────────────────────────────────────

def _off(size):
    return 0.5 if int(size) % 2 else 0.0


def _incl(lo, hi, o):
    """Grid values n + o with lo <= v <= hi."""
    return [n + o for n in range(math.ceil(lo - o - EPS), math.floor(hi - o + EPS) + 1)]


def _fits(grid, x, y, w, h):
    """grid.can_place((x, y), w, h) for a position already on the size's grid, without the
    snap and footprint-set overhead (this runs for every position a search looks at)."""
    occ, bounded = grid.occ, grid.bounds is not None
    bx, by = math.floor(x - w / 2 + EPS) + 0.5, math.floor(y - h / 2 + EPS) + 0.5
    for i in range(int(w)):
        for j in range(int(h)):
            t = (bx + i, by + j)
            if t in occ or (bounded and not grid.inside(t)):
                return False
    return True


def _passable_fn(passable, size):
    if passable is None:
        return lambda x, y: True
    if hasattr(passable, "can_place"):
        if hasattr(passable, "occ") and hasattr(passable, "bounds"):
            return lambda x, y: _fits(passable, x, y, size, size)
        return lambda x, y: passable.can_place((x, y), size, size)
    if callable(passable):
        return passable
    raise TypeError("passable must be None, a kc.grid.Grid or a callable (x, y) -> bool")


def chain(sources, targets, reach, passable=None, *, size=1, slack=0.5, margin=None, budget=4000):
    """Fewest new relay poles (wire reach `reach`, size x size) wiring any source to any target.

    sources/targets: nodes (a plain point takes `reach`). passable: where a relay may stand (see
    the module doc). Each planned link stays `slack` under the shorter reach. margin: how far
    relays may go outside the box around all endpoints (default: one reach). budget: the most
    positions expanded.

    Returns {"ok": True, "relays": [[x, y], ...] (source side first), "count": n,
    "source": [x, y, reach], "target": [x, y, reach]} (no relays when a source already links a
    target) or {"ok": False, "reason"}.

    A* over relay positions: each step places one relay (cost 1) within wire range of the
    previous node; the heuristic ceil((distance to the nearest target - last link) / hop) never
    overestimates, so the chain has the fewest relays. Ties prefer the state nearer the target.
    """
    reach = float(reach)
    srcs = [node(s, reach) for s in sources]
    tgts = [node(t, reach) for t in targets]
    if not srcs or not tgts:
        return {"ok": False, "reason": "no sources or no targets"}
    hop = reach - slack
    if hop <= 0:
        return {"ok": False, "reason": "slack is not below the pole's reach"}
    o = _off(size)
    margin = reach if margin is None else margin
    pts = srcs + tgts
    x0, x1 = min(p[0] for p in pts) - margin, max(p[0] for p in pts) + margin
    y0, y1 = min(p[1] for p in pts) - margin, max(p[1] for p in pts) + margin
    t_last = max(min(reach, t[2]) - slack for t in tgts)
    ok_at = _passable_fn(passable, size)
    ends = {(p[0], p[1]) for p in pts}  # a relay never stands on an endpoint's own position

    def found(path, t, s):
        return {"ok": True, "relays": [list(p) for p in path], "count": len(path), "source": list(s),
                "target": list(t)}

    def near(n):
        return min(tgts, key=lambda t: (_dist(n, t), t[1], t[0]))

    def h(n):
        """(relays still needed at least, distance to the nearest target)."""
        d = _dist(n, near(n))
        if any(_link(n, t, slack) for t in tgts):
            return 0, d
        return max(1, math.ceil((d - t_last) / hop - EPS)), d

    free_cache = {}

    def free(x, y):
        k = (x, y)
        if k not in free_cache:
            free_cache[k] = k not in ends and bool(ok_at(x, y))
        return free_cache[k]

    frontier, best, came, tie = [], {}, {}, 0
    for s in sorted(srcs, key=lambda s: (s[1], s[0])):
        for t in sorted(tgts, key=lambda t: (_dist(s, t), t[1], t[0])):
            if _link(s, t, slack):
                return found([], t, s)
        k = ("src",) + tuple(s)
        best[k] = 0
        came[k] = None
        hh, d = h(s)
        heapq.heappush(frontier, (hh, d, tie, 0, k, s))
        tie += 1
    expanded = 0
    while frontier:
        _, _, _, g, k, n = heapq.heappop(frontier)
        if g > best.get(k, math.inf):
            continue
        if g > 0:
            for t in sorted(tgts, key=lambda t: (_dist(n, t), t[1], t[0])):
                if _link(n, t, slack):
                    path, cur = [], k
                    while came[cur] is not None:
                        path.append((cur[0], cur[1]))
                        cur = came[cur]
                    return found(path[::-1], t, tuple(cur[1:]))
        expanded += 1
        if expanded > budget:
            return {"ok": False, "reason": f"search budget of {budget} poles spent"}
        lim = min(n[2], reach) - slack
        for x in _incl(max(x0, n[0] - lim), min(x1, n[0] + lim), o):
            dy = math.sqrt(max(0.0, lim * lim - (x - n[0]) ** 2))
            for y in _incl(max(y0, n[1] - dy), min(y1, n[1] + dy), o):
                if (x, y) == (n[0], n[1]) or not free(x, y):
                    continue
                m = (x, y, reach)
                if not _link(n, m, slack):
                    continue
                if g + 1 < best.get((x, y), math.inf):
                    best[(x, y)] = g + 1
                    came[(x, y)] = k
                    hh, d = h(m)
                    heapq.heappush(frontier, (g + 1 + hh, d, tie, g + 1, (x, y), m))
                    tie += 1
    return {"ok": False, "reason": "no chain: the target is walled off within the search area"}


def relay_chain(a, b, reach, passable=None, slack=0.5, *, size=1, margin=None, budget=4000):
    """Fewest relay poles between two points or nodes (see chain(); a plain point takes `reach`).
    Every gap stays <= min(reaches) - slack and every relay stands where `passable` allows."""
    return chain([a], [b], reach, passable, size=size, slack=slack, margin=margin, budget=budget)


def join_groups(groups, reach, passable=None, *, size=1, slack=0.5, margin=None, budget=4000):
    """Relays (reach `reach`, size x size) that wire separate groups into one network.

    groups: lists of nodes; they are flattened in order and regrouped by the link rule (with
    `slack`), so groups that already link count as one. While more than one group remains, the
    group holding the first node is chained (chain()) to the nearest other group, and the new
    relays join the network. Relays never overlap each other; `passable` must keep them off the
    existing nodes' own tiles.

    Returns {"ok": True, "relays": [[x, y], ...] in placement order, "count": n} or
    {"ok": False, "reason", "relays": what was placed before the failure, "count"}.
    """
    reach = float(reach)
    nodes = [node(p, reach) for g in groups for p in g]
    ok_at = _passable_fn(passable, size)
    used = set()

    def free(x, y):
        if not ok_at(x, y):
            return False
        if not used:
            return True
        bx, by = math.floor(x - size / 2 + EPS) + 0.5, math.floor(y - size / 2 + EPS) + 0.5
        return not any((bx + i, by + j) in used for i in range(int(size)) for j in range(int(size)))

    relays = []
    while True:
        comps = components(nodes, slack)
        if len(comps) <= 1:
            return {"ok": True, "relays": relays, "count": len(relays)}
        main = comps[0]
        rest = [k for c in comps[1:] for k in c]
        nearest = min(rest, key=lambda k: (min(_dist(nodes[k], nodes[m]) for m in main), k))
        other = next(c for c in comps if nearest in c)
        r = chain([nodes[m] for m in main], [nodes[k] for k in other], reach, free, size=size,
                  slack=slack, margin=margin, budget=budget)
        if not r["ok"]:
            return {"ok": False, "reason": r["reason"], "relays": relays, "count": len(relays)}
        for x, y in r["relays"]:
            relays.append([x, y])
            nodes.append((x, y, reach))
            used.update(geo.footprint((x, y), size, size))
