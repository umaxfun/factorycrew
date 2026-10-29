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

The generic parts live in the kc library: candidate positions, the greedy cover and the prune in
kc.cover; the link rule, wire groups, the A* chain and group joining in kc.wires. This file keeps
what is specific to poles: specs, input forms, roles, and check().

Written for factorycrew (MIT) from an idea only: greedy set cover + bridging groups in wire-reach
hops as in demipixel/factorio-generators (power.js) and Ben-Ramchandani/PlannerCore; neither has a
usable license, so no code was read into or copied from them.
"""
from __future__ import annotations

import math

try:  # factorio_compute: the kc package sits next to this file
    import kc  # noqa: F401
except ImportError:  # the repo: skills/power/lib -> mcp/kc
    import os as _os
    import sys as _sys
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _sys.path.insert(0, _os.path.abspath(_os.path.join(_here, "..", "..", "..", "mcp")))
from kc import cover as _kcover  # noqa: E402
from kc import wires as _kwires  # noqa: E402
from kc.grid import Grid as _Grid  # noqa: E402

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


def _foot(x, y, w, h=None):
    h = w if h is None else h
    x0, y0 = x - w / 2, y - h / 2
    return [(math.floor(x0 + EPS) + i + 0.5, math.floor(y0 + EPS) + j + 0.5)
            for i in range(int(round(w))) for j in range(int(round(h)))]


def _box(c):
    """Tile box (x0, y0, x1, y1) of a consumer (x, y, w, h)."""
    cx, cy, w, h = c
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _covers(px, py, supply, c):
    return _kcover.area_overlaps(_box(c), (px, py), supply)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _entity(spec, x, y, role):
    return {"name": spec["name"], "x": x, "y": y, "size": spec["size"], "supply": spec["supply"],
            "reach": spec["reach"], "role": role}


def _node(p):
    """(x, y, reach) of a planned pole entity or an existing pole."""
    if isinstance(p, dict) and "reach" not in p and "wire_reach" not in p:
        raise ValueError(f"pole needs a reach: {p}")
    g = _gridpole(p)
    return (g[0], g[1], g[2])


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
    size, supply = spec["size"], spec["supply"]

    # 1. candidates: free positions whose supply square reaches a consumer (kc.cover)
    covers = _kcover.candidates_around([_box(c) for c in cons], supply, _Grid(blocked=taken), size)
    reached = {i for v in covers.values() for i in v}
    uncoverable = [i for i in range(len(cons)) if i not in reached]

    # 2. greedy max coverage (kc.cover.greedy); ties: wires to a taken pole, near the anchor, y, x
    reach = spec["reach"]

    def key(p, chosen):
        wired = any(_kwires.can_link((p[0], p[1], reach), (q[0], q[1], reach), slack) for q in chosen)
        return (0 if wired or not chosen else 1, round(_dist(p, anchor), 6), p[1], p[0])

    def overlapping(p):  # positions whose footprint overlaps a pole at p
        return [(p[0] + dx, p[1] + dy) for dx in range(-size + 1, size) for dy in range(-size + 1, size)]

    chosen = _kcover.greedy(range(len(cons)), covers, tie=key, conflicts=overlapping)["chosen"]

    # 3. prune poles whose consumers are all covered by others
    chosen = _kcover.prune(chosen, covers)
    chosen.sort(key=lambda p: (round(_dist(p, anchor), 6), p[1], p[0]))
    ents = [_entity(spec, p[0], p[1], "cover") for p in chosen]
    if uncoverable:
        return {"ok": False, "reason": f"{len(uncoverable)} consumers have no free pole position in reach",
                "uncoverable": uncoverable, "entities": ents}

    # 4. join wire groups (kc.wires.join_groups): the group holding ents[0], the pole nearest the
    #    anchor, is chained to the nearest other group until one group remains
    for e in ents:
        taken.update(_foot(e["x"], e["y"], e["size"]))
    j = _kwires.join_groups([[(e["x"], e["y"], e["reach"]) for e in ents]], jspec["reach"],
                            _Grid(blocked=taken), size=jspec["size"], slack=slack, margin=margin,
                            budget=budget)
    ents += [_entity(jspec, x, y, "join") for x, y in j["relays"]]
    if not j["ok"]:
        return {"ok": False, "reason": f"cannot wire the cover poles into one group: {j['reason']}",
                "entities": ents, "uncoverable": []}
    return {"ok": True, "entities": ents, "poles": len(ents), "cover_poles": len(ents) - j["count"],
            "join_poles": j["count"]}


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
    c = _kwires.chain(nodes, targets, spec["reach"], _Grid(blocked=taken), size=spec["size"], slack=slack,
                      margin=margin, budget=budget)
    if not c["ok"]:
        return {"ok": False, "reason": c["reason"]}
    return {"ok": True, "entities": [_entity(spec, x, y, "link") for x, y in c["relays"]],
            "poles": c["count"], "target": c["target"], "source": c["source"]}


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
    loose = _kwires.unwired([n[:3] for n in new], [g[:3] for g in grid], slack)
    for i in loose:
        n = new[i]
        where = "the grid" if grid else "the other new poles"
        problems.append(f"pole #{i} at {[n[0], n[1]]} is not wired to {where}")
    return problems
