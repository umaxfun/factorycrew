"""Beacon placer for factorio_compute (`import beacons`).

Places beacons on free tiles around machines so that each beacon helps as many machines as it
can. Greedy, as in OilOutpostPlanner's `FindBeaconLocations` (layout.lua): list every free
beacon position that reaches at least one machine, score each by its MARGINAL gain under the
2.0 beacon profile (a machine with n beacons gets n * profile[n] beacons' worth of effect, the
last profile entry is used past its end), take the best, update the machines' counts, drop the
positions it now overlaps, rescore, and stop when the best gain falls below `min_utility` or
`max_beacons` is reached. Added here: an optional first phase that serves `min_per_machine`
(pick the position that lifts the most machines still below the minimum), exact reach from
the beacon's supply area and the machine's collision box, any machine size per machine, beacons
that already stand there, deterministic tie-breaking (gain, then y, then x), and `check()`.

Reach: a beacon's supply area is its tile footprint grown by `supply_area_distance` on each
side; a machine is affected when its collision box overlaps that area. The collision box
defaults to the footprint shrunk by 0.3 per side (vanilla 3x3 machines: +-1.2), or pass it.

Tiles are tile centers (x.5, y.5). Odd-sized beacons sit on tile centers, even-sized ones on
integers. Ported from Coppermine-factorio/OilOutpostPlanner (MIT), see SOURCE.md.
"""
from __future__ import annotations

import math


def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _tile(p):
    x, y = _xy(p)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def _size(s):
    if isinstance(s, (list, tuple)):
        return int(s[0]), int(s[1])
    return int(s), int(s)


def footprint(x, y, w, h):
    """Tile centers covered by a w x h entity centered at (x, y)."""
    x0, y0 = x - w / 2, y - h / 2
    return {(math.floor(x0 + 1e-9) + i + 0.5, math.floor(y0 + 1e-9) + j + 0.5) for i in range(w) for j in range(h)}


def _machine(m, inset):
    """(x, y, w, h, hx, hy): center, tile size and collision half-sizes."""
    if isinstance(m, dict):
        x, y = _xy(m)
        w = int(m.get("w", m.get("tile_width", 3)))
        h = int(m.get("h", m.get("tile_height", 3)))
        cb = m.get("collision_box")
    else:
        x, y = float(m[0]), float(m[1])
        w = int(m[2]) if len(m) > 2 else 3
        h = int(m[3]) if len(m) > 3 else w
        cb = None
    if cb:
        if isinstance(cb, dict):  # {left_top = {x, y}, right_bottom = {x, y}} as Lua returns it
            lt, rb = _xy(cb["left_top"]), _xy(cb["right_bottom"])
        else:
            lt, rb = _xy(cb[0]), _xy(cb[1])
        hx, hy = (rb[0] - lt[0]) / 2, (rb[1] - lt[1]) / 2
    else:
        hx, hy = w / 2 - inset, h / 2 - inset
    return (x, y, w, h, hx, hy)


def effect_of(n, profile=None):
    """Beacons' worth of effect a machine gets from n beacons: n * profile[n] (1-based)."""
    if n <= 0:
        return 0.0
    profile = list(profile or [1.0])
    return n * float(profile[min(n, len(profile)) - 1])


def transmitted(module_effects, distribution_effectivity=1.5):
    """Effect ONE beacon passes on before the profile: the sum of its modules' effects times
    distribution_effectivity. module_effects: one {speed, consumption, ...} dict per module."""
    out = {}
    for e in module_effects:
        for k, v in sorted(e.items()):
            out[k] = out.get(k, 0.0) + float(v) * distribution_effectivity
    return out


def _reaches(bx, by, ax, ay, m):
    return abs(m[0] - bx) < ax + m[4] - 1e-9 and abs(m[1] - by) < ay + m[5] - 1e-9


def _grid(v, size):
    return math.floor(v) + 0.5 if size % 2 else float(math.floor(v + 0.5))


