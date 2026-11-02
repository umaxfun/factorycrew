"""Solar fields for factorio_compute (`import solar`).

ratio()  -- panels and accumulators for a steady load over the surface's day/night cycle.
cell()   -- one tileable square cell: a pole in the middle, panels and accumulators around it.
field()  -- cells tiled over free ground near a point, as kc.place_ghosts items.
check()  -- overlaps, blocked tiles, pole coverage, wire links, achieved ratio.

THE RATIO (derived here, see SOURCE.md). A day lasts T = ticks_per_day / 60 s. The surface's
daytime runs 0..1 with noon at 0; with dusk < evening < morning < dawn the light level b is
1 before dusk and after dawn, falls linearly to 0 between dusk and evening, is 0 until morning
and rises linearly to 1 at dawn. A panel gives P * m * b, P its peak, m the solar multiplier
(surface.solar_power_multiplier * the "solar-power" surface property / 100). So
  full = 1 - (dawn - dusk), down = evening - dusk, dark = morning - evening, up = dawn - morning
  average fraction f = full + (down + up) / 2                      (Nauvis: 0.5 + 0.1 + 0.1 = 0.7)
For a load L = k * P * m per panel (k <= f) the panels fall short of L in one contiguous window:
the last k of the dusk ramp, the dark, and the first k of the dawn ramp. The energy missing in it
(two triangles and a rectangle) is what the accumulators must hold:
  E(k) = P * m * T * (dark * k + (down + up) * k^2 / 2)
At k = f this is the night deficit per panel; accumulators per panel = E(f) / capacity.
Nauvis: 60 kW * 416.67 s * (0.1 * 0.7 + 0.4 * 0.49 / 2) = 4.2 MJ = 0.84 of a 5 MJ accumulator,
and 42 kW average per 60 kW panel. Flow limits add two more bounds: the accumulators must
discharge L and absorb the noon surplus P * m - L.

THE CELL. Side S is the largest square the pole's supply area covers whole (pole at the center)
whose neighbours' poles are S apart, at most the wire reach: 18 for a substation (supply 9,
reach 18), 7 for a medium pole. The pole footprint is cut out; the four rectangles around it
(two ways: bands and pinwheels) are packed by a guillotine dynamic program over every sub-
rectangle size that keeps, per size, the Pareto front of (panels, accumulators) that fit. The
cell takes the most panels whose accumulators reach ratio * (1 - tolerance), and of those slots
fills only ceil(ratio * panels) accumulators (nearest the pole). For vanilla this is 24 panels +
20 accumulators per 18x18 substation cell: the common substation block, the wiki's 6:5 ratio
(0.833 against 0.84 needed, so ~0.3 % short at the end of the night). Everything lies inside the
pole's supply area, and the cells' poles link to their 4 neighbours, so a field is one network.

Tiles are tile centers (x.5, y.5) as in kc_compute; a 3x3 panel sits on x.5, a 2x2 accumulator
or substation on integers. Sizes, power and reach are arguments; vanilla values are defaults.
Written for factorycrew (MIT) from the idea of projectsbymj/Factorio-Solar-Planner (no license,
no code read). The substation cell count is the well-known community one; its layout is ours.
"""
from __future__ import annotations

import heapq
import math

PANEL = {"name": "solar-panel", "width": 3, "height": 3, "peak_kw": 60.0}
ACCUMULATOR = {"name": "accumulator", "width": 2, "height": 2, "capacity_mj": 5.0,
               "input_kw": 300.0, "output_kw": 300.0}
POLE = {"name": "substation", "width": 2, "height": 2, "supply": 9.0, "reach": 18.0}
NAUVIS = {"ticks_per_day": 25000, "dusk": 0.25, "evening": 0.45, "morning": 0.55, "dawn": 0.75,
          "solar_power_multiplier": 1.0, "solar_power": 100}
EPS = 1e-9


def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _tile(p):
    x, y = _xy(p)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def _ceil(v):
    return int(math.ceil(round(v, 9)))


# ---------------------------------------------------------------- ratio

