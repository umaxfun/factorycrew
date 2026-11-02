"""Ore mining outpost planner for factorio_compute (`import mining`).

Lays electric (or any) mining drills over an ore patch in the classic paired-row layout: two rows of
drills face a shared belt between them, pairs repeat with a free gap row for poles, and every lane
side-loads into one collector belt that ends at the patch corner nearest `toward`.

Algorithm, ported from Mining Patch Planner's "simple" layout (layouts/simple.lua, layouts/common.lua):
1. Work in a canonical frame where lanes run along +u and the exit is at the +u/+v corner; the real
   layout is that frame mapped back by a flip/transpose matrix. Both orientations (lanes east-west or
   north-south) are tried.
2. Convolve the ore grid: per drill position, ore tiles under the drill body ("inner") and under its
   mining area ("outer"). MPP uses a separable box filter; here 2D prefix sums give the same counts.
3. Try every lattice offset inside one period (drill size along u, one pair + gap along v). In each
   attempt a drill is placed where inner > ceil((size/2)^2) (MPP `simple_miner_placement`); positions
   with ore only in the outer area are postponed.
4. Score each attempt with MPP's multiplicative heuristic (`simple_layout_heuristic`: inner density,
   1/outer density, centricity, 1/lanes, 0.75^penalty). Take the best; fill in postponed drills that
   reach still-unmined ore (`process_postponed`). If ore is left unmined, try the other attempts in
   score order and keep the first that mines all ore, else the one that leaves least
   (`layout_attempts_fallback`).
5. Belts (not MPP's belt planner): each lane runs from its first drill output to one tile past the
   drills, side-loads into a collector belt along the patch edge, which ends in the exit belt.

Tiles are integer indices internally, tile centers (x.5, y.5) in the output. Directions are
Factorio 2.0 numbers: north 0, east 4, south 8, west 12.

Ported from https://github.com/rimbas/mining-patch-planner (MIT, (c) 2021 Rimbas), commit
3391e3e65332a365c2343c7afa67fcb2449c8c49; see SOURCE.md for what was changed.
"""
from __future__ import annotations

import math

STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
NAMES = {0: "north", 4: "east", 8: "south", 12: "west"}
VEC_DIR = {v: k for k, v in STEP.items()}

DRILL = {"name": "electric-mining-drill", "size": 3, "radius": 2.49, "output": (0.0, -1.85),
         "mining_speed": 0.5}
BELT = {"name": "transport-belt", "speed": 15.0}


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────

def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _idx(p):
    x, y = _xy(p)
    return (math.floor(x), math.floor(y))


def _dir(d):
    if isinstance(d, str):
        words = {v: k for k, v in NAMES.items()}
        if d.lower() in words:
            return words[d.lower()]
    d = int(d) % 16
    if d not in STEP:
        raise ValueError(f"direction must be a cardinal 0/4/8/12, got {d}")
    return d


def _ore(ore):
    """{(i, j): amount} from points, {x, y, amount} dicts or (x, y, amount) tuples."""
    out = {}
    for o in ore:
        amount = 1.0
        if isinstance(o, dict):
            amount = float(o.get("amount", 1) or 1)
        elif len(o) > 2:
            amount = float(o[2])
        out[_idx(o)] = amount
    return out


def _drill(spec):
    d = dict(DRILL)
    d.update(spec or {})
    out = d["output"]
    vec = {}
    if isinstance(out, dict):
        for k, v in out.items():
            vec[_dir(k)] = _xy(v)
    else:  # one vector for a north-facing drill (vector_to_place_result); the game rotates it
        x, y = _xy(out)
        for k in (0, 4, 8, 12):
            vec[k] = (x, y)
            x, y = -y, x
    d["vec"] = vec
    d["size"] = int(d["size"])
    d["radius"] = float(d["radius"])
    return d


def _belt(spec):
    b = dict(BELT)
    b.update(spec or {})
    return b


def _area_range(size, radius):
    """Mining-area tiles relative to the drill's top-left tile: centers strictly inside center ± r."""
    c = size / 2.0
    return math.floor(c - radius - 0.5) + 1, math.ceil(c + radius - 0.5) - 1


# ── frames: canonical (u, v) <-> real (x, y) ────────────────────────────────────────────────────

