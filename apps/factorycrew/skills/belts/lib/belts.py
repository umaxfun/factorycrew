"""Belt router for factorio_compute (`import belts`).

Lays a belt between two tiles around blocked tiles. A* over states (tile, heading): a state is
a belt (or underground exit) on that tile whose output points `heading`. From a state the next
tile is fixed (tile + heading); there the route places a straight belt, a curve (left/right,
plus `turn_cost`), or the entrance of an underground pair that comes out 2..max_underground
tiles further on the same axis (cost = all tiles advanced + `underground_cost`), jumping over
whatever lies between. Manhattan distance is the heuristic, so the route is the cheapest one.

Tiles are tile centers (x.5, y.5), like kc_compute. Directions are Factorio 2.0 numbers:
north 0, east 4, south 8, west 12. Underground pairs are emitted as two entities with the same
direction and type "input" / "output", the way the game and blueprints store them.

Written for factorycrew (MIT). The idea of an underground pair as a single search move is
common to belt planners (auto-belt, BeltRouter); no code was copied from them.
"""
from __future__ import annotations

import heapq
import math

STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
NAMES = {0: "north", 4: "east", 8: "south", 12: "west"}


def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _tile(p):
    x, y = _xy(p)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def _dir(d):
    if d is None:
        return None
    if isinstance(d, str):
        return {v: k for k, v in NAMES.items()}[d.lower()]
    d = int(d) % 16
    if d not in STEP:
        raise ValueError(f"direction must be a cardinal 0/4/8/12, got {d}")
    return d


def _left(d):
    return (d - 4) % 16


def _right(d):
    return (d + 4) % 16


def _axis(d):
    return d % 8  # 0: north-south, 4: east-west


def _move(p, d, k=1):
    dx, dy = STEP[d]
    return (p[0] + dx * k, p[1] + dy * k)


def route(start, goal, blocked=(), *, belt="transport-belt", underground="underground-belt",
          max_underground=5, start_dir=None, goal_dir=None, turn_cost=0.5, underground_cost=1.5,
          undergrounds=(), budget=200_000, margin=32):
    """Cheapest belt route from `start` to `goal` (both get a belt).

    blocked: tiles nothing may be placed on (entities, ghosts, water, cliffs).
    max_underground: the underground prototype's max_underground_distance (5 for the basic
        one): the exit may be at most that many tiles from the entrance. 0 or None: no undergrounds.
    start_dir / goal_dir: required output direction of the first / last belt, or None for any.
        goal_dir is how the last belt must point, e.g. into a machine's input.
    undergrounds: existing undergrounds of the SAME tier, as {x, y, direction} dicts or
        (x, y, direction) tuples. A new pair may not span one of them on the same axis: the game
        would pair the entrance with that one instead.
    margin: how far the search may wander outside the start-goal box, in tiles.

    Returns {"ok": True, "entities": [{name, x, y, direction, type?}, ...] in flow order,
    "belts", "undergrounds" (pairs), "turns", "cost", "expanded"} or
    {"ok": False, "reason": ..., "expanded"}.
    Place the entities with kc.place_ghosts(surface, result["entities"]) as they are.
    """
    blocked = {_tile(b) for b in blocked}
    s, g = _tile(start), _tile(goal)
    sd, gd = _dir(start_dir), _dir(goal_dir)
    maxu = int(max_underground or 0)
    for name, t in (("start", s), ("goal", g)):
        if t in blocked:
            return {"ok": False, "reason": f"{name} tile {list(t)} is blocked", "expanded": 0}
    foreign = {}
    for u in undergrounds:
        if isinstance(u, dict):
            t, d = _tile(u), _dir(u.get("direction", 0))
        else:
            t, d = _tile((u[0], u[1])), _dir(u[2])
        foreign.setdefault(t, set()).add(_axis(d))

    x0, x1 = min(s[0], g[0]) - margin, max(s[0], g[0]) + margin
    y0, y1 = min(s[1], g[1]) - margin, max(s[1], g[1]) + margin

    def free(t):
        return t not in blocked and x0 <= t[0] <= x1 and y0 <= t[1] <= y1

    def h(t):
        return abs(t[0] - g[0]) + abs(t[1] - g[1])

    def done(state):
        return state[0] == g and (gd is None or state[1] == gd)

    frontier, best, came = [], {}, {}
    tie = 0
    for d in ([sd] if sd is not None else list(STEP)):
        st = (s, d)
        best[st] = 0.0
        came[st] = None
        heapq.heappush(frontier, (h(s), tie, 0.0, st))
        tie += 1

    expanded = 0
    while frontier:
        _, _, cost, st = heapq.heappop(frontier)
        if cost > best.get(st, math.inf):
            continue
        if done(st):
            return _build(st, came, cost, expanded, belt, underground)
        expanded += 1
        if expanded > budget:
            return {"ok": False, "reason": f"search budget of {budget} states spent", "expanded": expanded}
        p, d = st
        q = _move(p, d)
        if not free(q):
            continue
        succ = [((q, d), 1.0, None)]
        succ += [((q, _left(d)), 1.0 + turn_cost, None), ((q, _right(d)), 1.0 + turn_cost, None)]
        for k in range(2, maxu + 1):
            e = _move(q, d, k)
            if not (x0 <= e[0] <= x1 and y0 <= e[1] <= y1):
                break
            span = [_move(q, d, i) for i in range(1, k)]
            if any(_axis(d) in foreign.get(t, ()) for t in span):
                break  # a same-tier underground on this axis: longer jumps cross it too
            if e in blocked:
                continue
            # p -> entrance q -> exit e advances k + 1 tiles: charge them all, or a chain of
            # undergrounds undercuts plain belt (and the heuristic stops being admissible)
            succ.append(((e, d), float(k + 1) + underground_cost, q))
        for nxt, step_cost, entrance in succ:
            c = cost + step_cost
            if c < best.get(nxt, math.inf):
                best[nxt] = c
                came[nxt] = (st, entrance)
                heapq.heappush(frontier, (c + h(nxt[0]), tie, c, nxt))
                tie += 1
    return {"ok": False, "reason": "no route: the goal is walled off within the search area", "expanded": expanded}