def cycle(day_night=None, solar_multiplier=None):
    """The day as {seconds, full, down, dark, up, average, multiplier} from LuaSurface fields:
    ticks_per_day, dusk, evening, morning, dawn, always_day, freeze_daytime, daytime,
    solar_power_multiplier, solar_power (the surface property, percent; 100 if absent).
    `solar_multiplier` overrides the product of the last two."""
    dn = dict(NAUVIS if day_night is None else day_night)
    m = solar_multiplier
    if m is None:
        m = float(dn.get("solar_power_multiplier", 1.0)) * float(dn.get("solar_power", 100)) / 100.0
    if dn.get("always_day"):
        return {"seconds": None, "full": 1.0, "down": 0.0, "dark": 0.0, "up": 0.0, "average": 1.0,
                "multiplier": float(m), "constant": True}
    dusk, evening, morning, dawn = (float(dn[k]) for k in ("dusk", "evening", "morning", "dawn"))
    if not (0 <= dusk <= evening <= morning <= dawn <= 1):
        raise ValueError(f"need 0 <= dusk <= evening <= morning <= dawn <= 1, got {dusk}, {evening}, {morning}, {dawn}")
    if dn.get("freeze_daytime"):
        b = brightness(float(dn.get("daytime", 0.0)), dusk, evening, morning, dawn)
        return {"seconds": None, "full": b, "down": 0.0, "dark": 0.0, "up": 0.0, "average": b,
                "multiplier": float(m), "constant": True}
    full, down, dark, up = 1 - (dawn - dusk), evening - dusk, morning - evening, dawn - morning
    return {"seconds": float(dn["ticks_per_day"]) / 60.0, "full": full, "down": down, "dark": dark, "up": up,
            "average": full + (down + up) / 2, "multiplier": float(m), "constant": False}


def brightness(t, dusk, evening, morning, dawn):
    """Light level 0..1 at daytime t (0 = noon) on the game's piecewise-linear curve."""
    t %= 1.0
    if t < dusk or t >= dawn:
        return 1.0
    if t < evening:
        return 1.0 - (t - dusk) / (evening - dusk)
    if t < morning:
        return 0.0
    return (t - morning) / (dawn - morning)


def _deficit_kj(c, peak_kw, k):
    """Energy (kJ) one panel misses over a night when it must deliver k * peak * multiplier."""
    if c["constant"]:
        return 0.0
    return peak_kw * c["multiplier"] * c["seconds"] * (c["dark"] * k + (c["down"] + c["up"]) * k * k / 2)


def ratio(panel_peak_kw=60.0, accumulator_capacity_mj=5.0, day_night=None, *, solar_multiplier=None,
          accumulator_input_kw=None, accumulator_output_kw=None, target_mw=None):
    """Accumulators per panel for a steady load equal to the panels' day average.

    panel_peak_kw: the panel prototype's production (get_max_energy_production * 60 / 1000 kW).
    accumulator_capacity_mj: electric_energy_source_prototype.buffer_capacity / 1e6.
    day_night: LuaSurface fields (see cycle()); default Nauvis.
    accumulator_input_kw / _output_kw: flow limits; given, they bound the ratio too.
    target_mw: also return how many panels and accumulators make that average.
    """
    c = cycle(day_night, solar_multiplier)
    peak = float(panel_peak_kw) * c["multiplier"]
    avg = peak * c["average"]
    need = {"energy": _deficit_kj(c, float(panel_peak_kw), c["average"]) / (float(accumulator_capacity_mj) * 1000.0)}
    if c["constant"]:
        need["energy"] = 0.0
    else:
        if accumulator_output_kw:
            need["discharge"] = avg / float(accumulator_output_kw)
        if accumulator_input_kw:
            need["charge"] = (peak - avg) / float(accumulator_input_kw)
    bound = max(sorted(need), key=lambda k: need[k])
    r = need[bound]
    out = {"accumulators_per_panel": round(r, 6), "panels_per_accumulator": round(1 / r, 6) if r > EPS else None,
           "average_kw_per_panel": round(avg, 6), "peak_kw_per_panel": round(peak, 6),
           "average_fraction": round(c["average"], 6), "deficit_mj_per_panel": round(r * float(accumulator_capacity_mj), 6),
           "limited_by": bound if r > EPS else "none", "bounds": {k: round(v, 6) for k, v in sorted(need.items())},
           "day_seconds": c["seconds"], "multiplier": c["multiplier"]}
    if target_mw is not None:
        if avg <= EPS:
            out["panels"], out["accumulators"] = None, None
        else:
            n = _ceil(float(target_mw) * 1000.0 / avg)
            out["panels"], out["accumulators"] = n, _ceil(n * r)
    return out


