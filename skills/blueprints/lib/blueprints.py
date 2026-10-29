"""Blueprint strings for factorio_compute (`import blueprints`).

Codec: a blueprint string is the version byte "0" + base64(zlib(JSON)). `decode` also takes the
raw JSON that 2.0 imports directly. `encode` writes compact JSON at zlib level 9, as the game does.

Placing: `to_ghosts` turns a blueprint into kc.place_ghosts items the way `build_blueprint` does.
Each entity is mirrored (x -> -x, direction d -> 16 - d; vertical: y -> -y, d -> 8 - d) and then
rotated clockwise in 90-degree steps ((x, y) -> (-y, x), d -> d + 4); entities that cannot turn
(prototype supports_direction == false: poles, chests, lamps) keep their direction. The bounding box of the
transformed entities (footprints from `sizes`, w/h swapped for east/west) is then centred on
`anchor`: an odd width puts the centre on a tile centre (floor + 0.5), an even width on the
nearest tile corner (round). That is the game's own cursor rule, checked in the 2.1 stand by
tests/integration/test_blueprints_game.py. Blueprints made by 1.x store 8-way directions; they
are doubled to the 2.0 16-way values, as the game does on import.

Origin: decode/encode/version/type/label/book listing are ported from pierre-dejoue/factorio-blueprints
(MIT, commit 045b59e2), factorio_game/exchange_string/blueprints.py and blueprints.py. The field
names, the 2.0 wire format ([entity1, connector1, entity2, connector2]) and the list of known entity
keys come from redruin1/factorio-blueprint-schemas (MIT, commit 682630ff), schemas/2.0.0. Everything
else (summary, transforms, to_ghosts, from_entities, check) is written for factorycrew (MIT).
See ../SOURCE.md.
"""
from __future__ import annotations

import base64
import json
import math
import zlib

TYPES = ("blueprint", "blueprint_book", "deconstruction_planner", "upgrade_planner")
ITEMS = {"blueprint": "blueprint", "blueprint_book": "blueprint-book",
         "deconstruction_planner": "deconstruction-planner", "upgrade_planner": "upgrade-planner"}
NAMES = {0: "north", 4: "east", 8: "south", 12: "west"}
DEFAULT_VERSION = (2, 0, 77, 0)

# 2.0 entity keys, the union of `properties` over schemas/2.0.0/entity.json and entity/*.json
# (redruin1/factorio-blueprint-schemas). A key outside this set is reported by check().
ENTITY_KEYS = frozenset("""
direction entity_number items mirror name position quality tags
alert_parameters always_on always_show ammo_inventory artillery_auto_targeting automatic_targeting_parameters
bar buffer_size chunk-filter circuit_condition circuit_enabled circuit_set_filters color
connect_to_logistic_network control_behavior driver_is_main_gunner drop_position
enable_logistics_while_moving filter filter_mode filters fluid_filter green_output_signal grid icon
ignore_unlisted_targets_condition ignore_unprioritized include_in_crafting infinity_settings
input_priority inventory link_id logistic_condition manual_trains_limit mode orange_output_signal
orientation output_priority override_stack_size parameters pickup_position player_description
power_production power_usage priority priority_list read_ammo read_contents read_recipe_finished
read_working recipe recipe_finished_signal recipe_quality red_output_signal request_filters
request_missing_construction_materials result_inventory selected_gun_index set_ignore_unprioritized
set_priority_list set_recipe show_in_chart spoil_priority station switch_state temperature text
transitional_request_index trunk_inventory type use_filters use_transitional_requests variation
working_signal
""".split()) | {"result-inventory", "priority-list"}  # hyphenated forms seen in real 2.0.47 strings
BLUEPRINT_KEYS = frozenset(("item", "label", "label_color", "description", "icons", "version", "snap-to-grid",
                            "absolute-snapping", "position-relative-to-grid", "entities", "tiles", "wires",
                            "parameters", "schedules", "stock_connections"))
BOOK_KEYS = frozenset(("item", "label", "label_color", "description", "icons", "version", "active_index", "blueprints"))
PLANNER_KEYS = frozenset(("item", "label", "label_color", "description", "icons", "version", "settings"))
CORE = ("entity_number", "name", "position", "direction", "type")