def _build(st, came, cost, expanded, belt, underground):
    chain = []
    cur = st
    while cur is not None:
        prev = came[cur]
        chain.append((cur, prev[1] if prev else None))
        cur = prev[0] if prev else None
    chain.reverse()
    ents, pairs, turns = [], 0, 0
    last_dir = None
    for (tile, d), entrance in chain:
        if entrance is not None:
            ents.append({"name": underground, "type": "input", "x": entrance[0], "y": entrance[1], "direction": d})
            ents.append({"name": underground, "type": "output", "x": tile[0], "y": tile[1], "direction": d})
            pairs += 1
        else:
            ents.append({"name": belt, "x": tile[0], "y": tile[1], "direction": d})
        if last_dir is not None and d != last_dir:
            turns += 1
        last_dir = d
    return {"ok": True, "entities": ents, "belts": len(ents) - 2 * pairs, "undergrounds": pairs,
            "turns": turns, "cost": round(cost, 3), "expanded": expanded}


def check(entities, blocked=(), undergrounds=(), max_underground=5):
    """Problems with a route as a list of strings ([] = a valid, connected belt line).

    Verifies: no tile used twice, nothing on a blocked tile, each entity feeds the next one
    (a belt into the next tile, never head-on; an underground input into its output on the same
    axis within max_underground), and no underground pair spans another underground of the
    same name on the same axis (ours or `undergrounds`)."""
    blocked = {_tile(b) for b in blocked}
    problems, seen = [], {}
    for i, e in enumerate(entities):
        t = _tile(e)
        if t in seen:
            problems.append(f"#{i} {e['name']} at {list(t)}: tile already used by #{seen[t]}")
        seen[t] = i
        if t in blocked:
            problems.append(f"#{i} {e['name']} at {list(t)} is on a blocked tile")
    axes = {}
    for u in list(undergrounds) + [e for e in entities if e.get("type") in ("input", "output")]:
        if isinstance(u, dict):
            axes.setdefault((_tile(u), u.get("name", "")), set()).add(_axis(_dir(u.get("direction", 0))))
        else:
            axes.setdefault((_tile((u[0], u[1])), ""), set()).add(_axis(_dir(u[2])))
    for i in range(len(entities) - 1):
        a, b = entities[i], entities[i + 1]
        ta, tb, da, db = _tile(a), _tile(b), _dir(a["direction"]), _dir(b["direction"])
        if a.get("type") == "input":
            if b.get("type") != "output" or db != da:
                problems.append(f"#{i} underground input is not followed by its output")
                continue
            k = abs(tb[0] - ta[0]) + abs(tb[1] - ta[1])
            if _move(ta, da, int(k)) != tb or not 2 <= k <= max_underground:
                problems.append(f"#{i} underground pair {list(ta)}->{list(tb)} is off-axis or out of reach")
                continue
            for j in range(1, int(k)):
                t = _move(ta, da, j)
                for (ut, name), ax in axes.items():
                    if ut == t and _axis(da) in ax and (not name or name == a["name"]):
                        problems.append(f"#{i} underground pair spans another underground at {list(t)}")
        else:
            if _move(ta, da) != tb:
                problems.append(f"#{i} {a['name']} at {list(ta)} points {NAMES[da]}, but the next entity is at {list(tb)}")
            elif (db - da) % 16 == 8:
                problems.append(f"#{i} belts at {list(ta)} and {list(tb)} face each other")
            if b.get("type") == "input" and db != da:
                problems.append(f"#{i + 1} underground input must be entered from behind")
    return problems