def sustained_kw(panels, accumulators, panel_peak_kw=60.0, accumulator_capacity_mj=5.0, day_night=None, *,
                 solar_multiplier=None, accumulator_output_kw=None):
    """Steady kW that `panels` and `accumulators` deliver around the clock (the largest load whose
    night deficit the accumulators hold, at most the panels' average)."""
    c = cycle(day_night, solar_multiplier)
    pm = float(panel_peak_kw) * c["multiplier"]
    if panels <= 0 or pm <= 0:
        return 0.0
    if c["constant"]:
        return round(panels * pm * c["average"], 6)
    # E(k) * panels <= accumulators * capacity, E quadratic in k
    qa, qb = (c["down"] + c["up"]) / 2, c["dark"]
    qc = -accumulators * float(accumulator_capacity_mj) * 1000.0 / (panels * pm * c["seconds"])
    if qa > EPS:
        k = (-qb + math.sqrt(qb * qb - 4 * qa * qc)) / (2 * qa)
    elif qb > EPS:
        k = -qc / qb
    else:
        k = c["average"]
    load = panels * pm * min(k, c["average"])
    if accumulator_output_kw and c["dark"] > EPS:
        load = min(load, accumulators * float(accumulator_output_kw))
    return round(load, 6)


# ---------------------------------------------------------------- cell

def _spec(d, base):
    s = dict(base)
    s.update(d or {})
    return s


def _r(ratio_arg):
    if ratio_arg is None:
        return ratio()["accumulators_per_panel"]
    if isinstance(ratio_arg, dict):
        return float(ratio_arg["accumulators_per_panel"])
    return float(ratio_arg)


