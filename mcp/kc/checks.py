"""Validators for a planned set of entities, before it is placed.

The plan is the list Kira hands to kc.place_ghosts: [{name, x, y, direction?, type?}, ...].
Sizes and stats come from `specs` = kc.world.specs_from_proto(proto) (name -> normalised spec),
or any dict name -> {w, h, ...}; a name missing from specs counts as 1×1.
Every function returns a list of problems ([] = fine), so they compose:

    problems = kc.checks.all(plan, specs, grid=g, grid_poles=[...], connect=[...])
"""
from __future__ import annotations

from . import cover, geo, search, wires


def _size(specs, name):
    s = (specs or {}).get(name) or {}
    return int(s.get("w", 1) or 1), int(s.get("h", 1) or 1)


def tiles_of(item, specs=None):
    """Tile centers an item covers, by its size and direction."""
    w, h = _size(specs, item["name"])
    d = item.get("direction", 0)
    return geo.footprint(geo.snap(item, w, h, d), w, h, d)


def overlaps(plan, specs=None):
    """Items of the plan that share a tile with an earlier item."""
    seen, out = {}, []
    for i, it in enumerate(plan):
        for t in sorted(tiles_of(it, specs)):
            if t in seen:
                out.append(f"#{i} {it['name']} at ({it['x']}, {it['y']}) overlaps #{seen[t]} on tile {list(t)}")
                break
        for t in tiles_of(it, specs):
            seen.setdefault(t, i)
    return out


def blocked(plan, grid, specs=None):
    """Items that land on a tile the grid (kc.grid.Grid or a set of tiles) already has taken."""
    taken = grid.free if hasattr(grid, "free") else (lambda t, s={geo.tile(b) for b in grid}: t not in s)
    out = []
    for i, it in enumerate(plan):
        bad = sorted(t for t in tiles_of(it, specs) if not taken(t))
        if bad:
            out.append(f"#{i} {it['name']} at ({it['x']}, {it['y']}) is on taken tile {list(bad[0])}")
    return out


def _poles(items, specs):
    out = []
    for it in items:
        s = (specs or {}).get(it["name"]) or {}
        if s.get("supply_area_distance") is not None or s.get("supply") is not None:
            out.append({"name": it["name"], "x": float(it["x"]), "y": float(it["y"]),
                        "supply": float(s.get("supply_area_distance", s.get("supply"))),
                        "reach": float(s.get("max_wire_distance", s.get("reach", 0)) or 0)})
    return out


def _electric(specs, name):
    s = (specs or {}).get(name) or {}
    if s.get("supply_area_distance") is not None or s.get("supply") is not None:
        return False  # poles power others, they are not consumers
    return bool(s.get("electric") or s.get("energy") == "electric")


def unpowered(plan, specs, existing_poles=()):
    """Electric items no pole (planned or `existing_poles`, {x, y, supply}) reaches with its supply area."""
    poles = _poles(plan, specs) + [dict(p, supply=float(p.get("supply", p.get("supply_area_distance", 0))))
                                   for p in existing_poles]
    out = []
    for i, it in enumerate(plan):
        if not _electric(specs, it["name"]):
            continue
        w, h = _size(specs, it["name"])
        box = geo.rect(geo.snap(it, w, h, it.get("direction", 0)), w, h, it.get("direction", 0))
        if not any(cover.area_overlaps(box, (p["x"], p["y"]), p["supply"]) for p in poles):
            out.append(f"#{i} {it['name']} at ({it['x']}, {it['y']}) is outside every pole's supply area")
    return out


def unwired(plan, specs, grid_poles=()):
    """Planned poles not wire-connected (shorter-reach rule) to any of `grid_poles` ({x, y, reach}).
    With no grid poles, poles not connected to the first planned pole."""
    poles = _poles(plan, specs)
    if not poles:
        return []
    roots = list(grid_poles)
    bad = wires.unwired(poles, roots=roots)
    return [f"pole {poles[j]['name']} at ({poles[j]['x']}, {poles[j]['y']}) "
            f"is not wired to {'the grid' if roots else 'the first pole'}" for j in bad]


def disconnected(tiles, terminals):
    """Terminals not joined into one 4-connected network of `tiles` (pipes, belts as tile sets)."""
    tiles = [geo.tile(t) for t in tiles]
    terms = [geo.tile(t) for t in terminals]
    if not terms:
        return []
    missing = [t for t in terms if t not in set(tiles)]
    out = [f"terminal {list(t)} is not on the network" for t in missing]
    rest = [t for t in terms if t not in missing]
    if rest:
        for t in search.unreached(tiles, rest[0], rest[1:]):
            out.append(f"terminal {list(geo.tile(t))} is cut off from {list(rest[0])}")
    return out


def all(plan, specs=None, *, grid=None, grid_poles=None, existing_poles=(), connect=None):
    """Every check that its inputs allow: overlaps always; blocked with `grid`; unpowered and wiring when
    specs know poles; `connect` = (tiles, terminals) for one fluid/belt network."""
    out = overlaps(plan, specs)
    if grid is not None:
        out += blocked(plan, grid, specs)
    if specs:
        out += unpowered(plan, specs, existing_poles)
        if grid_poles is not None:
            out += unwired(plan, specs, grid_poles)
    if connect is not None:
        out += disconnected(*connect)
    return out