# Vanilla footprints (tile_width, tile_height) used when `sizes` does not name an entity. Read the
# real ones from prototypes (tile_width/tile_height) and pass them as `sizes`; modded names are
# unknown here and count as 1x1.
VANILLA_SIZES = {
    "splitter": (2, 1), "fast-splitter": (2, 1), "express-splitter": (2, 1), "turbo-splitter": (2, 1),
    "assembling-machine-1": (3, 3), "assembling-machine-2": (3, 3), "assembling-machine-3": (3, 3),
    "electric-furnace": (3, 3), "stone-furnace": (2, 2), "steel-furnace": (2, 2), "chemical-plant": (3, 3),
    "oil-refinery": (5, 5), "electric-mining-drill": (3, 3), "burner-mining-drill": (2, 2),
    "pumpjack": (3, 3), "beacon": (3, 3), "lab": (3, 3), "substation": (2, 2), "big-electric-pole": (2, 2),
    "accumulator": (2, 2), "solar-panel": (3, 3), "roboport": (4, 4), "radar": (3, 3),
    "storage-tank": (3, 3), "boiler": (3, 2), "steam-engine": (3, 5), "steam-turbine": (3, 5),
    "offshore-pump": (1, 2), "pump": (1, 2), "decider-combinator": (1, 2), "arithmetic-combinator": (1, 2),
    "selector-combinator": (1, 2), "gun-turret": (2, 2), "laser-turret": (2, 2), "train-stop": (2, 2),
    "centrifuge": (3, 3), "nuclear-reactor": (5, 5), "heat-exchanger": (3, 2), "rocket-silo": (9, 9),
    "straight-rail": (2, 2), "rail-signal": (1, 1), "rail-chain-signal": (1, 1),
}
MIRROR_FLAG = ("chemical-plant", "oil-refinery", "assembling-machine-2", "assembling-machine-3")
# Vanilla entities whose prototype has supports_direction == false: build_blueprint leaves their
# direction alone (a pole stays 0 in a rotated print). Pass the real set as `fixed`.
VANILLA_FIXED = frozenset("""small-electric-pole medium-electric-pole big-electric-pole substation small-lamp
wooden-chest iron-chest steel-chest active-provider-chest passive-provider-chest storage-chest buffer-chest
requester-chest infinity-chest accumulator beacon lab roboport radar solar-panel pipe heat-pipe stone-wall
stone-furnace steel-furnace electric-furnace rocket-silo land-mine""".split())


# ── codec (ported from pierre-dejoue/factorio-blueprints) ──

def decode(string):
    """Blueprint string -> dict. Takes "0" + base64(zlib(JSON)) or plain JSON (2.0 imports both).
    Whitespace and line breaks inside the string are ignored."""
    s = str(string).strip()
    if s.startswith("{"):
        return json.loads(s)  # raw JSON: its strings keep their spaces
    s = "".join(s.split())
    if not s:
        raise ValueError("empty blueprint string")
    if s[0] != "0":
        raise ValueError(f"unsupported blueprint string version byte {s[0]!r} (only '0' exists)")
    return json.loads(zlib.decompress(base64.b64decode(s[1:])).decode("utf-8"))


