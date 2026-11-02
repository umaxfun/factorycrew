"""Greedy set cover: the fewest candidates that together cover every target.

Use it for anything where a placed thing serves an area: electric poles over consumers, beacons
over machines, roboports over a build area, radars over chunks, turrets over a wall.

    targets     anything hashable (consumer indices, tiles, chunk keys)
    candidates  anything hashable (positions as (x, y) tuples)
    covers      callable candidate -> iterable of targets, or a dict {candidate: targets}

Pieces:
  greedy()            repeatedly take the candidate with the best gain (new target weight / cost)
  prune()             drop chosen candidates that became redundant (latest first)
  uncovered()         validator: targets no chosen candidate covers
  square_reach()      the square of half-size r around a center (a pole's supply area, a beacon's
                      effect area)
  area_overlaps()     whether that square overlaps an entity box with positive area (Factorio's
                      rule: edge contact does not count)
  candidates_around() every free position, on a size's grid, whose square reaches some box,
                      mapped to the boxes it reaches (feeds greedy() directly)

The square rule: a pole powers an entity when the entity's tile box overlaps the square of
half-size supply_area_distance around the pole's center. A beacon reaches a machine when the
machine's box overlaps the beacon's own box grown by supply_area_distance, i.e. a square of
half-size beacon_size / 2 + supply_area_distance around the beacon's center.

Written for factorycrew (MIT). Extracted from skills/power/lib/power.py (greedy max coverage with
gain buckets, redundant-pole prune, candidate generation), itself written from scratch from an
idea only (greedy cover in demipixel/factorio-generators power.js and Ben-Ramchandani/PlannerCore;
neither has a usable license, so no code was read into or copied from them).
"""
from __future__ import annotations

import math

from . import geo

EPS = 1e-9


# ── helpers ──────────────────────────────────────────────────────────────

def _ordered(xs):
    """A list in a fixed order: sequences keep theirs, sets are sorted (repr as a fallback)."""
    if isinstance(xs, (set, frozenset)):
        try:
            return sorted(xs)
        except TypeError:
            return sorted(xs, key=repr)
    return list(xs)


def _unique(xs):
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _fn(v, default):
    if v is None:
        return lambda _k: default
    if callable(v):
        return v
    return lambda k: v.get(k, default)


def _covers_fn(covers):
    if isinstance(covers, dict):
        return lambda c: covers.get(c, ())
    if not callable(covers):
        raise TypeError("covers must be a callable candidate -> targets or a dict")
    return covers


# ── greedy cover ─────────────────────────────────────────────────────────

def greedy(targets, candidates, covers=None, *, weight=None, cost=None, max_count=None, tie=None,
           conflicts=None, min_gain=0.0):
    """Greedy (weighted) set cover / max coverage.

    targets: hashable items to cover. candidates: hashable items to choose from (a dict
        {candidate: targets} may stand for both candidates and covers).
    covers: candidate -> iterable of targets (or a dict). Targets outside `targets` are ignored.
    weight: target -> weight (callable or dict, default 1). cost: candidate -> cost > 0 (default 1).
        Each step takes the candidate with the highest (weight of still-uncovered targets) / cost.
    max_count: stop after this many candidates (max coverage instead of full cover).
    tie: optional key(candidate, chosen_so_far) that orders candidates with equal score (smaller
        first); the input order breaks what remains, so the result is deterministic.
    conflicts: optional candidate -> iterable of candidates that cannot be chosen once it is (e.g.
        positions whose footprints overlap it).
    min_gain: stop when the best score is not above this.

    Returns {"chosen": [candidates in pick order], "uncovered": [targets left, input order],
    "covered": weight covered, "cost": total cost}. Gains are kept in buckets and updated as
    targets get covered, so each step costs about the size of the best bucket.
    """
    if covers is None:
        if not isinstance(candidates, dict):
            raise TypeError("covers is required unless candidates is a dict {candidate: targets}")
        covers = candidates
    cov = _covers_fn(covers)
    w, c = _fn(weight, 1), _fn(cost, 1)
    tlist = _unique(_ordered(targets))
    tset = set(tlist)
    cands = _unique(_ordered(candidates))
    weighted = weight is not None or cost is not None

    reach, by_t, gain, costs, idx = {}, {t: [] for t in tlist}, {}, {}, {}
    for i, k in enumerate(cands):
        ts = [t for t in _unique(cov(k)) if t in tset]
        if not ts:
            continue
        ck = c(k)
        if not ck > 0:
            raise ValueError(f"candidate cost must be > 0: {k!r} costs {ck!r}")
        reach[k], costs[k], idx[k] = ts, ck, i
        gain[k] = sum(w(t) for t in ts)
        for t in ts:
            by_t[t].append(k)

    def score(k):
        return round(gain[k] / costs[k], 9) if weighted else gain[k]

    buckets, where = {}, {}

    def put(k):
        s = score(k)
        if s > EPS:
            buckets.setdefault(s, set()).add(k)
            where[k] = s

    def drop(k):
        s = where.pop(k, None)
        if s is not None:
            buckets[s].discard(k)
            if not buckets[s]:
                del buckets[s]

    for k in reach:
        put(k)
    alive = set(reach)

    def kill(k):
        alive.discard(k)
        drop(k)

    done, chosen, covered, spent = set(), [], 0, 0
    while max_count is None or len(chosen) < max_count:
        if not buckets:
            break
        top = max(buckets)
        if top <= min_gain:
            break
        pool = buckets[top]
        if tie is None:
            k = min(pool, key=lambda q: idx[q])
        else:
            k = min(pool, key=lambda q: (tie(q, chosen), idx[q]))
        chosen.append(k)
        spent += costs[k]
        kill(k)
        for t in reach[k]:
            if t in done:
                continue
            done.add(t)
            wt = w(t)
            covered += wt
            for q in by_t[t]:
                if q in alive:
                    drop(q)
                    gain[q] -= wt
                    put(q)
        if conflicts is not None:
            for q in conflicts(k):
                if q in alive:
                    kill(q)
    return {"chosen": chosen, "uncovered": [t for t in tlist if t not in done], "covered": covered,
            "cost": spent}