class _Frame:
    def __init__(self, transpose, fu, fv):
        self.transpose, self.fu, self.fv = transpose, fu, fv

    def vec(self, u, v):  # canonical vector -> real vector
        u, v = u * self.fu, v * self.fv
        return (v, u) if self.transpose else (u, v)

    def inv(self, x, y):  # real vector -> canonical vector
        u, v = (y, x) if self.transpose else (x, y)
        return u * self.fu, v * self.fv

    def dir(self, u, v):
        x, y = self.vec(u, v)
        return VEC_DIR[(int(x), int(y))]

    def to_c(self, t):  # real tile index -> canonical tile index
        u, v = self.inv(t[0] + 0.5, t[1] + 0.5)
        return (math.floor(u), math.floor(v))

    def to_r(self, u, v):  # canonical continuous point -> real continuous point
        return self.vec(u, v)


def _frames(ore, toward, orientation):
    xs = [t[0] for t in ore]
    ys = [t[1] for t in ore]
    cx, cy = (min(xs) + max(xs) + 1) / 2.0, (min(ys) + max(ys) + 1) / 2.0
    tx, ty = _xy(toward) if toward is not None else (cx, cy)
    sx = -1 if tx < cx else 1
    sy = -1 if ty < cy else 1
    frames = []
    if orientation in ("auto", "horizontal"):
        frames.append(("horizontal", _Frame(False, sx, sy)))
    if orientation in ("auto", "vertical"):
        frames.append(("vertical", _Frame(True, sy, sx)))
    if not frames:
        raise ValueError("orientation must be auto, horizontal or vertical")
    return frames


class _Sum:
    """2D prefix sums over a box: O(1) rectangle sums (stands in for MPP's convolution)."""

    def __init__(self, cells, u0, v0, u1, v1):
        self.u0, self.v0, self.w, self.h = u0, v0, u1 - u0 + 1, v1 - v0 + 1
        w = self.w
        s = [[0.0] * (w + 1) for _ in range(self.h + 1)]
        for j in range(self.h):
            row, prev, acc = s[j + 1], s[j], 0.0
            for i in range(w):
                acc += cells.get((u0 + i, v0 + j), 0.0)
                row[i + 1] = prev[i + 1] + acc
        self.s = s

    def rect(self, ua, ub, va, vb):
        ua, ub = max(ua - self.u0, 0), min(ub - self.u0, self.w - 1)
        va, vb = max(va - self.v0, 0), min(vb - self.v0, self.h - 1)
        if ua > ub or va > vb:
            return 0.0
        s = self.s
        return s[vb + 1][ub + 1] - s[va][ub + 1] - s[vb + 1][ua] + s[va][ua]


# ── heuristics (MPP layouts/common.lua) ─────────────────────────────────────────────────────────

def _simple_score(h):
    # MPP: 1 / (1 + ceil(drill rows / 2)) over every lattice row; here the belts actually used, so two
    # half-filled pairs (two belts) lose to one full pair (one belt)
    lane_mult = 1 / (1 + h["lanes"])
    centricity = 1 / (1 + math.log(1 + h["centricity"], 20))
    outer = 1 / (1 + math.log(h["outer_density"] + 1, 10))
    inner = 1 + math.log(h["inner_density"] + 1, 10)
    return outer * inner * centricity * lane_mult * (0.75 ** h["penalty"])


def _full_score(h):
    centricity = 1 / (1 + math.log(1 + h["centricity"], 20))
    outer = 1 + math.log(h["outer_density"] + 1, 10)
    drills = 1 + math.log(h["drills"] ** 0.5, 2)
    return drills * outer * centricity * h["deviation"] * (0.75 ** h["penalty"])


# ── planner ─────────────────────────────────────────────────────────────────────────────────────

