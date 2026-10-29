"""Electric pole planner for factorio_compute (`import power`).

cover(): the fewest cover poles whose supply squares reach every consumer, as one wired group.
  1. Candidates: every free pole position (on the pole's grid: odd sizes on tile centers x.5,
     even sizes on integer coordinates) whose supply square overlaps at least one consumer
     footprint. A pole may not stand on a blocked tile, a consumer, or another new pole.
  2. Greedy max-coverage set cover: repeatedly take the candidate that covers the most
     still-unpowered consumers (gains kept in buckets, updated as consumers get covered). Ties go
     to a candidate that wires to a pole already taken, then to the one nearest `anchor`, then by
     y, x, so the result is deterministic and tends to stay one group.
  3. Prune: drop any pole (latest first) whose consumers are all covered by other poles.
  4. Join: while the poles form more than one wire group, chain the main group to the nearest
     other group with the fewest extra poles (A* below).
link(): the fewest `reach` poles that wire a group to an existing grid pole (A* below).
check(): every consumer covered, every new pole wired to the grid, nothing on blocked tiles,
  nothing overlapping, every pole on its size's grid.

A* for chains: states are pole positions, each step places one pole (cost 1) within wire range
of the previous one; the heuristic ceil((distance to the nearest target - last link) / hop) never
overestimates, so the chain has the fewest poles. Ties prefer the state nearer the target.

Wiring rule (Factorio's, the same as kc.unwired): two poles link when their distance is at most
the SHORTER of their two wire reaches (substations 18.03 apart with reach 18 do NOT link). The
planner keeps `slack` (default half a tile) under that limit; check() applies the game rule
exactly unless given a slack. Supply: an entity is powered when its tile footprint overlaps the
pole's supply square (half-size = supply_area_distance) by more than an edge.

Written for factorycrew (MIT) from an idea only: greedy set cover + bridging groups in wire-reach
hops as in demipixel/factorio-generators (power.js) and Ben-Ramchandani/PlannerCore; neither has a
usable license, so no code was read into or copied from them.
"""
from __future__ import annotations

import heapq
import math

EPS = 1e-9

# Vanilla defaults only; Kira passes the prototypes' real numbers (kc.poles() gives them).
SUBSTATION = {"name": "substation", "size": 2, "supply": 9.0, "reach": 18.0}
BIG_POLE = {"name": "big-electric-pole", "size": 2, "supply": 2.0, "reach": 30.0}
MEDIUM_POLE = {"name": "medium-electric-pole", "size": 1, "supply": 3.5, "reach": 9.0}
SMALL_POLE = {"name": "small-electric-pole", "size": 1, "supply": 2.5, "reach": 7.5}


# ── input forms ──

