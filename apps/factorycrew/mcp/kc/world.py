"""World adapter: turn what the mod's Lua readers return into kc objects and planner specs.

The Lua side (mod/kc-companion/scripts/helpers.lua, `kc.*` inside factorio_query) reads the save:
  kc.scan(surface, area, opts)       entities + ghosts + terrain of a box
  kc.proto(names)                    geometry and stats per entity prototype
  kc.resources(surface, at, r, names) resource tiles grouped by name
  kc.best(type, key, opts)           best researched entity of a type by a key
This module is the Python half: `grid_from_scan` builds a kc.grid.Grid, `specs_from_proto`
normalises prototypes (direction words -> 0/4/8/12, lists -> tuples), and small accessors give
the dicts the skills take (`pole_spec`, `drill_spec`, `belt_spec`, `beacon_spec`, `machine_spec`,
`pipe_ports`, `ore_tiles`). `check_scan` / `check_specs` list what is missing or cut.

Shapes it reads (JSON from the game; an empty Lua table may arrive as {} or []):
  scan: {area: [x0, y0, x1, y1], entities: [{name, type, x, y, w, h, direction, force, unit?,
         ghost?, recipe?, filters?, fluid?, io?, pair?}], tiles: {water|cliff|void: [[y, x_from, x_to]]},
         count, truncated, counts?}. w/h are the size as placed (already rotated); a run covers the
         tiles with integer indices x_from..x_to in row y (tile center = index + 0.5).
  proto: {name: spec | false}; see `specs_from_proto`.
  resources: {name: {name, count, amount, bbox, category, products, infinite?, tiles: [[x, y, amount]],
              truncated}}.
Stdlib only, Python 3.9.
"""
from __future__ import annotations

from . import geo
from .grid import Grid

DIR_WORDS = {"north": geo.N, "east": geo.E, "south": geo.S, "west": geo.W}
TERRAIN = ("water", "cliff", "void")


def _list(v):
    """A JSON array that may have come through as {} (empty Lua table) or None."""
    if v is None or v == {}:
        return []
    if isinstance(v, dict):  # a Lua array with holes serialises as {"1": .., "2": ..}
        return [v[k] for k in sorted(v, key=lambda s: int(s))]
    return list(v)


def _dir_key(k):
    if isinstance(k, str) and k.lower() in DIR_WORDS:
        return DIR_WORDS[k.lower()]
    return geo.dir_of(int(k))


def _box(v):
    v = _list(v)
    return tuple(float(a) for a in v) if len(v) == 4 else None


# ── scan ─────────────────────────────────────────────────────────────────

def tiles_from_runs(runs):
    """Tile centers of row runs [[y, x_from, x_to], ...] (integer tile indices), row by row."""
    out = []
    for r in _list(runs):
        y, x0, x1 = int(r[0]), int(r[1]), int(r[2])
        out.extend((x + 0.5, y + 0.5) for x in range(x0, x1 + 1))
    return out


def terrain(scan):
    """{"water": [tiles], "cliff": [tiles], "void": [tiles]} from a scan (empty lists when absent)."""
    t = scan.get("tiles") or {}
    return {k: tiles_from_runs(t.get(k)) for k in TERRAIN}


def entities(scan, ghosts=True, skip_types=(), skip_names=()):
    """The scan's entities as dicts with float x/y, int w/h (as placed) and int direction."""
    skip_types, skip_names = set(skip_types), set(skip_names)
    out = []
    for e in _list(scan.get("entities")):
        if e.get("ghost") and not ghosts:
            continue
        if e.get("type") in skip_types or e.get("name") in skip_names:
            continue
        d = dict(e)
        d["x"], d["y"] = float(e["x"]), float(e["y"])
        d["w"], d["h"] = int(e.get("w", 1)), int(e.get("h", 1))
        d["direction"] = geo.dir_of(e.get("direction", 0))
        d["ghost"] = bool(e.get("ghost", False))
        if "pair" in e and e["pair"] is not None:
            d["pair"] = tuple(float(a) for a in e["pair"])
        out.append(d)
    return out


def footprint_of(e):
    """Tiles a scan entity covers (its w/h are already the placed size)."""
    return geo.footprint((e["x"], e["y"]), int(e.get("w", 1)), int(e.get("h", 1)), geo.N)


def grid_from_scan(scan, bounds=None, ghosts=True, skip_types=(), skip_names=(), terrain_blocks=True):
    """kc.grid.Grid of a kc.scan result.

    Entities (and ghosts unless ghosts=False) are placed with their ids in grid.entities; water,
    cliff and void tiles are occupied by the strings "water" / "cliff" / "void". bounds: None = the
    scan's area (tiles outside count as blocked), False = unbounded, or an explicit (x0, y0, x1, y1).
    skip_types / skip_names leave those entities out (e.g. skip_types=("tree",) when trees get cleared).
    """
    if bounds is None:
        bounds = _box(scan.get("area"))
    elif bounds is False:
        bounds = None
    g = Grid(bounds=bounds)
    if terrain_blocks:
        for kind, tiles in terrain(scan).items():
            for t in tiles:
                g.occ[t] = kind
    for e in entities(scan, ghosts=ghosts, skip_types=skip_types, skip_names=skip_names):
        d = e["direction"]
        w, h = e["w"], e["h"]
        if geo.dir_of(d) in (geo.E, geo.W):  # Grid rotates by direction itself; hand it the prototype size
            w, h = h, w
        g.place(dict(e, w=w, h=h), force=True)
    return g