def plan(ore, blocked=(), *, toward=None, drill=None, belt=None, avoid=(), orientation="auto",
         mode="simple", pair_gap=1, column_gap=0, mining_time=1.0):
    """Drill rows facing shared belts over an ore patch, all belts ending in one exit.

    ore: ore tiles of ONE resource, as points, {x, y, amount} or (x, y, amount).
    blocked: tiles nothing may be built on (entities, water, cliffs). Belts may cross ore.
    toward: where the ore should go (the base, a train stop): the exit is at the patch corner on
        that side. None: the +x/+y corner.
    drill: {name, size, radius (mining_drill_radius), output (vector_to_place_result of a north-facing
        drill, or {direction: vector}), mining_speed}. Defaults: vanilla electric mining drill.
    belt: {name, speed (items/s)}. Default: transport-belt, 15/s.
    avoid: tiles of OTHER resources: no drill's mining area may touch them (no mixed ore).
    orientation: "auto" (try both), "horizontal" (lanes east-west) or "vertical".
    mode: "simple" (MPP default: drills where ore is under the body, fewer drills) or "full"
        (MPP "coverage" / overfill: also drills that only leech from the edge).
    pair_gap: free rows between drill pairs (where poles go); column_gap: free tiles between drills.

    Returns {"ok": True, "entities": drills + belts for kc.place_ghosts, "drills", "belts", "exit",
    "drill_boxes": [[x0, y0, x1, y1], ...], "occupied": tiles, "pole_rows": free rows next to every
    drill row, "stats": {...}} or {"ok": False, "reason": ...}.
    """
    ore_r = _ore(ore)
    if not ore_r:
        return {"ok": False, "reason": "no ore tiles"}
    D, B = _drill(drill), _belt(belt)
    blocked_r = {_idx(b) for b in blocked}
    avoid_r = {_idx(a) for a in avoid}
    if mode not in ("simple", "full"):
        raise ValueError("mode must be simple or full")
    best, reasons = None, []
    for oname, fr in _frames(ore_r, toward, orientation):
        r = _plan_frame(fr, ore_r, blocked_r, avoid_r, D, mode, int(pair_gap), int(column_gap))
        if not r.get("ok"):
            reasons.append(f"{oname}: {r['reason']}")
            continue
        r["orientation"] = oname
        if best is None or (r["unconsumed"], -r["score"]) < (best["unconsumed"], -best["score"]):
            best = r
    if best is None:
        return {"ok": False, "reason": "; ".join(reasons)}
    return _emit(best, ore_r, D, B, float(mining_time))


def _plan_frame(fr, ore_r, blocked_r, avoid_r, D, mode, pair_gap, column_gap):
    s = D["size"]
    ore = {}
    for t, a in ore_r.items():
        ore[fr.to_c(t)] = a
    blocked = {fr.to_c(t) for t in blocked_r}
    avoid = {fr.to_c(t) for t in avoid_r}

    # drill geometry in this frame: the top row faces +v (onto the belt below it), the bottom row -v
    geo = {}
    for kind, cv in (("top", 1), ("bot", -1)):
        rd = fr.dir(0, cv)
        ou, ov = fr.inv(*D["vec"][rd])
        geo[kind] = (rd, math.floor(s / 2.0 + ou), math.floor(s / 2.0 + ov))
    dv_top, dv_bot = geo["top"][2], geo["bot"][2]
    if dv_top < s or dv_bot > -1:
        return {"ok": False, "reason": "the drill's output does not land outside its body in front of it"}
    v1rel = dv_top - dv_bot  # bottom row top-left, relative to the top row's
    period_v = v1rel + s + pair_gap
    period_u = s + column_gap
    lo, hi = _area_range(s, D["radius"])
    us = [t[0] for t in ore]
    vs = [t[1] for t in ore]
    umin, umax, vmin, vmax = min(us), max(us), min(vs), max(vs)
    m = s + hi - lo + period_v + 2
    box = (umin - m, vmin - m, umax + m, vmax + m)
    ones = {t: 1.0 for t in ore}
    S_ore, S_amt = _Sum(ones, *box), _Sum(ore, *box)
    S_blk = _Sum({t: 1.0 for t in blocked}, *box)
    S_av = _Sum({t: 1.0 for t in avoid}, *box) if avoid else None
    ulim = umax - lo + s + 4  # past every possible drill plus room to shift the collector
    lane_block = {}

    def lane_ok(b, u_out):
        if b not in lane_block:
            lb = None
            for u in range(ulim, umin - m - 1, -1):
                if (u, b) in blocked:
                    lb = u
                    break
            lane_block[b] = lb
        lb = lane_block[b]
        return lb is None or u_out > lb

    cap = math.ceil((s / 2.0) ** 2)
    leech1, leech2 = (hi - lo + 1) ** 2 * 0.20, s * s

    def placeable(inner, outer):
        if mode == "full":
            return inner > 0 or outer > leech1 or outer > leech2
        return inner > cap

    cu = (umin + umax + 1) / 2.0
    cvv = (vmin + vmax + 1) / 2.0
    attempts = []
    for sv in range(period_v):
        for su in range(period_u):
            miners, postponed = [], []
            v0 = vmin - v1rel - hi + sv - period_v
            line = 0
            first_line = None
            while v0 + lo <= vmax:
                b = v0 + dv_top
                for kind, v in (("top", v0), ("bot", v0 + v1rel)):
                    if v + hi < vmin or v + lo > vmax:
                        continue
                    line += 1
                    if first_line is None:
                        first_line = line
                    rd, du, _ = geo[kind]
                    col = 0
                    u = umin - hi + su
                    while u + lo <= umax:
                        col += 1
                        outer = S_ore.rect(u + lo, u + hi, v + lo, v + hi)
                        if outer > 0 and S_blk.rect(u, u + s - 1, v, v + s - 1) == 0 \
                                and (S_av is None or S_av.rect(u + lo, u + hi, v + lo, v + hi) == 0) \
                                and lane_ok(b, u + du):
                            inner = S_ore.rect(u, u + s - 1, v, v + s - 1)
                            mnr = {"u": u, "v": v, "kind": kind, "dir": rd, "b": b, "out": u + du,
                                   "inner": inner, "outer": outer, "col": col, "line": line,
                                   "amount": S_amt.rect(u + lo, u + hi, v + lo, v + hi)}
                            (miners if placeable(inner, outer) else postponed).append(mnr)
                        u += period_u
                v0 += period_v
            attempts.append(_score(miners, postponed, su, sv, s, mode, cu, cvv, first_line))
    attempts.sort(key=lambda a: (-a["score"], a["sv"], a["su"]))
    chosen = None
    for a in attempts:
        _postponed(a, ore, lo, hi)
        if a["unconsumed"] == 0:
            chosen = a
            break
    if chosen is None:
        chosen = min(attempts, key=lambda a: a["unconsumed"])
    if not chosen["miners"]:
        return {"ok": False, "reason": "no drill position reaches the ore (all blocked or mixed ore)"}
    return _belts(fr, chosen, blocked, ulim, D)