def _fronts(W, H, pw, ph, aw, ah):
    """Pareto fronts of (panels, accumulators) for every w x h <= W x H under guillotine cuts.
    F[w, h] = {panels: (accumulators, how)}."""
    lo = min(pw, ph, aw, ah)
    F = {}
    for w in range(0, W + 1):
        for h in range(0, H + 1):
            best = {0: (0, None)}

            def put(p, a, how):
                if a > best.get(p, (-1,))[0]:
                    best[p] = (a, how)
            if w and h:
                if w >= pw and h >= ph:
                    put(1, 0, ("P",))
                if w >= aw and h >= ah:
                    put(0, 1, ("A",))
                for p, (a, _) in F[w - 1, h].items():
                    put(p, a, ("w",))
                for p, (a, _) in F[w, h - 1].items():
                    put(p, a, ("h",))
                for x in range(lo, w // 2 + 1):
                    if w - x < lo:
                        continue
                    A, B = F[x, h], F[w - x, h]
                    for p1, (a1, _) in A.items():
                        for p2, (a2, _) in B.items():
                            put(p1 + p2, a1 + a2, ("v", x, p1))
                for y in range(lo, h // 2 + 1):
                    if h - y < lo:
                        continue
                    A, B = F[w, y], F[w, h - y]
                    for p1, (a1, _) in A.items():
                        for p2, (a2, _) in B.items():
                            put(p1 + p2, a1 + a2, ("c", y, p1))
            front, top = {}, -1
            for p in sorted(best, reverse=True):
                if best[p][0] > top:
                    front[p] = best[p]
                    top = best[p][0]
            F[w, h] = front
    return F


def _place(F, w, h, x0, y0, p, out):
    how = F[w, h][p][1]
    if how is None:
        return
    k = how[0]
    if k == "P" or k == "A":
        out.append((k, x0, y0))
    elif k == "w":
        _place(F, w - 1, h, x0, y0, p, out)
    elif k == "h":
        _place(F, w, h - 1, x0, y0, p, out)
    elif k == "v":
        x, p1 = how[1], how[2]
        _place(F, x, h, x0, y0, p1, out)
        _place(F, w - x, h, x0 + x, y0, p - p1, out)
    else:
        y, p1 = how[1], how[2]
        _place(F, w, y, x0, y0, p1, out)
        _place(F, w, h - y, x0, y0 + y, p - p1, out)


def _cell_size(pole):
    """Largest S such that a pole centered in S x S covers it whole and S <= wire reach."""
    d, reach, w, h = float(pole["supply"]), float(pole["reach"]), int(pole["width"]), int(pole["height"])
    for s in range(int(min(2 * d, reach) + EPS), 0, -1):
        if s < max(w, h):
            break
        qx, qy = (s - w) // 2, (s - h) // 2
        cx, cy = qx + w / 2, qy + h / 2
        if cx - d <= EPS and cx + d >= s - EPS and cy - d <= EPS and cy + d >= s - EPS:
            return s
    return None


def cell(panel=None, accumulator=None, pole=None, ratio=None, size=None, day_night=None, tolerance=0.02):
    """One tileable cell: pole in the middle, panels and accumulators packed around it.

    panel {name, width, height, peak_kw}, accumulator {name, width, height, capacity_mj,
    output_kw?}, pole {name, width, height, supply (supply_area_distance), reach
    (max_wire_distance)}; missing keys take the vanilla values.
    ratio: accumulators per panel (a number, or the dict ratio() returns); default ratio() on Nauvis.
    tolerance: how far below the ratio the cell may fall to fit one more panel (default 2 %).
    size: force the side (or [width, height]); default the largest the pole covers and links.
    day_night: if given (or ratio is a ratio() dict, then Nauvis unless given), the result
    carries sustained_kw for one cell.

    Returns {"ok", "width", "height", "entities" (relative to the cell's top-left tile corner
    (0, 0)), "pole" (its center), "panels", "accumulators", "ratio", "usable_panels",
    "sustained_kw"?} or {"ok": False, "reason"}.
    """
    pn, ac, po = _spec(panel, PANEL), _spec(accumulator, ACCUMULATOR), _spec(pole, POLE)
    r = _r(ratio)
    if size is None:
        s = _cell_size(po)
        if s is None:
            return {"ok": False, "reason": f"pole {po['name']} covers no cell larger than itself"}
        W = H = s
    else:
        W, H = (int(size), int(size)) if isinstance(size, (int, float)) else (int(size[0]), int(size[1]))
    pw, ph, aw, ah = int(pn["width"]), int(pn["height"]), int(ac["width"]), int(ac["height"])
    qw, qh = int(po["width"]), int(po["height"])
    qx, qy = (W - qw) // 2, (H - qh) // 2
    F = _fronts(W, H, pw, ph, aw, ah)
    R, B, Rw = qx + qw, qy + qh, W - qx - qw
    layouts = [
        [(0, 0, W, qy), (0, B, W, H - B), (0, qy, qx, qh), (R, qy, Rw, qh)],          # bands
        [(0, 0, qx, H), (R, 0, Rw, H), (qx, 0, qw, qy), (qx, B, qw, H - B)],          # columns
        [(0, 0, R, qy), (R, 0, Rw, B), (qx, B, W - qx, H - B), (0, qy, qx, H - qy)],  # pinwheel
        [(0, 0, qx, B), (qx, 0, W - qx, qy), (R, qy, Rw, H - qy), (0, B, R, H - B)],  # pinwheel
    ]

    best = None  # most panels whose accumulators reach r * (1 - tolerance); then the fewest needed
    for li, regions in enumerate(layouts):
        combo = {0: (0, [])}
        for (x, y, w, h) in regions:
            nxt = {}
            for p0, (a0, picks) in combo.items():
                for p1, (a1, _) in F[w, h].items():
                    if a0 + a1 > nxt.get(p0 + p1, (-1,))[0]:
                        nxt[p0 + p1] = (a0 + a1, picks + [p1])
            combo = nxt
        for p in sorted(combo):
            a, picks = combo[p]
            if a + EPS < r * p * (1 - tolerance):
                continue
            want = min(a, _ceil(r * p))
            sc = (p, -want)
            if best is None or sc > best[0]:
                best = (sc, p, want, li, picks)
    _, P, A, li, picks = best
    items = []
    for (x, y, w, h), p in zip(layouts[li], picks):
        _place(F, w, h, x, y, p, items)
    # more accumulator slots than the ratio needs: keep the ones nearest the pole
    cx, cy = qx + qw / 2, qy + qh / 2
    accs = sorted((t for t in items if t[0] == "A"),
                  key=lambda t: (abs(t[1] + aw / 2 - cx) + abs(t[2] + ah / 2 - cy), t[2], t[1]))
    items = [t for t in items if t[0] == "P"] + accs[:A]
    ents = [{"name": po["name"], "x": qx + qw / 2, "y": qy + qh / 2, "direction": 0}]
    for k, x, y in sorted(items, key=lambda t: (t[2], t[1], t[0])):
        sp, (w, h) = (pn, (pw, ph)) if k == "P" else (ac, (aw, ah))
        ents.append({"name": sp["name"], "x": x + w / 2, "y": y + h / 2, "direction": 0})
    out = {"ok": True, "width": W, "height": H, "entities": ents, "pole": [qx + qw / 2, qy + qh / 2],
           "panels": P, "accumulators": A, "ratio": round(A / P, 6) if P else None,
           "target_ratio": r, "usable_panels": round(min(P, A / r) if r > EPS else P, 6),
           "names": {"pole": po["name"], "panel": pn["name"], "accumulator": ac["name"]},
           "sizes": {po["name"]: [qw, qh], pn["name"]: [pw, ph], ac["name"]: [aw, ah]}}
    if day_night is not None or isinstance(ratio, dict):
        out["sustained_kw"] = sustained_kw(P, A, float(pn["peak_kw"]), float(ac["capacity_mj"]), day_night,
                                           accumulator_output_kw=ac.get("output_kw"))
    return out


# ---------------------------------------------------------------- field

def _box(area):
    if isinstance(area, dict):
        a, b = area.get("left_top", area.get("lt")), area.get("right_bottom", area.get("rb"))
    else:
        a, b = area[0], area[1]
    (x0, y0), (x1, y1) = _xy(a), _xy(b)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _foot(e, w, h):
    x0, y0 = e["x"] - w / 2, e["y"] - h / 2
    return [(math.floor(x0 + EPS) + i + 0.5, math.floor(y0 + EPS) + j + 0.5) for i in range(int(w)) for j in range(int(h))]


def field(cell_layout, origin=None, *, area=None, target_mw=None, blocked=(), partial=False, kw_per_cell=None,
          search=24, max_cells=2000, sizes=None):
    """Tile `cell_layout` (what cell() returned) over free ground.

    origin: the point the field grows from (the grid is anchored at its tile corner); with
        `area` it defaults to the area's top-left corner.
    area: [[x0, y0], [x1, y1]] (or {left_top, right_bottom}): fill every cell that fits inside.
    target_mw: stop once the cells give this much (uses cell sustained_kw, or kw_per_cell).
    blocked: tiles nothing may go on (entities, ghosts, water, cliffs, ore you keep free).
    partial: keep a cell whose pole tiles are free and drop only the items that hit blocked
        tiles (trees, a rock); default False skips the whole cell.
    search: in target mode, how many cells away from origin the field may reach.
    sizes: {name: [width, height]} for the cell's entity names; default read from the cell
        (panel 3x3, accumulator and pole 2x2 unless the cell says otherwise).

    Cells are added nearest-first, each touching one already chosen, so every pole links to
    a neighbour and the field is one network. Returns {"ok", "entities" (kc.place_ghosts
    items), "cells" (top-left corners), "panels", "accumulators", "poles", "skipped" (blocked
    cells), "sustained_kw"?} or {"ok": False, "reason"}.
    """
    if not cell_layout.get("ok"):
        return {"ok": False, "reason": "the cell is not valid: " + str(cell_layout.get("reason"))}
    W, H = int(cell_layout["width"]), int(cell_layout["height"])
    rel = cell_layout["entities"]
    pole_name = rel[0]["name"]
    if sizes is None:
        sizes = _sizes_from_cell(cell_layout)
    blocked = {_tile(b) for b in blocked}
    if area is None and target_mw is None:
        return {"ok": False, "reason": "give area or target_mw"}
    kw = kw_per_cell if kw_per_cell is not None else cell_layout.get("sustained_kw")
    need = None
    if target_mw is not None:
        if not kw:
            return {"ok": False, "reason": "target_mw needs the cell's sustained_kw (cell(..., day_night=...)) or kw_per_cell"}
        need = _ceil(float(target_mw) * 1000.0 / float(kw))
    if area is not None:
        x0, y0, x1, y1 = _box(area)
        if origin is None:
            origin = (x0, y0)
        ax, ay = math.floor(x0), math.floor(y0)
        cols, rows = int((math.floor(x1) - ax) // W), int((math.floor(y1) - ay) // H)
        cand = {(i, j) for i in range(cols) for j in range(rows)}
    else:
        if origin is None:
            return {"ok": False, "reason": "target_mw needs an origin"}
        ox, oy = _xy(origin)
        ax, ay = math.floor(ox) - W // 2, math.floor(oy) - H // 2
        cand = {(i, j) for i in range(-search, search + 1) for j in range(-search, search + 1)}
    ox, oy = _xy(origin)

    hit = {}
    for t in blocked:
        c = (int((t[0] - ax) // W), int((t[1] - ay) // H))
        if c in cand:
            hit.setdefault(c, set()).add(t)
    pole_tiles = set(_foot(rel[0], *sizes[pole_name]))

    def usable(c):
        if c not in cand:
            return False
        if c not in hit:
            return True
        if not partial:
            return False
        local = {(t[0] - ax - c[0] * W, t[1] - ay - c[1] * H) for t in hit[c]}
        return not (local & pole_tiles)

    def dist(c):
        return math.hypot(ax + c[0] * W + W / 2 - ox, ay + c[1] * H + H / 2 - oy)

    free = sorted((c for c in cand if usable(c)), key=lambda c: (dist(c), c[1], c[0]))
    skipped = len(cand) - len(free)
    if not free:
        return {"ok": False, "reason": "every candidate cell hits a blocked tile", "skipped": skipped}
    limit = min(need if need is not None else len(free), max_cells)
    freeset = set(free)
    chosen, seen = [], {free[0]}
    heap = [(dist(free[0]), free[0][1], free[0][0], free[0])]
    while heap and len(chosen) < limit:
        _, _, _, c = heapq.heappop(heap)
        chosen.append(c)
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + d[0], c[1] + d[1])
            if n in freeset and n not in seen:
                seen.add(n)
                heapq.heappush(heap, (dist(n), n[1], n[0], n))
    ents, panels, accs = [], 0, 0
    for c in sorted(chosen, key=lambda c: (c[1], c[0])):
        bx, by = ax + c[0] * W, ay + c[1] * H
        for e in rel:
            g = dict(e, x=e["x"] + bx, y=e["y"] + by)
            if partial and c in hit and set(_foot(g, *sizes[g["name"]])) & hit[c]:
                continue
            ents.append(g)
    names = _names(cell_layout)
    panels = sum(1 for e in ents if e["name"] == names["panel"])
    accs = sum(1 for e in ents if e["name"] == names["accumulator"])
    out = {"ok": True, "entities": ents, "cells": [[ax + c[0] * W, ay + c[1] * H] for c in sorted(chosen, key=lambda c: (c[1], c[0]))],
           "panels": panels, "accumulators": accs, "poles": len(chosen), "skipped": skipped,
           "unreachable": len(free) - len(seen) if area is not None else 0}
    if kw:
        out["sustained_kw"] = round(float(kw) * len(chosen), 3) if not partial else None
    if need is not None and len(chosen) < need:
        out["ok"] = False
        out["reason"] = f"only {len(chosen)} of {need} cells fit within {search} cells of origin"
    return out


def _names(cell_layout):
    if "names" in cell_layout:
        return cell_layout["names"]
    rel = cell_layout["entities"]
    others = sorted({e["name"] for e in rel[1:]})
    counts = {n: sum(1 for e in rel if e["name"] == n) for n in others}
    panel = next((n for n in others if counts[n] == cell_layout["panels"]), None)
    acc = next((n for n in others if n != panel), None)
    return {"pole": rel[0]["name"], "panel": panel, "accumulator": acc}


def _sizes_from_cell(cell_layout):
    out = {}
    if "sizes" in cell_layout:
        return {k: tuple(v) for k, v in cell_layout["sizes"].items()}
    n = _names(cell_layout)
    out[n["pole"]] = (POLE["width"], POLE["height"])
    if n["panel"]:
        out[n["panel"]] = (PANEL["width"], PANEL["height"])
    if n["accumulator"]:
        out[n["accumulator"]] = (ACCUMULATOR["width"], ACCUMULATOR["height"])
    return out


# ---------------------------------------------------------------- check

def check(entities, panel=None, accumulator=None, pole=None, blocked=(), ratio=None, tolerance=0.05):
    """Problems with a solar layout as strings ([] = valid).

    Verifies: every entity has a known name, no two share a tile, none is on a blocked tile,
    every panel and accumulator lies WHOLE inside some pole's supply area (stricter than the
    game, which only needs an overlap), all poles form one wire-linked network (distance <=
    reach), and, given `ratio`, accumulators / panels is not below ratio * (1 - tolerance).
    """
    pn, ac, po = _spec(panel, PANEL), _spec(accumulator, ACCUMULATOR), _spec(pole, POLE)
    size = {pn["name"]: (pn["width"], pn["height"]), ac["name"]: (ac["width"], ac["height"]),
            po["name"]: (po["width"], po["height"])}
    blocked = {_tile(b) for b in blocked}
    problems, seen = [], {}
    poles, items = [], []
    for i, e in enumerate(entities):
        if e.get("name") not in size:
            problems.append(f"#{i} unknown entity {e.get('name')!r}")
            continue
        w, h = size[e["name"]]
        for t in _foot(e, w, h):
            if t in seen:
                problems.append(f"#{i} {e['name']} at {[e['x'], e['y']]} overlaps #{seen[t]} on {list(t)}")
                break
            seen[t] = i
        if any(t in blocked for t in _foot(e, w, h)):
            problems.append(f"#{i} {e['name']} at {[e['x'], e['y']]} is on a blocked tile")
        (poles if e["name"] == po["name"] else items).append((i, e))
    d, reach = float(po["supply"]), float(po["reach"])
    for i, e in items:
        w, h = size[e["name"]]
        if not any(p["x"] - d - EPS <= e["x"] - w / 2 and e["x"] + w / 2 <= p["x"] + d + EPS and
                   p["y"] - d - EPS <= e["y"] - h / 2 and e["y"] + h / 2 <= p["y"] + d + EPS for _, p in poles):
            problems.append(f"#{i} {e['name']} at {[e['x'], e['y']]} is outside every pole's supply area")
    if poles:
        linked, todo = {0}, [0]
        while todo:
            a = poles[todo.pop()][1]
            for j, (_, b) in enumerate(poles):
                if j not in linked and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= reach + EPS:
                    linked.add(j)
                    todo.append(j)
        for j, (i, p) in enumerate(poles):
            if j not in linked:
                problems.append(f"#{i} pole at {[p['x'], p['y']]} is not wire-linked to the first pole")
    elif items:
        problems.append("no pole")
    if ratio is not None:
        r = _r(ratio)
        np_ = sum(1 for _, e in items if e["name"] == pn["name"])
        na = sum(1 for _, e in items if e["name"] == ac["name"])
        if np_ and na / np_ < r * (1 - tolerance) - EPS:
            problems.append(f"achieved ratio {na}/{np_} = {na / np_:.3f} accumulators per panel is below {r:.3f}")
    return problems