def blocked_tiles(scan, ghosts=True, skip_types=(), skip_names=(), terrain_blocks=True):
    """Sorted tile centers that are taken in the scan (entities, ghosts, terrain): the `blocked`
    argument most planners take."""
    out = set()
    if terrain_blocks:
        for tiles in terrain(scan).values():
            out.update(tiles)
    for e in entities(scan, ghosts=ghosts, skip_types=skip_types, skip_names=skip_names):
        out.update(footprint_of(e))
    return sorted(out, key=lambda t: (t[1], t[0]))


def check_scan(scan):
    """Problems with a scan as strings ([] = complete and well formed). A truncated scan is a problem:
    re-scan a smaller box or with names/types filters before planning on it."""
    out = []
    for k in ("area", "entities"):
        if k not in scan:
            out.append(f"missing {k}")
    if scan.get("truncated"):
        out.append(f"truncated: {len(_list(scan.get('entities')))} of {scan.get('count')} entities listed"
                   + (", tile runs cut" if scan.get("tiles_truncated") else ""))
    for i, e in enumerate(_list(scan.get("entities"))):
        miss = [k for k in ("name", "x", "y", "w", "h") if k not in e]
        if miss:
            out.append(f"entity {i} ({e.get('name')}) lacks {', '.join(miss)}")
    return out


# ── prototypes ───────────────────────────────────────────────────────────

def _ports(ports):
    out = {}
    for k, lst in (ports or {}).items():
        d = _dir_key(k)
        out[d] = [_port(p) for p in _list(lst)]
    return out


def _port(p):
    q = {"inside": (float(p["x"]), float(p["y"])), "direction": geo.dir_of(p.get("direction", 0)),
         "flow": p.get("flow"), "kind": p.get("kind", "normal")}
    if "tx" in p:
        q["target"] = (float(p["tx"]), float(p["ty"]))
    if p.get("max") is not None:
        q["max"] = int(p["max"])
    if p.get("category") is not None:
        q["category"] = tuple(_list(p["category"]))
    return q


def normalize_spec(s):
    """One kc.proto entry normalised (idempotent): ints for sizes, tuples for points and boxes,
    direction-keyed dicts with 0/4/8/12 keys, lists where Lua may have sent {}."""
    if not s:
        return None
    if s.get("_kc_norm"):
        return s
    n = dict(s)
    n["_kc_norm"] = True
    n["w"], n["h"] = int(s.get("w") or 1), int(s.get("h") or 1)
    n["size"] = (n["w"], n["h"])
    n["collision_box"] = _box(s.get("collision_box"))
    if "mining_area" in s:
        n["mining_area"] = _box(s["mining_area"])
    for k in ("resource_categories", "crafting_categories", "allowed_effects"):
        if k in s:
            n[k] = tuple(_list(s[k]))
    if "profile" in s:
        n["profile"] = [float(v) for v in _list(s["profile"])]
    if "output" in s:
        n["output"] = {_dir_key(k): (float(v[0]), float(v[1])) for k, v in (s["output"] or {}).items()}
    for k in ("pickup", "drop"):
        if s.get(k) is not None:
            n[k] = (float(s[k][0]), float(s[k][1]))
    n["fluid_boxes"] = [{"production": fb.get("production"), "volume": fb.get("volume"),
                         "ports": _ports(fb.get("ports") or {})} for fb in _list(s.get("fluid_boxes"))]
    return n


def specs_from_proto(proto):
    """{name: normalised spec} from a kc.proto result (unknown names -> None). A single spec dict is
    accepted too and comes back as {its name: spec}.

    A spec has: name, type, w, h, size (w, h), collision_box (x0, y0, x1, y1), item, energy
    ("electric"|"burner"|"heat"|"fluid"|"void"), energy_usage (W), fluid_boxes [{production, volume,
    ports: {0|4|8|12: [{inside, target, direction, flow, kind, max?, category?}]}}] and, per type,
    supply_area_distance, max_wire_distance, mining_radius, mining_area, mining_speed,
    resource_categories, output {dir: (x, y)}, belt_speed, items_per_second, underground,
    max_underground_distance, crafting_speed, crafting_categories, module_slots, allowed_effects,
    distribution_effectivity, profile, pickup, drop. Positions are relative to the entity center.
    """
    if isinstance(proto, dict) and "name" in proto and "type" in proto and isinstance(proto.get("name"), str):
        return {proto["name"]: normalize_spec(proto)}
    return {k: normalize_spec(v) for k, v in sorted((proto or {}).items())}


def _need(s, *keys):
    s = normalize_spec(s)
    if s is None:
        raise ValueError("no spec (unknown prototype?)")
    miss = [k for k in keys if s.get(k) is None]
    if miss:
        raise ValueError(f"{s.get('name')} ({s.get('type')}) has no {', '.join(miss)}")
    return s