def _score(miners, postponed, su, sv, s, mode, cu, cv, first_line):
    n = len(miners)
    h = {"lanes": len({m["b"] for m in miners}) or 1, "drills": max(n, 1)}
    h["inner_density"] = sum(m["inner"] for m in miners) / max(n, 1)
    h["outer_density"] = sum(m["outer"] for m in miners) / max(n, 1)
    if miners:
        bu = (min(m["u"] for m in miners) + max(m["u"] + s for m in miners)) / 2.0
        bv = (min(m["v"] for m in miners) + max(m["v"] + s for m in miners)) / 2.0
        h["centricity"] = math.hypot(cu - bu, cv - bv)
        biggest = max(m["amount"] for m in miners) or 1.0
        avg = sum(m["amount"] for m in miners) / biggest / n
        h["deviation"] = (sum((m["amount"] / biggest - avg) ** 2 for m in miners) / n) ** 0.5
    else:
        h["centricity"], h["deviation"] = 0.0, 0.0
    # MPP heuristic_penalties: no drills at all, no drill in the first column, none in the first row
    h["penalty"] = (0 if miners else 1) + (0 if any(m["col"] == 1 for m in miners) else 1) \
        + (0 if any(m["line"] == first_line for m in miners) else 1)
    score = (_full_score if mode == "full" else _simple_score)(h)
    return {"miners": miners, "postponed": postponed, "su": su, "sv": sv, "score": score, "h": h,
            "done": False}


def _postponed(a, ore, lo, hi):
    """MPP process_postponed: add postponed drills that still reach unmined ore, best first."""
    if a["done"]:
        return
    consumed = set()

    def area(m):
        return [(u, v) for v in range(m["v"] + lo, m["v"] + hi + 1)
                for u in range(m["u"] + lo, m["u"] + hi + 1) if (u, v) in ore]

    for m in a["miners"]:
        consumed.update(area(m))
    for m in a["postponed"]:
        m["unconsumed"] = sum(1 for t in area(m) if t not in consumed)
    order = sorted(a["postponed"], key=lambda m: (-m["unconsumed"], -m["outer"], -m["inner"], m["v"], m["u"]))
    for m in order:
        tiles = area(m)
        if any(t not in consumed for t in tiles):
            m["postponed"] = True
            a["miners"].append(m)
            consumed.update(tiles)
    a["unconsumed"] = len(ore) - len(consumed)
    a["done"] = True