def place(machines, blocked=(), *, beacon="beacon", size=3, supply_area_distance=3, profile=None,
          beacon_effect=None, min_utility=0.5, min_per_machine=0, max_beacons=None, existing=(),
          collision_inset=0.3):
    """Greedy beacon layout around `machines`.

    machines: [{x, y, w, h, collision_box?}] or (x, y, w, h); w/h default 3. Their tiles are blocked.
    blocked: other occupied tiles (belts, inserters, poles, water ...) as tile centers.
    size: beacon tile size (int or (w, h)); supply_area_distance: from the prototype
        (get_supply_area_distance(quality) in 2.0). profile: the beacon prototype's `profile`
        list (None = [1], no diminishing). beacon_effect: optional {speed, consumption, ...} that
        ONE beacon transmits (see transmitted()); used to report each machine's effects.
    min_utility: stop when the best position adds less than this many beacons' worth (summed
        over the machines it reaches, after the profile). With the 2.0 profile 1/sqrt(n): 0.5
        fills a row of assemblers to 8 beacons in the middle, 0.1 to everything that fits;
        2.0 (upstream's default) keeps only beacons shared by two or more fresh machines.
    min_per_machine: first phase: keep adding beacons for machines below this count while a
        free position reaches one of them; `short` lists the ones that could not get there.
    max_beacons: cap on beacons placed. existing: beacons already built, [{x, y}]: they count
        and block.

    Returns {"ok": True, "entities": [{name, x, y, direction}], "beacons": n,
    "machines": [{x, y, beacons, effect, effects?}], "total_effect", "short": [machine index]}.
    Place with kc.place_ghosts(surface, result["entities"]).
    """
    bw, bh = _size(size)
    d = float(supply_area_distance)
    ax, ay = bw / 2 + d, bh / 2 + d
    ms = [_machine(m, collision_inset) for m in machines]
    occ = {_tile(b) for b in blocked}
    for m in ms:
        occ |= footprint(m[0], m[1], m[2], m[3])
    counts = [0] * len(ms)
    for b in existing:
        bx, by = _xy(b)
        occ |= footprint(bx, by, bw, bh)
        for i, m in enumerate(ms):
            if _reaches(bx, by, ax, ay, m):
                counts[i] += 1

    # every free position that reaches at least one machine, and the machines it reaches
    cands = {}
    for m in ms:
        rx, ry = ax + m[4], ay + m[5]
        x = _grid(m[0] - rx, bw)
        while x <= m[0] + rx:
            y = _grid(m[1] - ry, bh)
            while y <= m[1] + ry:
                if (x, y) not in cands:
                    cands[(x, y)] = None
                y += 1
            x += 1
    live = {}
    for p in sorted(cands, key=lambda t: (t[1], t[0])):
        if footprint(p[0], p[1], bw, bh) & occ:
            continue
        hits = [i for i, m in enumerate(ms) if _reaches(p[0], p[1], ax, ay, m)]
        if hits:
            live[p] = hits

    def gain(hits):
        return sum(effect_of(counts[i] + 1, profile) - effect_of(counts[i], profile) for i in hits)

    need = int(min_per_machine or 0)
    chosen = []
    cap = math.inf if max_beacons is None else int(max_beacons)
    while live and len(chosen) < cap:
        under = {i for i, c in enumerate(counts) if c < min_per_machine}
        best, best_key = None, None
        for p, hits in live.items():
            g = gain(hits)
            lift = sum(1 for i in hits if i in under)
            key = (lift, g, -p[1], -p[0]) if under else (g, -p[1], -p[0])
            if best_key is None or key > best_key:
                best, best_key = p, key
        if under and best_key[0] == 0:
            # nothing reaches the machines still short: continue with the plain greedy
            min_per_machine = 0
            continue
        if not under and best_key[0] < min_utility - 1e-9:
            break
        chosen.append(best)
        for i in live[best]:
            counts[i] += 1
        live = {p: h for p, h in live.items() if not (abs(p[0] - best[0]) < bw and abs(p[1] - best[1]) < bh)}

    res = evaluate(chosen, machines, size=size, supply_area_distance=d, profile=profile,
                   beacon_effect=beacon_effect, existing=existing, collision_inset=collision_inset)
    return {"ok": True,
            "entities": [{"name": beacon, "x": p[0], "y": p[1], "direction": 0} for p in chosen],
            "beacons": len(chosen), "machines": res["machines"], "total_effect": res["total_effect"],
            "short": [i for i, c in enumerate(counts) if c < need]}