def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _tile(p):
    x, y = _xy(p)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def _get(d, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _spec(s, default):
    """A pole spec dict {name, size, supply, reach}; accepts kc.poles() entries and the
    prototype names supply_area_distance / wire_reach / max_wire_distance / tile_width."""
    s = dict(default if s is None else s)
    out = {
        "name": s.get("name", default["name"]),
        "size": int(_get(s, "size", "tile_width", default=1)),
        "supply": float(_get(s, "supply", "supply_area_distance", default=0.0)),
        "reach": float(_get(s, "reach", "wire_reach", "max_wire_distance", default=0.0)),
    }
    if out["size"] < 1 or out["reach"] <= 0:
        raise ValueError(f"pole spec needs size >= 1 and reach > 0: {s}")
    return out


def _consumer(c):
    """(x, y, w, h) of a consumer: {x, y, w, h} / {position, width, height} / tile_width... /
    (x, y) / (x, y, w, h). Size defaults to 1x1."""
    if isinstance(c, dict):
        x, y = _xy(c)
        w = float(_get(c, "w", "width", "tile_width", default=1))
        h = float(_get(c, "h", "height", "tile_height", default=w))
        return (x, y, w, h)
    if len(c) >= 4:
        return (float(c[0]), float(c[1]), float(c[2]), float(c[3]))
    return (float(c[0]), float(c[1]), 1.0, 1.0)


def _gridpole(g):
    """(x, y, reach, supply, size) of an existing pole: (x, y, reach[, supply, size]) or a dict
    with x/y or position, reach, and optional supply, size."""
    if isinstance(g, dict):
        x, y = _xy(g)
        return (x, y, float(_get(g, "reach", "wire_reach", "max_wire_distance")),
                float(_get(g, "supply", "supply_area_distance", default=0.0)),
                int(_get(g, "size", "tile_width", default=1)))
    g = list(g)
    return (float(g[0]), float(g[1]), float(g[2]),
            float(g[3]) if len(g) > 3 else 0.0, int(g[4]) if len(g) > 4 else 1)


# ── geometry ──

def _off(size):
    return 0.5 if size % 2 else 0.0


def _strict(lo, hi, o):
    """Grid values n + o with lo < v < hi."""
    return [n + o for n in range(math.floor(lo - o + EPS) + 1, math.ceil(hi - o - EPS))]


def _incl(lo, hi, o):
    """Grid values n + o with lo <= v <= hi."""
    return [n + o for n in range(math.ceil(lo - o - EPS), math.floor(hi - o + EPS) + 1)]


def _foot(x, y, w, h=None):
    h = w if h is None else h
    x0, y0 = x - w / 2, y - h / 2
    return [(math.floor(x0 + EPS) + i + 0.5, math.floor(y0 + EPS) + j + 0.5)
            for i in range(int(round(w))) for j in range(int(round(h)))]


def _covers(px, py, supply, c):
    cx, cy, w, h = c
    return abs(px - cx) < supply + w / 2 - EPS and abs(py - cy) < supply + h / 2 - EPS


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _links(a, b, slack):
    """a, b: (x, y, reach, ...). Factorio: distance <= the shorter reach (minus our slack)."""
    return _dist(a, b) <= min(a[2], b[2]) - slack + EPS


def _entity(spec, x, y, role):
    return {"name": spec["name"], "x": x, "y": y, "size": spec["size"], "supply": spec["supply"],
            "reach": spec["reach"], "role": role}


def _node(p):
    """(x, y, reach) of a planned pole entity or an existing pole."""
    if isinstance(p, dict) and "reach" not in p and "wire_reach" not in p:
        raise ValueError(f"pole needs a reach: {p}")
    g = _gridpole(p)
    return (g[0], g[1], g[2])


def _groups(nodes, slack):
    """Wire groups (lists of indices, each sorted) under the link rule."""
    seen, out = set(), []
    for i in range(len(nodes)):
        if i in seen:
            continue
        comp, todo = [], [i]
        seen.add(i)
        while todo:
            k = todo.pop()
            comp.append(k)
            for j in range(len(nodes)):
                if j not in seen and _links(nodes[k], nodes[j], slack):
                    seen.add(j)
                    todo.append(j)
        out.append(sorted(comp))
    return out


# ── chains of poles (A*) ──

def _chain(sources, targets, spec, taken, slack, margin=None, budget=4000):
    """Fewest new `spec` poles wiring any source to any target. sources/targets: (x, y, reach).
    taken: tiles a new pole may not cover. Returns (positions, target, source) or
    (None, reason, None)."""
    size, reach = spec["size"], spec["reach"]
    hop = reach - slack
    if hop <= 0:
        return None, "slack is not below the pole's reach", None
    o = _off(size)
    margin = reach if margin is None else margin
    pts = list(sources) + list(targets)
    x0, x1 = min(p[0] for p in pts) - margin, max(p[0] for p in pts) + margin
    y0, y1 = min(p[1] for p in pts) - margin, max(p[1] for p in pts) + margin
    t_last = max(min(reach, t[2]) - slack for t in targets)

    def near(n):
        return min(targets, key=lambda t: (_dist(n, t), t[1], t[0]))

    def h(n):
        """(poles still needed at least, distance to the nearest target)."""
        d = _dist(n, near(n))
        if any(_links(n, t, slack) for t in targets):
            return 0, d
        return max(1, math.ceil((d - t_last) / hop - EPS)), d

    free_cache = {}

    def free(x, y):
        k = (x, y)
        if k not in free_cache:
            free_cache[k] = not any(t in taken for t in _foot(x, y, size))
        return free_cache[k]

    frontier, best, came, tie = [], {}, {}, 0
    for s in sorted(sources, key=lambda s: (s[1], s[0])):
        for t in sorted(targets, key=lambda t: (_dist(s, t), t[1], t[0])):
            if _links(s, t, slack):
                return [], t, s
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
            for t in sorted(targets, key=lambda t: (_dist(n, t), t[1], t[0])):
                if _links(n, t, slack):
                    path, cur = [], k
                    while came[cur] is not None:
                        path.append((cur[0], cur[1]))
                        cur = came[cur]
                    return path[::-1], t, tuple(cur[1:])
        expanded += 1
        if expanded > budget:
            return None, f"search budget of {budget} poles spent", None
        lim = min(n[2], reach) - slack
        for x in _incl(max(x0, n[0] - lim), min(x1, n[0] + lim), o):
            dy = math.sqrt(max(0.0, lim * lim - (x - n[0]) ** 2))
            for y in _incl(max(y0, n[1] - dy), min(y1, n[1] + dy), o):
                if (x, y) == (n[0], n[1]) or not free(x, y):
                    continue
                m = (x, y, reach)
                if not _links(n, m, slack):
                    continue
                if g + 1 < best.get((x, y), math.inf):
                    best[(x, y)] = g + 1
                    came[(x, y)] = k
                    hh, d = h(m)
                    heapq.heappush(frontier, (g + 1 + hh, d, tie, g + 1, (x, y), m))
                    tie += 1
    return None, "no chain: the target is walled off within the search area", None


# ── public API ──

def cover(consumers, pole=None, blocked=(), *, join=None, anchor=None, slack=0.5, margin=None,
          budget=4000):
    """Fewest `pole` poles covering every consumer, as one wire-connected group.

    consumers: entities needing power, each {x, y, w, h} (or width/height, tile_width/
        tile_height, or (x, y[, w, h])): center + tile size. Their tiles are kept free of poles.
    pole: {name, size, supply, reach} (a kc.poles() entry works; supply_area_distance / wire_reach
        are accepted too). Default: vanilla substation.
    blocked: tiles no pole may cover (entities, ghosts, water, cliffs, the design's own tiles).
    join: spec of the poles added only to wire groups together (default: `pole`).
    anchor: point the first poles lean towards (e.g. the nearest grid pole); default: the
        consumers' center.
    slack: planned links stay this far under the shorter reach (default half a tile).
    margin: how far a join chain may wander outside its endpoints' box (default: one reach).

    Returns {"ok": True, "entities": [{name, x, y, size, supply, reach, role}], "poles",
    "cover_poles", "join_poles"} or {"ok": False, "reason", "entities" (what was found),
    "uncoverable": [indices]}. role is "cover" or "join".
    """
    spec = _spec(pole, SUBSTATION)
    jspec = _spec(join, spec) if join is not None else spec
    cons = [_consumer(c) for c in consumers]
    if not cons:
        return {"ok": True, "entities": [], "poles": 0, "cover_poles": 0, "join_poles": 0}
    taken = {_tile(b) for b in blocked}
    for c in cons:
        taken.update(_foot(*c))
    if anchor is None:
        anchor = (sum(c[0] for c in cons) / len(cons), sum(c[1] for c in cons) / len(cons))
    else:
        anchor = _xy(anchor)
    size, supply, o = spec["size"], spec["supply"], _off(spec["size"])

    # 1. candidates
    covers, by_cons = {}, [[] for _ in cons]
    for i, c in enumerate(cons):
        cx, cy, w, h = c
        for x in _strict(cx - supply - w / 2, cx + supply + w / 2, o):
            for y in _strict(cy - supply - h / 2, cy + supply + h / 2, o):
                p = (x, y)
                if p not in covers:
                    if any(t in taken for t in _foot(x, y, size)):
                        covers[p] = None
                        continue
                    covers[p] = []
                if covers[p] is not None:
                    covers[p].append(i)
                    by_cons[i].append(p)
    uncoverable = [i for i in range(len(cons)) if not by_cons[i]]
    covers = {p: v for p, v in covers.items() if v}

    # 2. greedy max coverage with gain buckets
    gain = {p: len(v) for p, v in covers.items()}
    buckets = {}
    for p, g in gain.items():
        buckets.setdefault(g, set()).add(p)
    alive, done, chosen = set(covers), [False] * len(cons), []
    left = len(cons) - len(uncoverable)

    def move(p, g):
        if gain[p] in buckets:
            buckets[gain[p]].discard(p)
        gain[p] = g
        if g > 0:
            buckets.setdefault(g, set()).add(p)

    while left > 0:
        top = max((g for g, s in buckets.items() if s), default=0)
        if top == 0:
            break

        def key(p):
            wired = any(_dist(p, q) <= spec["reach"] - slack + EPS for q in chosen)
            return (0 if wired or not chosen else 1, round(_dist(p, anchor), 6), p[1], p[0])
        p = min(buckets[top], key=key)
        chosen.append(p)
        for i in covers[p]:
            if not done[i]:
                done[i] = True
                left -= 1
                for q in by_cons[i]:
                    if q in alive:
                        move(q, gain[q] - 1)
        for dx in range(-size + 1, size):
            for dy in range(-size + 1, size):
                q = (p[0] + dx, p[1] + dy)
                if q in alive:
                    alive.discard(q)
                    move(q, 0)

    # 3. prune poles whose consumers are all covered by others
    count = [0] * len(cons)
    for p in chosen:
        for i in covers[p]:
            count[i] += 1
    for p in list(reversed(chosen)):
        if all(count[i] >= 2 for i in covers[p]):
            chosen.remove(p)
            for i in covers[p]:
                count[i] -= 1
    chosen.sort(key=lambda p: (round(_dist(p, anchor), 6), p[1], p[0]))
    ents = [_entity(spec, p[0], p[1], "cover") for p in chosen]
    if uncoverable:
        return {"ok": False, "reason": f"{len(uncoverable)} consumers have no free pole position in reach",
                "uncoverable": uncoverable, "entities": ents}

    # 4. join wire groups
    for e in ents:
        taken.update(_foot(e["x"], e["y"], e["size"]))
    joins = 0
    while True:
        nodes = [(e["x"], e["y"], e["reach"]) for e in ents]
        comps = _groups(nodes, slack)
        if len(comps) <= 1:
            break
        main = comps[0]  # holds ents[0], the pole nearest the anchor
        rest = [k for c in comps[1:] for k in c]
        nearest = min(rest, key=lambda k: (min(_dist(nodes[k], nodes[m]) for m in main), k))
        other = next(c for c in comps if nearest in c)
        path, why, _ = _chain([nodes[m] for m in main], [nodes[k] for k in other], jspec, taken, slack,
                              margin=margin, budget=budget)
        if path is None:
            return {"ok": False, "reason": f"cannot wire the cover poles into one group: {why}",
                    "entities": ents, "uncoverable": []}
        for x, y in path:
            ents.append(_entity(jspec, x, y, "join"))
            taken.update(_foot(x, y, jspec["size"]))
            joins += 1
    return {"ok": True, "entities": ents, "poles": len(ents), "cover_poles": len(ents) - joins,
            "join_poles": joins}


def link(group, grid_poles, blocked=(), *, reach_pole=None, consumers=(), slack=0.5, candidates=8,
         margin=None, budget=4000):
    """Fewest `reach_pole` poles wiring `group` to an existing grid pole.

    group: the new poles (cover()'s entities, or (x, y, reach) / {x, y, reach} items).
    grid_poles: existing poles of the grid, (x, y, reach[, supply, size]) or dicts with x/y or
        position, reach, optional supply/size. Only the `candidates` nearest to the group are
        tried (1 = strictly the nearest one); the one needing the fewest new poles wins.
    reach_pole: spec of the link poles (kc.poles().reach, a big pole); default: the group's own
        pole spec.
    consumers: their tiles are kept free too. blocked: tiles no pole may cover.
    margin: how far the chain may wander outside the group-target box (default: one reach);
        raise it to go around a long wall.

    Returns {"ok": True, "entities": [... role "link"], "poles": n, "target": [x, y, reach],
    "source": [x, y, reach]} or {"ok": False, "reason"}.
    """
    nodes = [_node(p) for p in group]
    grid = [_gridpole(g) for g in grid_poles]
    if not nodes:
        return {"ok": False, "reason": "empty group"}
    if not grid:
        return {"ok": False, "reason": "no grid poles given"}
    if reach_pole is None:
        g0 = group[0]
        reach_pole = {"name": g0.get("name"), "size": g0.get("size", 1), "supply": g0.get("supply", 0.0),
                      "reach": g0.get("reach")} if isinstance(g0, dict) else None
    spec = _spec(reach_pole, BIG_POLE)
    taken = {_tile(b) for b in blocked}
    for c in consumers:
        taken.update(_foot(*_consumer(c)))
    for p in group:
        if isinstance(p, dict):
            taken.update(_foot(*_xy(p), int(p.get("size", 1))))
        else:
            taken.update(_foot(float(p[0]), float(p[1]), 1))
    for g in grid:
        taken.update(_foot(g[0], g[1], g[4]))
    grid.sort(key=lambda g: (min(_dist(g, n) for n in nodes), g[1], g[0]))
    targets = [(g[0], g[1], g[2]) for g in grid[:max(1, int(candidates))]]
    path, t, s = _chain(nodes, targets, spec, taken, slack, margin=margin, budget=budget)
    if path is None:
        return {"ok": False, "reason": t}
    return {"ok": True, "entities": [_entity(spec, x, y, "link") for x, y in path], "poles": len(path),
            "target": list(t), "source": list(s)}


def plan(consumers, pole=None, blocked=(), grid_poles=(), *, reach_pole=None, slack=0.5, **kw):
    """cover() then, if grid_poles are given, link() the group to the grid. One call for Kira.
    Returns cover()'s dict with the link poles appended to "entities" and "link_poles", "target"."""
    anchor = kw.pop("anchor", None)
    if anchor is None and grid_poles:
        cons = [_consumer(c) for c in consumers]
        cx = sum(c[0] for c in cons) / max(1, len(cons))
        cy = sum(c[1] for c in cons) / max(1, len(cons))
        g = min((_gridpole(g) for g in grid_poles), key=lambda g: (_dist(g, (cx, cy)), g[1], g[0]))
        anchor = (g[0], g[1])
    r = cover(consumers, pole, blocked, anchor=anchor, slack=slack, **kw)
    if not r["ok"] or not grid_poles or not r["entities"]:
        r["link_poles"] = 0
        return r
    lk = link(r["entities"], grid_poles, blocked, reach_pole=reach_pole, consumers=consumers, slack=slack,
              margin=kw.get("margin"), budget=kw.get("budget", 4000))
    if not lk["ok"]:
        return dict(r, ok=False, reason=f"cannot link to the grid: {lk['reason']}", link_poles=0)
    r["entities"] = r["entities"] + lk["entities"]
    r["poles"] = len(r["entities"])
    r["link_poles"] = lk["poles"]
    r["target"] = lk["target"]
    return r


def check(consumers, poles, grid_poles=(), blocked=(), slack=0.0):
    """Problems with a pole plan as a list of strings ([] = valid).

    poles: the NEW poles, dicts with x, y, reach, supply, size (cover()/link() entities).
    grid_poles: existing poles, assumed to be one network: every new pole must wire to one of
        them through new poles. Without grid poles, the new poles must form one group.
    Checks: each pole on its size's grid; no pole tile blocked, on a consumer, or shared with
    another pole (new or existing, sizes from the dicts); every consumer inside some pole's
    supply square (existing poles count when they give a supply); wiring by the shorter-reach
    rule with `slack` (0 = exactly the game's rule)."""
    cons = [_consumer(c) for c in consumers]
    grid = [_gridpole(g) for g in grid_poles]
    blocked = {_tile(b) for b in blocked}
    problems, owner = [], {}
    for i, c in enumerate(cons):
        for t in _foot(*c):
            owner.setdefault(t, f"consumer #{i}")
    for j, g in enumerate(grid):
        for t in _foot(g[0], g[1], g[4]):
            owner.setdefault(t, f"grid pole #{j}")
    new = []
    for i, p in enumerate(poles):
        x, y = _xy(p)
        size, supply, reach = int(p.get("size", 1)), float(p.get("supply", 0.0)), float(p["reach"])
        name = p.get("name", "pole")
        new.append((x, y, reach, supply, size))
        o = _off(size)
        if abs((x - o) - round(x - o)) > EPS or abs((y - o) - round(y - o)) > EPS:
            want = "tile centers (x.5)" if o else "integer coordinates"
            problems.append(f"pole #{i} {name} at {[x, y]}: size {size} must sit on {want}")
        for t in _foot(x, y, size):
            if t in blocked:
                problems.append(f"pole #{i} {name} at {[x, y]} covers blocked tile {list(t)}")
            if t in owner:
                problems.append(f"pole #{i} {name} at {[x, y]} overlaps {owner[t]} at {list(t)}")
            else:
                owner[t] = f"pole #{i}"
    supplies = [n for n in new + grid if n[3] > 0]
    for i, c in enumerate(cons):
        if not any(_covers(n[0], n[1], n[3], c) for n in supplies):
            problems.append(f"consumer #{i} at {[c[0], c[1]]} is outside every pole's supply area")
    nodes = grid + new
    seeds = list(range(len(grid))) if grid else ([len(grid)] if new else [])
    linked, todo = set(seeds), list(seeds)
    while todo:
        k = todo.pop()
        for j in range(len(nodes)):
            if j not in linked and _links(nodes[k], nodes[j], slack):
                linked.add(j)
                todo.append(j)
    for i, n in enumerate(new):
        if len(grid) + i not in linked:
            where = "the grid" if grid else "the other new poles"
            problems.append(f"pole #{i} at {[n[0], n[1]]} is not wired to {where}")
    return problems