def _belts(fr, a, blocked, ulim, D):
    s = D["size"]
    miners = a["miners"]
    lanes = {}
    for m in miners:
        lanes.setdefault(m["b"], []).append(m["out"])
    uc0 = max(m["u"] + s for m in miners)
    bs = sorted(lanes)
    belts = []  # (u, v, (du, dv)) in flow order
    if len(bs) == 1:
        b = bs[0]
        for u in range(min(lanes[b]), uc0 + 1):
            belts.append((u, b, (1, 0)))
    else:
        uc = None
        for cand in range(uc0, min(uc0 + 4, ulim) + 1):
            if all((cand, v) not in blocked for v in range(bs[0], bs[-1] + 2)):
                uc = cand
                break
        if uc is None:
            return {"ok": False, "reason": "no free column for the collector belt next to the drills"}
        for b in bs:
            for u in range(min(lanes[b]), uc):
                belts.append((u, b, (1, 0)))
        for v in range(bs[0], bs[-1] + 2):
            belts.append((uc, v, (0, 1)))
    a = dict(a)
    a.update(ok=True, frame=fr, belts=belts, lanes=len(bs))
    return a


def _emit(a, ore_r, D, B, mining_time):
    fr, s = a["frame"], D["size"]
    lo, hi = _area_range(s, D["radius"])
    drills, boxes, occupied, covered = [], [], set(), set()
    for m in sorted(a["miners"], key=lambda m: (m["v"], m["u"])):
        x, y = fr.to_r(m["u"] + s / 2.0, m["v"] + s / 2.0)
        drills.append({"name": D["name"], "x": x, "y": y, "direction": m["dir"]})
        boxes.append([x - s / 2.0, y - s / 2.0, x + s / 2.0, y + s / 2.0])
        occupied.update((math.floor(x - s / 2.0) + i, math.floor(y - s / 2.0) + j) for i in range(s) for j in range(s))
        covered.update(t for t in _area_tiles(x, y, D["radius"]) if t in ore_r)
    belts = []
    for u, v, (du, dv) in a["belts"]:
        x, y = fr.to_r(u + 0.5, v + 0.5)
        belts.append({"name": B["name"], "x": math.floor(x) + 0.5, "y": math.floor(y) + 0.5,
                      "direction": fr.dir(du, dv)})
        occupied.add((math.floor(x), math.floor(y)))
    # free rows on the far side of every drill row from its belt: the pair_gap rows between pairs
    # and the rows just outside the outermost drills
    rows = set()
    for m in a["miners"]:
        rows.add(m["v"] - 1 if m["kind"] == "top" else m["v"] + s)
    us = [m["u"] for m in a["miners"]]
    pole_rows = []
    for v in sorted(rows):
        row = []
        for u in range(min(us) - 1, max(us) + s + 1):
            x, y = fr.to_r(u + 0.5, v + 0.5)
            t = (math.floor(x), math.floor(y))
            if t not in occupied:
                row.append([t[0] + 0.5, t[1] + 0.5])
        if row:
            pole_rows.append(row)
    n = len(drills)
    rate = n * float(D.get("mining_speed") or 0) / mining_time
    stats = {"ore_tiles": len(ore_r), "covered_tiles": len(covered),
             "coverage": round(100.0 * len(covered) / len(ore_r), 1), "drills": n, "lanes": a["lanes"],
             "belts": len(belts), "orientation": a["orientation"], "offset": [a["su"], a["sv"]],
             "postponed_drills": sum(1 for m in a["miners"] if m.get("postponed")),
             "score": round(a["score"], 4), "ore_amount": sum(ore_r[t] for t in covered),
             "rate": round(rate, 3)}
    ex = belts[-1]
    step = STEP[ex["direction"]]
    out = {"ok": True, "entities": drills + belts, "drills": drills, "belts": belts, "exit": dict(ex),
           "out": [ex["x"] + step[0], ex["y"] + step[1]],
           "drill_boxes": boxes, "occupied": [[t[0] + 0.5, t[1] + 0.5] for t in sorted(occupied)],
           "pole_rows": pole_rows, "stats": stats}
    if rate > float(B["speed"]) + 1e-9:
        out["warning"] = f"{n} drills make {rate:.1f}/s, more than one {B['name']} carries ({B['speed']}/s)"
    return out


def _area_tiles(x, y, radius):
    xa, xb = math.floor(x - radius - 0.5) + 1, math.ceil(x + radius - 0.5) - 1
    ya, yb = math.floor(y - radius - 0.5) + 1, math.ceil(y + radius - 0.5) - 1
    return [(i, j) for j in range(ya, yb + 1) for i in range(xa, xb + 1)]


# ── check ───────────────────────────────────────────────────────────────────────────────────────