def evaluate(beacons, machines, *, size=3, supply_area_distance=3, profile=None, beacon_effect=None,
             existing=(), collision_inset=0.3):
    """Per machine: how many of `beacons` (+ `existing`) reach it, and its effect after the
    profile. Pure geometry; use it on any layout, e.g. a template row."""
    bw, bh = _size(size)
    ax, ay = bw / 2 + float(supply_area_distance), bh / 2 + float(supply_area_distance)
    pts = [_xy(b) for b in list(existing) + list(beacons)]
    out, total = [], 0.0
    for m in machines:
        mm = _machine(m, collision_inset)
        n = sum(1 for bx, by in pts if _reaches(bx, by, ax, ay, mm))
        e = effect_of(n, profile)
        total += e
        row = {"x": mm[0], "y": mm[1], "beacons": n, "effect": round(e, 6)}
        if beacon_effect:
            row["effects"] = {k: round(float(v) * e, 6) for k, v in sorted(beacon_effect.items())}
        out.append(row)
    return {"machines": out, "total_effect": round(total, 6)}


def check(plan, machines, blocked=(), *, size=3, supply_area_distance=3, profile=None, existing=(),
          min_per_machine=0, collision_inset=0.3):
    """Problems with a beacon layout as a list of strings ([] = valid).

    plan: the dict place() returned, or just a list of beacon entities/positions. Verifies each
    beacon sits on its grid, overlaps no other beacon, machine, existing beacon or blocked
    tile; and, when plan is a place() result, that its per-machine counts and effects match
    the geometry recomputed here and that every machine not in `short` has min_per_machine."""
    ents = plan["entities"] if isinstance(plan, dict) else list(plan)
    bw, bh = _size(size)
    ms = [_machine(m, collision_inset) for m in machines]
    occ = {}
    for t in {_tile(b) for b in blocked}:
        occ[t] = "a blocked tile"
    for i, m in enumerate(ms):
        for t in footprint(m[0], m[1], m[2], m[3]):
            occ[t] = f"machine #{i}"
    for j, b in enumerate(existing):
        bx, by = _xy(b)
        for t in footprint(bx, by, bw, bh):
            occ[t] = f"existing beacon #{j}"
    problems = []
    for i, e in enumerate(ents):
        x, y = _xy(e)
        if abs(x - _grid(x, bw)) > 1e-9 or abs(y - _grid(y, bh)) > 1e-9:
            problems.append(f"beacon #{i} at {[x, y]} is off the {bw}x{bh} grid")
        for t in sorted(footprint(x, y, bw, bh)):
            if t in occ:
                problems.append(f"beacon #{i} at {[x, y]} overlaps {occ[t]} at {list(t)}")
                break
        for t in footprint(x, y, bw, bh):
            occ.setdefault(t, f"beacon #{i}")
    if isinstance(plan, dict) and "machines" in plan:
        got = evaluate(ents, machines, size=size, supply_area_distance=supply_area_distance,
                       profile=profile, existing=existing, collision_inset=collision_inset)["machines"]
        for i, (a, b) in enumerate(zip(plan["machines"], got)):
            if a["beacons"] != b["beacons"] or abs(a["effect"] - b["effect"]) > 1e-6:
                problems.append(f"machine #{i}: plan says {a['beacons']} beacons / {a['effect']}, "
                                f"geometry gives {b['beacons']} / {b['effect']}")
        short = set(plan.get("short", []))
        for i, b in enumerate(got):
            if b["beacons"] < min_per_machine and i not in short:
                problems.append(f"machine #{i} has {b['beacons']} beacons < {min_per_machine} and is not listed as short")
    return problems