def encode(bp):
    """dict -> blueprint string ("0" + base64(zlib level 9 of compact JSON)). A bare
    {"entities": [...]} body is wrapped as {"blueprint": ...} first."""
    if not any(t in bp for t in TYPES):
        bp = {"blueprint": dict({"item": "blueprint", "version": version_number(DEFAULT_VERSION)}, **bp)}
    raw = json.dumps(bp, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "0" + base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


def version_string(v):
    """64-bit version number -> "2.0.11.3" (four 16-bit fields; the dev field only when non-zero)."""
    v = int(v)
    major, minor, patch, dev = (v >> 48) & 0xFFFF, (v >> 32) & 0xFFFF, (v >> 16) & 0xFFFF, v & 0xFFFF
    s = f"{major}.{minor}.{patch}"
    return f"{s}.{dev}" if dev else s


def version_number(v):
    """(2, 0, 77) / "2.0.77" -> 64-bit version number."""
    if isinstance(v, str):
        v = [int(p) for p in v.split(".")]
    v = list(v) + [0] * (4 - len(v))
    return (v[0] << 48) | (v[1] << 32) | (v[2] << 16) | v[3]


def kind(obj):
    """'blueprint' / 'blueprint_book' / 'deconstruction_planner' / 'upgrade_planner' / None."""
    for t in TYPES:
        if isinstance(obj, dict) and t in obj:
            return t
    return None


def _body(obj):
    t = kind(obj)
    if t:
        return obj[t]
    if isinstance(obj, dict) and "entities" in obj:
        return obj  # a bare blueprint body
    raise ValueError("not a blueprint, book or planner: expected one of " + ", ".join(TYPES))


def _major(body):
    return (int(body["version"]) >> 48) & 0xFFFF if "version" in body else 2


# ── books ──

def book_entries(book, depth=8):
    """Flat list of what a book holds, nested books included (up to `depth` levels):
    [{path: [i, ...] (indices from the outer book), index, type, label, entities, item}, ...]
    `item` is the wrapped entry ({"blueprint": ...}), ready for summary/to_ghosts/encode."""
    out = []

    def walk(obj, path, level):
        for e in _body(obj).get("blueprints", []) or []:
            idx = e.get("index")
            t = kind(e)
            body = e.get(t, {}) if t else {}
            p = path + [idx]
            out.append({"path": p, "index": idx, "type": t or "unknown", "label": body.get("label", ""),
                        "entities": len(body.get("entities", []) or []), "item": e})
            if t == "blueprint_book" and level < depth:
                walk(e, p, level + 1)

    if kind(book) != "blueprint_book":
        raise ValueError("book_entries needs a blueprint book")
    walk(book, [], 1)
    return out


# ── geometry ──

def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _dir(d):
    if d is None:
        return 0
    if isinstance(d, str):
        return {v: k for k, v in NAMES.items()}[d.lower()]
    return int(d) % 16


def _mirror_mode(m):
    if not m:
        return None
    if m is True or m in ("horizontal", "h", "x"):
        return "h"
    if m in ("vertical", "v", "y"):
        return "v"
    raise ValueError(f"mirror must be False, True/'horizontal' or 'vertical', got {m!r}")


def _size(name, d, sizes):
    w, h = (sizes or {}).get(name) or VANILLA_SIZES.get(name) or (1, 1)
    if isinstance(w, dict):  # {"tile_width": .., "tile_height": ..} straight from prototypes
        w, h = w["tile_width"], w["tile_height"]
    if d in (4, 12):
        w, h = h, w
    return float(w), float(h)


def _snap_center(v, span):
    """Where the centre of a box `span` tiles wide lands for cursor coordinate v."""
    if int(round(span)) % 2:
        return math.floor(v) + 0.5
    return math.floor(v + 0.5)


def _flip_priority(p):
    return {"left": "right", "right": "left"}.get(p, p)


def transform(bp, direction=0, mirror=False, mirror_flag=MIRROR_FLAG, fixed=None):
    """Entities of a blueprint mirrored then rotated, in blueprint coordinates (not centred).
    Returns [{entity_number, name, x, y, direction, type?, extra}]. Splitter priorities swap
    sides under a mirror; machines in `mirror_flag` (and any entity carrying `mirror`) toggle
    their fluid-box `mirror` flag, like the game's F/G flip. Names in `fixed` (default
    VANILLA_FIXED) keep their stored direction: the game cannot turn them."""
    body = _body(bp)
    if kind(bp) not in (None, "blueprint"):
        raise ValueError(f"a {kind(bp)} has no entities to place; pick one with book_entries() first")
    rot = _dir(direction)
    if rot % 4:
        raise ValueError(f"blueprints rotate in 90-degree steps: direction 0/4/8/12, got {rot}")
    mm = _mirror_mode(mirror)
    fixed = VANILLA_FIXED if fixed is None else frozenset(fixed)
    scale = 2 if _major(body) < 2 else 1  # 1.x: 8-way directions
    if mm:
        bad = sorted({e["name"] for e in body.get("entities", []) or []
                      if "curved-rail" in e["name"] or "half-diagonal-rail" in e["name"]})
        if bad:
            raise ValueError(f"mirroring curved/diagonal rails is not supported: {bad}")
    out = []
    for e in body.get("entities", []) or []:
        x, y = _xy(e["position"])
        d = (_dir(e.get("direction", 0)) * scale) % 16
        extra = {k: v for k, v in e.items() if k not in CORE}
        extra["entity_number"] = e.get("entity_number")
        turn = e["name"] not in fixed
        if mm == "h":
            x, d = -x, ((16 - d) % 16 if turn else d)
        elif mm == "v":
            y, d = -y, ((8 - d) % 16 if turn else d)
        if mm:
            for k in ("input_priority", "output_priority"):
                if k in extra:
                    extra[k] = _flip_priority(extra[k])
            if "mirror" in e or e["name"] in (mirror_flag or ()):
                extra["mirror"] = not e.get("mirror", False)
        for _ in range(rot // 4):
            x, y = -y, x
        if turn:
            d = (d + rot) % 16
        it = {"entity_number": e.get("entity_number"), "name": e["name"], "x": x, "y": y, "direction": d}
        if "type" in e:
            it["type"] = e["type"]
        it["extra"] = extra
        out.append(it)
    return out


def bounding_box(items, sizes=None):
    """Tile-aligned box [[x0, y0], [x1, y1]] covering every entity's footprint."""
    if not items:
        return [[0.0, 0.0], [0.0, 0.0]]
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    for it in items:
        x, y = _xy(it if "x" in it else it["position"])
        w, h = _size(it["name"], _dir(it.get("direction", 0)), sizes)
        x0, x1 = min(x0, x - w / 2), max(x1, x + w / 2)
        y0, y1 = min(y0, y - h / 2), max(y1, y + h / 2)
    return [[math.floor(x0 + 1e-9), math.floor(y0 + 1e-9)], [math.ceil(x1 - 1e-9), math.ceil(y1 - 1e-9)]]


def to_ghosts(bp, anchor, direction=0, mirror=False, sizes=None, fixed=None, mirror_flag=MIRROR_FLAG):
    """kc.place_ghosts items for building `bp` centred at `anchor`, as build_blueprint{position=anchor,
    direction=direction} would place them.

    direction: 0/4/8/12 (or the words), clockwise. mirror: False, True/"horizontal" (F) or
    "vertical" (G), applied before the rotation. sizes: {name: (tile_width, tile_height)} from
    prototypes; names missing here and in VANILLA_SIZES count as 1x1, which shifts the result if
    the blueprint has a big unknown entity. fixed: names whose prototype has
    supports_direction == false (default VANILLA_FIXED); they keep their direction.
    Returns [{name, x, y, direction, type?, extra}]: `type` = "input"/"output" on underground
    halves; `extra` holds everything else (entity_number for wires, recipe, quality, color,
    always_on, control_behavior, items, filters, ...) for the mod to apply. Snap-to-grid
    blueprints are centred like free ones (check() says so)."""
    items = transform(bp, direction, mirror, mirror_flag, fixed)
    (x0, y0), (x1, y1) = bounding_box(items, sizes)
    ax, ay = _xy(anchor)
    dx = _snap_center(ax, x1 - x0) - (x0 + x1) / 2
    dy = _snap_center(ay, y1 - y0) - (y0 + y1) / 2
    if any(("rail" in it["name"] and "signal" not in it["name"]) for it in items):
        dx, dy = 2 * math.floor(dx / 2 + 0.5), 2 * math.floor(dy / 2 + 0.5)  # rails sit on a 2x2 grid
    out = []
    for it in items:
        g = {"name": it["name"], "x": it["x"] + dx, "y": it["y"] + dy, "direction": it["direction"]}
        if "type" in it:
            g["type"] = it["type"]
        g["extra"] = it["extra"]
        out.append(g)
    return out


def wires(bp):
    """The 2.0 wires list [[entity1, connector1, entity2, connector2], ...] (connector ids:
    1 red, 2 green on the first side, 3/4 the second side of combinators, 5 copper, 6 copper of a
    power switch's second side). Pair them with to_ghosts items through extra.entity_number."""
    return [list(w) for w in _body(bp).get("wires", []) or []]


def from_entities(items, label="", wires=None, tiles=None, version=DEFAULT_VERSION, relative=True, icons=None):
    """kc.place_ghosts-style items -> {"blueprint": {...}} (2.0). Items: {name, x, y, direction?,
    type?, extra?} or blueprint entities with `position`; `extra` keys are copied into the entity.
    relative: shift positions by a whole number of tiles so the box starts near (0, 0) (keeps the
    x.5 / integer alignment). wires: 2.0 wires list over the 1-based item order."""
    ents = []
    pts = [_xy(it if "x" in it else it["position"]) for it in items]
    sx = math.floor(min(p[0] for p in pts)) if (relative and pts) else 0
    sy = math.floor(min(p[1] for p in pts)) if (relative and pts) else 0
    if sx % 2 or sy % 2:  # an even shift keeps rails on their 2x2 grid
        sx, sy = sx - sx % 2, sy - sy % 2
    for i, (it, (x, y)) in enumerate(zip(items, pts), 1):
        e = {"entity_number": i, "name": it["name"], "position": {"x": x - sx, "y": y - sy}}
        d = _dir(it.get("direction", 0))
        if d:
            e["direction"] = d
        if it.get("type"):
            e["type"] = it["type"]
        for k, v in (it.get("extra") or {}).items():
            if k not in CORE:
                e[k] = v
        ents.append(e)
    body = {"item": "blueprint", "version": version_number(version), "entities": ents}
    if label:
        body["label"] = label
    body["icons"] = icons or ([{"signal": {"name": ents[0]["name"]}, "index": 1}] if ents else [])
    if wires:
        body["wires"] = [list(w) for w in wires]
    if tiles:
        body["tiles"] = [{"name": t["name"], "position": {"x": _xy(t)[0] - sx, "y": _xy(t)[1] - sy}} for t in tiles]
    return {"blueprint": body}


# ── summary / check ──

def _counts(names):
    c = {}
    for n in names:
        c[n] = c.get(n, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def summary(bp, sizes=None, depth=3):
    """What a blueprint string holds, without placing anything.
    Blueprint: {type, label, version, entities, counts {name: n}, size [w, h] in tiles, tiles
    {name: n}, wires, recipes {recipe: n}, quality {name: n} (non-normal), parameters,
    snap_to_grid}. Book: {type, label, version, entries [{index, type, label,
    ...summary}]}. Planners: {type, label, version, settings (mappers / filters count)}."""
    t = kind(bp) or ("blueprint" if isinstance(bp, dict) and "entities" in bp else None)
    body = _body(bp)
    out = {"type": t, "label": body.get("label", ""),
           "version": version_string(body["version"]) if "version" in body else "unknown"}
    if body.get("description"):
        out["description"] = body["description"]
    if t == "blueprint":
        ents = body.get("entities", []) or []
        out["entities"] = len(ents)
        out["counts"] = _counts(e.get("name", "?") for e in ents)
        if ents:
            (x0, y0), (x1, y1) = bounding_box(transform(bp), sizes)
            out["size"] = [x1 - x0, y1 - y0]
        else:
            out["size"] = [0, 0]
        out["tiles"] = _counts(tl.get("name", "?") for tl in body.get("tiles", []) or [])
        out["wires"] = len(body.get("wires", []) or [])
        out["recipes"] = _counts(e["recipe"] for e in ents if e.get("recipe"))
        out["quality"] = _counts(e["quality"] for e in ents if e.get("quality", "normal") != "normal")
        out["parameters"] = len(body.get("parameters", []) or [])
        out["snap_to_grid"] = body.get("snap-to-grid")
    elif t == "blueprint_book":
        out["entries"] = []
        for e in body.get("blueprints", []) or []:
            s = summary(e, sizes, depth - 1) if depth > 1 else {"type": kind(e), "label": _body(e).get("label", "")}
            s["index"] = e.get("index")
            out["entries"].append(s)
        out["active_index"] = body.get("active_index", 0)
    else:
        st = body.get("settings", {}) or {}
        out["settings"] = {k: (len(v) if isinstance(v, list) else v) for k, v in sorted(st.items())}
    return out


def _check_entity(e, i, where, nums, problems):
    if not isinstance(e, dict):
        problems.append(f"{where} entity #{i} is not an object")
        return
    for k in ("entity_number", "name", "position"):
        if k not in e:
            problems.append(f"{where} entity #{i} has no {k}")
    n = e.get("entity_number")
    if n is not None:
        if n in nums:
            problems.append(f"{where} entity_number {n} is used twice")
        nums.add(n)
    if "position" in e:
        try:
            _xy(e["position"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{where} entity #{i} has a bad position {e['position']!r}")
    d = e.get("direction", 0)
    if not isinstance(d, int) or not 0 <= d <= 15:
        problems.append(f"{where} entity #{i} direction {d!r} is not 0..15")
    if e.get("type") not in (None, "input", "output"):
        problems.append(f"{where} entity #{i} type {e.get('type')!r} is not input/output")
    for k in ("connections", "neighbours"):
        if k in e:
            problems.append(f"{where} entity #{i} uses the 1.x wire field '{k}' (2.0 keeps wires in blueprint.wires)")
    unknown = sorted(set(e) - ENTITY_KEYS - {"connections", "neighbours"})
    if unknown:
        problems.append(f"{where} entity #{i} {e.get('name')}: unknown keys {unknown}")


def _uses_parameter(obj):
    if isinstance(obj, dict):
        return any(_uses_parameter(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_uses_parameter(v) for v in obj)
    return isinstance(obj, str) and obj.startswith("parameter-")


def check(bp, path="", known=None):
    """Problems as strings ([] = a well-formed 2.0 blueprint/book/planner that to_ghosts can place).
    Reports: an unknown top-level type or key, missing entity fields, duplicate entity numbers,
    directions outside 0..15, 1.x wire fields, wires to missing entities or bad connector ids,
    unknown entity keys (vs the 2.0 schemas), unresolved parameter-N placeholders, snap-to-grid.
    known: optional set of entity names that exist in this save (from prototypes); others are
    reported, so a vanilla print in a modded save is caught before placing."""
    problems = []
    if not isinstance(bp, dict):
        return [f"{path or 'blueprint'}: not an object"]
    t = kind(bp)
    where = path or t or "blueprint"
    if t is None:
        return [f"{where}: no blueprint/blueprint_book/deconstruction_planner/upgrade_planner key (keys: {sorted(bp)})"]
    extra_top = sorted(set(bp) - {t, "index"})
    if extra_top:
        problems.append(f"{where}: unknown top-level keys {extra_top}")
    body = bp[t]
    if not isinstance(body, dict):
        return problems + [f"{where}: {t} is not an object"]
    allowed = {"blueprint": BLUEPRINT_KEYS, "blueprint_book": BOOK_KEYS}.get(t, PLANNER_KEYS)
    unknown = sorted(set(body) - allowed)
    if unknown:
        problems.append(f"{where}: unknown {t} keys {unknown}")
    if t == "blueprint" and body.get("entities") and "version" in body and _major(body) < 2:
        problems.append(f"{where}: made by {version_string(body['version'])}: to_ghosts doubles its 8-way "
                        "directions, but 1.x names may differ in 2.0 (stack-inserter is bulk-inserter now); "
                        "check the names with known=")
    if t == "blueprint_book":
        for j, e in enumerate(body.get("blueprints", []) or []):
            problems += check(e, f"{where}[{e.get('index', j) if isinstance(e, dict) else j}]", known)
        return problems
    if t != "blueprint":
        return problems
    ents = body.get("entities", []) or []
    nums = set()
    for i, e in enumerate(ents):
        _check_entity(e, i, where, nums, problems)
        if known is not None and isinstance(e, dict) and e.get("name") not in known:
            problems.append(f"{where} entity #{i}: {e.get('name')} does not exist in this save")
    for j, w in enumerate(body.get("wires", []) or []):
        if not (isinstance(w, list) and len(w) == 4):
            problems.append(f"{where} wire #{j} is not [entity1, connector1, entity2, connector2]")
            continue
        for n, c in ((w[0], w[1]), (w[2], w[3])):
            if n not in nums:
                problems.append(f"{where} wire #{j} names missing entity {n}")
            if not isinstance(c, int) or not 1 <= c <= 6:
                problems.append(f"{where} wire #{j} connector {c!r} is not a wire_connector_id 1..6")
    if _uses_parameter(ents):
        problems.append(f"{where}: parameterised (parameter-N placeholders); resolve them before placing")
    if body.get("snap-to-grid"):
        problems.append(f"{where}: snap-to-grid {body['snap-to-grid']}; to_ghosts centres it like a free blueprint")
    return problems