def _belts_module():
    try:
        import belts  # next to this file in factorio_compute
        return belts
    except ImportError:
        import os
        import sys
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "belts", "lib")
        if os.path.isdir(p):
            sys.path.insert(0, os.path.abspath(p))
            try:
                import belts
                return belts
            except ImportError:
                return None
        return None


def check(entities, ore, blocked=(), *, drill=None, belt=None, exit=None):
    """Problems with a mining layout as a list of strings ([] = valid).

    Verifies: no two entities share a tile and none is on a blocked tile; every drill's mining area
    holds ore; every drill's output tile is a belt; every belt feeds another belt (never head-on)
    except the one exit belt (`exit`, or the only belt that feeds nothing); every belt reaches the
    exit; each source-to-exit line passes belts.check."""
    D, B = _drill(drill), _belt(belt)
    s = D["size"]
    ore_r = set(_ore(ore))
    blocked_r = {_idx(b) for b in blocked}
    problems, seen = [], {}
    drills, bmap = [], {}
    for i, e in enumerate(entities):
        x, y = _xy(e)
        if e["name"] == D["name"]:
            tiles = [(math.floor(x - s / 2.0) + a, math.floor(y - s / 2.0) + b) for a in range(s) for b in range(s)]
            drills.append((i, x, y, _dir(e.get("direction", 0))))
        elif e["name"] == B["name"]:
            tiles = [_idx(e)]
            bmap[tiles[0]] = (i, _dir(e.get("direction", 0)))
        else:
            problems.append(f"#{i} {e['name']}: neither the drill nor the belt")
            continue
        for t in tiles:
            if t in seen:
                problems.append(f"#{i} {e['name']} at {[x, y]} overlaps #{seen[t]} on tile {[t[0] + .5, t[1] + .5]}")
            seen[t] = i
            if t in blocked_r:
                problems.append(f"#{i} {e['name']} at {[x, y]} is on a blocked tile {[t[0] + .5, t[1] + .5]}")
    for i, x, y, d in drills:
        if not any(t in ore_r for t in _area_tiles(x, y, D["radius"])):
            problems.append(f"#{i} drill at {[x, y]} has no ore in its mining area")
        vx, vy = D["vec"][d]
        out = (math.floor(x + vx), math.floor(y + vy))
        if out not in bmap:
            problems.append(f"#{i} drill at {[x, y]} drops onto {[out[0] + .5, out[1] + .5]}, which is not a belt")
    if not bmap:
        return problems + ["no belts"]
    nxt, ends = {}, []
    for t in sorted(bmap):
        i, d = bmap[t]
        dx, dy = STEP[d]
        n = (t[0] + dx, t[1] + dy)
        if n in bmap:
            if (bmap[n][1] - d) % 16 == 8:
                problems.append(f"#{i} belt at {[t[0] + .5, t[1] + .5]} runs head-on into #{bmap[n][0]}")
            else:
                nxt[t] = n
        else:
            ends.append(t)
    if exit is not None:
        ex = _idx(exit)
        if ex not in bmap:
            problems.append(f"exit {[ex[0] + .5, ex[1] + .5]} is not a belt")
    elif len(ends) == 1:
        ex = ends[0]
    else:
        ex = None
    for t in ends:
        if t != ex:
            problems.append(f"#{bmap[t][0]} belt at {[t[0] + .5, t[1] + .5]} feeds nothing and is not the exit")
    reach = set()
    for t in sorted(bmap):
        path, cur = [], t
        while cur in nxt and cur not in reach and cur not in path:
            path.append(cur)
            cur = nxt[cur]
        if cur in path:
            problems.append(f"belt loop through {[cur[0] + .5, cur[1] + .5]}")
        elif cur == ex or cur in reach:
            reach.update(path)
            reach.add(cur)
        else:
            problems.append(f"belt at {[t[0] + .5, t[1] + .5]} does not reach the exit")
    bm = _belts_module()
    if bm is not None and ex is not None:
        fed = set(nxt.values())
        for t in sorted(bmap):
            if t in fed:
                continue
            line, cur = [], t
            while True:
                line.append(entities[bmap[cur][0]])
                if cur not in nxt or len(line) > len(bmap):
                    break
                cur = nxt[cur]
            blk = [(b[0] + .5, b[1] + .5) for b in sorted(blocked_r)]
            problems += [f"line from {[t[0] + .5, t[1] + .5]}: {p}" for p in bm.check(line, blk)]
    return problems