def pole_spec(s):
    """{name, size, supply, reach}: the pole dict the power/solar skills and kc.poles entries use."""
    s = _need(s, "supply_area_distance", "max_wire_distance")
    return {"name": s["name"], "size": s["w"], "supply": float(s["supply_area_distance"]),
            "reach": float(s["max_wire_distance"])}


def drill_spec(s):
    """{name, size, radius, output {0|4|8|12: (x, y)}, mining_speed, categories, area}: the drill dict
    of the mining skill. output is where the drill drops its product, per facing, relative to center."""
    s = _need(s, "mining_radius")
    return {"name": s["name"], "size": s["w"], "radius": float(s["mining_radius"]),
            "output": dict(s.get("output") or {}), "mining_speed": s.get("mining_speed"),
            "categories": s.get("resource_categories", ()), "area": s.get("mining_area")}


def belt_spec(s, underground=None):
    """{name, speed (items/s), belt_speed (tiles/tick), underground, max_underground}. Pass the
    underground belt's spec as `underground` to fill max_underground (the planners' reach)."""
    s = _need(s, "belt_speed")
    u = normalize_spec(underground) if underground else None
    return {"name": s["name"], "speed": float(s.get("items_per_second") or s["belt_speed"] * 480),
            "belt_speed": float(s["belt_speed"]),
            "underground": (u["name"] if u else s.get("underground")),
            "max_underground": (u.get("max_underground_distance") if u else s.get("max_underground_distance"))}


def beacon_spec(s):
    """{name, size, supply_area_distance, distribution_effectivity, profile, module_slots}: the keyword
    arguments of the beacons skill."""
    s = _need(s, "supply_area_distance")
    return {"name": s["name"], "size": s["w"], "supply_area_distance": float(s["supply_area_distance"]),
            "distribution_effectivity": s.get("distribution_effectivity"), "profile": s.get("profile"),
            "module_slots": s.get("module_slots", 0)}


def machine_spec(s):
    """{name, w, h, crafting_speed, categories, module_slots, energy, energy_usage} of a crafting machine."""
    s = _need(s, "crafting_speed")
    return {"name": s["name"], "w": s["w"], "h": s["h"], "crafting_speed": float(s["crafting_speed"]),
            "categories": s.get("crafting_categories", ()), "module_slots": s.get("module_slots", 0),
            "energy": s.get("energy"), "energy_usage": s.get("energy_usage")}


def pipe_ports(s, direction=geo.N, center=None, production=None, kind=None):
    """Fluid connections of an entity facing `direction`: [{inside, target, direction, flow, kind,
    production, box, max?}]. inside = the entity's own tile with the connection, target = the tile a
    pipe must stand on (for kind "underground" the first tile of the gap). Relative to the entity
    center, or absolute when `center` is given. production / kind filter ("output", "underground")."""
    s = normalize_spec(s)
    d = geo.dir_of(direction)
    cx, cy = geo.xy(center) if center is not None else (0.0, 0.0)
    out = []
    for i, fb in enumerate(s.get("fluid_boxes") or []):
        if production is not None and fb.get("production") != production:
            continue
        for p in fb["ports"].get(d, []):
            if kind is not None and p["kind"] != kind:
                continue
            q = dict(p, production=fb.get("production"), box=i)
            q["inside"] = (p["inside"][0] + cx, p["inside"][1] + cy)
            if "target" in p:
                q["target"] = (p["target"][0] + cx, p["target"][1] + cy)
            out.append(q)
    return out


def check_specs(specs, required):
    """Problems with prototype specs ([] = fine). required: {name: kind} with kind one of "pole",
    "drill", "belt", "beacon", "machine", "fluid", "any"; or a list of names (kind "any")."""
    if not isinstance(required, dict):
        required = {n: "any" for n in required}
    getters = {"pole": pole_spec, "drill": drill_spec, "belt": belt_spec, "beacon": beacon_spec,
               "machine": machine_spec}
    out = []
    for name, kind in sorted(required.items()):
        s = (specs or {}).get(name)
        if not s:
            out.append(f"{name}: unknown prototype")
            continue
        if kind == "fluid":
            if not normalize_spec(s).get("fluid_boxes"):
                out.append(f"{name}: no fluid boxes")
        elif kind in getters:
            try:
                getters[kind](s)
            except ValueError as e:
                out.append(f"{name}: {e}")
        elif kind != "any":
            out.append(f"{name}: unknown kind {kind!r}")
    return out


# ── resources ────────────────────────────────────────────────────────────

def ore_tiles(resources, name):
    """[(x, y, amount)] of one resource from a kc.resources result (the mining skill's `ore`)."""
    g = (resources or {}).get(name) or {}
    return [(float(t[0]), float(t[1]), float(t[2])) for t in _list(g.get("tiles"))]


def resource_names(resources):
    """Resource names in a kc.resources result, the largest total amount first."""
    return [k for k, _ in sorted((resources or {}).items(), key=lambda kv: (-float(kv[1].get("amount", 0)), kv[0]))]