def prune(chosen, covers, targets=None):
    """Chosen candidates with the redundant ones dropped: walking from the latest pick back, a
    candidate goes when every target it covers is covered by another kept one. Order is kept.
    targets: only these count (default: everything `covers` names)."""
    cov = _covers_fn(covers)
    tset = None if targets is None else set(_ordered(targets))
    sets = {}
    for k in chosen:
        sets[k] = [t for t in _unique(cov(k)) if tset is None or t in tset]
    count = {}
    for k in chosen:
        for t in sets[k]:
            count[t] = count.get(t, 0) + 1
    kept = list(chosen)
    for k in reversed(list(chosen)):
        if all(count[t] >= 2 for t in sets[k]):
            kept.remove(k)
            for t in sets[k]:
                count[t] -= 1
    return kept


def uncovered(targets, chosen, covers):
    """Validator: the targets (input order) that no chosen candidate covers. [] = full cover."""
    cov = _covers_fn(covers)
    got = set()
    for k in chosen:
        got.update(cov(k))
    return [t for t in _ordered(targets) if t not in got]


# ── squares and boxes ────────────────────────────────────────────────────

def square_reach(center, radius):
    """(x0, y0, x1, y1) of the square of half-size `radius` around `center`."""
    x, y = geo.xy(center)
    r = float(radius)
    return (x - r, y - r, x + r, y + r)


def box(entity):
    """(x0, y0, x1, y1) of an entity box: a 4-sequence as is, or a dict {x, y, w?, h?,
    direction?} (also position / width / height / tile_width / tile_height; size 1 by default)."""
    if isinstance(entity, dict):
        if all(k in entity for k in ("x0", "y0", "x1", "y1")):
            return (float(entity["x0"]), float(entity["y0"]), float(entity["x1"]), float(entity["y1"]))
        w = float(next((entity[k] for k in ("w", "width", "tile_width") if entity.get(k) is not None), 1))
        h = float(next((entity[k] for k in ("h", "height", "tile_height") if entity.get(k) is not None), w))
        return geo.rect(entity, w, h, entity.get("direction", geo.N))
    b = list(entity)
    if len(b) != 4:
        raise ValueError(f"a box is (x0, y0, x1, y1) or an entity dict, got {entity!r}")
    return tuple(float(v) for v in b)


def area_overlaps(footprint_box, center, radius):
    """Whether the square of half-size `radius` around `center` overlaps the box with positive
    area (touching edges do not count, as in the game)."""
    return geo.overlaps(box(footprint_box), square_reach(center, radius))


def _strict(lo, hi, o):
    """Grid values n + o with lo < v < hi."""
    return [n + o for n in range(math.floor(lo - o + EPS) + 1, math.ceil(hi - o - EPS))]


def _off(size):
    return 0.5 if int(size) % 2 else 0.0


def _fits(grid, x, y, w, h):
    """grid.can_place((x, y), w, h) for a position already on the size's grid (fast path for a
    kc.grid.Grid; anything else with can_place is asked directly)."""
    if not (hasattr(grid, "occ") and hasattr(grid, "bounds")):
        return grid.can_place((x, y), w, h)
    occ, bounded = grid.occ, grid.bounds is not None
    bx, by = math.floor(x - w / 2 + EPS) + 0.5, math.floor(y - h / 2 + EPS) + 0.5
    for i in range(int(w)):
        for j in range(int(h)):
            t = (bx + i, by + j)
            if t in occ or (bounded and not grid.inside(t)):
                return False
    return True


def candidates_around(boxes, radius, grid=None, size=1):
    """Every position for a size x size thing (odd sizes on tile centers, even on integers; size
    may be (w, h)) whose square of half-size `radius` reaches at least one box, and where the
    thing fits (`grid.can_place`, when a kc.grid.Grid is given).

    boxes: entity boxes (see box()). Returns {(x, y): [indices of the boxes it reaches]}, in the
    order positions are found (box by box, x then y). The dict is an intermediate meant for
    greedy(range(len(boxes)), result); its keys are tuples, so turn it into a list before
    putting it in a JSON result."""
    w, h = (size, size) if isinstance(size, (int, float)) else (size[0], size[1])
    w, h = int(w), int(h)
    ox, oy = _off(w), _off(h)
    r = float(radius)
    out, bad = {}, set()
    for i, b in enumerate(boxes):
        x0, y0, x1, y1 = box(b)
        for x in _strict(x0 - r, x1 + r, ox):
            for y in _strict(y0 - r, y1 + r, oy):
                p = (x, y)
                if p in bad:
                    continue
                if p not in out:
                    if grid is not None and not _fits(grid, x, y, w, h):
                        bad.add(p)
                        continue
                    out[p] = []
                out[p].append(i)
    return out
