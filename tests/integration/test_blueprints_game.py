"""Level 2: blueprint strings from skills/blueprints/lib/blueprints.py against the real game.

A small blueprint (belts, an underground pair, a splitter, an inserter, an assembler with a recipe,
a coloured lamp, a pole, a chest, an accumulator and one red wire) is encoded in Python, imported
into a blueprint item in a script inventory (game.create_inventory + LuaItemStack.import_stack), and
built with build_blueprint at fractional and whole positions in all four directions, plain and
mirrored (the game cannot flip a print from script, so the mirrored cases build our flipped copy).
Sizes and the entities that cannot turn (supports_direction == false) come from the prototypes. The ghosts the game makes must match blueprints.to_ghosts: names, positions, directions,
underground types. A real 2.0 print (Red Circuits Block, 254 entities) is built the same way, and
the game's own export of our string must decode to the same contents.
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "skills" / "blueprints" / "lib"))
import blueprints  # noqa: E402

X0, Y0 = 900, 900  # an empty corner of the test map, away from the belts test
E, S, W, N = 4, 8, 12, 0

SMALL = [
    {"name": "transport-belt", "x": 0.5, "y": 0.5, "direction": E},
    {"name": "transport-belt", "x": 1.5, "y": 0.5, "direction": E},
    {"name": "underground-belt", "x": 2.5, "y": 0.5, "direction": E, "type": "input"},
    {"name": "underground-belt", "x": 5.5, "y": 0.5, "direction": E, "type": "output"},
    {"name": "splitter", "x": 7.5, "y": 1.0, "direction": E, "extra": {"input_priority": "left"}},
    {"name": "inserter", "x": 0.5, "y": 2.5, "direction": N},
    {"name": "assembling-machine-1", "x": 2.5, "y": 4.5, "extra": {"recipe": "iron-gear-wheel"}},
    {"name": "small-lamp", "x": 5.5, "y": 3.5, "extra": {"color": {"r": 1, "g": 0, "b": 0, "a": 1}, "always_on": True}},
    {"name": "medium-electric-pole", "x": 4.5, "y": 2.5},
    {"name": "wooden-chest", "x": 6.5, "y": 5.5},
    {"name": "accumulator", "x": 9.0, "y": 4.0},
]
WIRES = [[8, 1, 10, 1]]  # lamp red -> chest red

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0}, {Y0}}}, 3)
s.force_generate_chunk_requests()
local tiles = {{}}
for x = {X0 - 45}, {X0 + 44} do for y = {Y0 - 45}, {Y0 + 44} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = {{{{{X0 - 45}, {Y0 - 45}}}, {{{X0 + 45}, {Y0 + 45}}}}}}}) do
  if e.type ~= "character" then e.destroy() end
end
local sizes, fixed = {{}}, {{}}
for _, n in pairs(helpers.json_to_table(NAMES)) do
  local p = prototypes.entity[n]
  sizes[n] = {{p.tile_width, p.tile_height}}
  if not p.supports_direction then fixed[#fixed + 1] = n end
end
return {{sizes = sizes, fixed = fixed}}
"""

BUILD = f"""
local inv = game.create_inventory(1)
local stack = inv[1]
stack.set_stack{{name = "blueprint"}}
local out = {{}}
for i, c in ipairs(helpers.json_to_table(CASES)) do
  local res = stack.import_stack(c.s)
  local ghosts = stack.build_blueprint{{surface = surface, force = "player", position = {{c.x, c.y}},
                                        direction = c.d, build_mode = defines.build_mode.forced}}
  local got = {{}}
  for _, g in pairs(ghosts) do
    local r = {{name = g.ghost_name, x = g.position.x, y = g.position.y, direction = g.direction}}
    if g.ghost_type == "underground-belt" then r.type = g.belt_to_ground_type end
    local ok, rec = pcall(function() return g.get_recipe() end)
    if ok and rec then r.recipe = rec.name end
    local okp, pr = pcall(function() return g.splitter_input_priority end)
    if okp and pr and g.ghost_type == "splitter" then r.input_priority = pr end
    got[#got + 1] = r
  end
  for _, g in pairs(ghosts) do g.destroy() end
  out[i] = {{import = res, ghosts = got}}
  if c.export then out[i].export = stack.export_stack() end
end
inv.destroy()
return out
"""


def _key(g):
    return (g["name"], round(g["x"], 3), round(g["y"], 3))


def _norm(ghosts):
    return sorted((g["name"], round(g["x"], 3), round(g["y"], 3), g["direction"], g.get("type")) for g in ghosts)


def _build(mcp, cases):
    lua = BUILD.replace("CASES", json.dumps(json.dumps(cases)))
    r = mcp.tool("factorio_query", lua=lua)
    assert r["ok"], r
    return r["data"]


def _setup(mcp, names):
    r = mcp.tool("factorio_query", lua=SETUP.replace("NAMES", json.dumps(json.dumps(sorted(names)))))
    assert r["ok"], r
    d = r["data"]
    fixed = d["fixed"] if isinstance(d["fixed"], list) else []  # an empty Lua table comes back as {}
    return {k: tuple(v) for k, v in d["sizes"].items()}, set(fixed)


def test_build_blueprint_matches_to_ghosts(stack, mcp):
    bp = blueprints.from_entities(SMALL, label="kc test", wires=WIRES)
    assert blueprints.check(bp) == []
    sizes, fixed = _setup(mcp, {e["name"] for e in SMALL})
    assert {"small-lamp", "wooden-chest", "accumulator", "medium-electric-pole"} <= fixed, fixed
    cases, want = [], []
    for mirror in (False, "horizontal", "vertical"):
        # the game cannot flip a print from script: build our mirrored copy unrotated-in-Python
        flipped = blueprints.from_entities(blueprints.transform(bp, 0, mirror, fixed=fixed), relative=False) if mirror else bp
        s = blueprints.encode(flipped)
        for d in (N, E, S, W):
            for ax, ay in ((X0 + 0.3, Y0 + 0.7), (X0 + 0.5, Y0 + 0.5), (X0 + 0.0, Y0 + 0.0), (X0 - 0.6, Y0 + 0.2)):
                cases.append({"s": s, "x": ax, "y": ay, "d": d, "export": not mirror and d == N})
                want.append((mirror, d, (ax, ay), blueprints.to_ghosts(bp, (ax, ay), d, mirror, sizes, fixed)))
    got = _build(mcp, cases)
    bad = []
    for (mirror, d, anchor, w), g in zip(want, got):
        assert g["import"] == 0, (mirror, d, g)
        if _norm(w) != _norm(g["ghosts"]):
            bad.append({"mirror": mirror, "dir": d, "anchor": anchor,
                        "ours_only": sorted(set(_norm(w)) - set(_norm(g["ghosts"])))[:6],
                        "game_only": sorted(set(_norm(g["ghosts"])) - set(_norm(w)))[:6]})
    assert not bad, json.dumps(bad[:4], indent=1)
    # settings survive the trip through the game: recipe, splitter priority (swapped by a mirror)
    for (mirror, d, anchor, w), g in zip(want, got):
        gm = {_key(x): x for x in g["ghosts"]}
        for x in w:
            if "recipe" in x["extra"]:
                assert gm[_key(x)].get("recipe") == x["extra"]["recipe"], (mirror, d, gm[_key(x)])
            if "input_priority" in x["extra"]:
                assert gm[_key(x)].get("input_priority") == x["extra"]["input_priority"], (mirror, d, gm[_key(x)])
    # the game's own export of our string decodes to the same contents
    exported = [g["export"] for g in got if g.get("export")]
    assert exported
    back = blueprints.decode(exported[0])
    assert blueprints.check(back) == [], blueprints.check(back)
    ours, theirs = blueprints.summary(bp, sizes), blueprints.summary(back, sizes)
    for k in ("counts", "size", "wires", "recipes"):
        assert ours[k] == theirs[k], (k, ours[k], theirs[k])
    lamp = [e for e in back["blueprint"]["entities"] if e["name"] == "small-lamp"][0]
    assert lamp.get("always_on") is True and lamp["color"]["r"] == 1, lamp


def test_real_print_builds_where_to_ghosts_says(stack, mcp):
    s = (ROOT / "skills" / "blueprints" / "examples" / "red-circuits-block.txt").read_text()
    bp = blueprints.decode(s)
    assert blueprints.check(bp) == []
    sizes, fixed = _setup(mcp, set(blueprints.summary(bp)["counts"]))
    cases, want = [], []
    for d, anchor in ((N, (X0 + 0.4, Y0 - 0.2)), (E, (X0 - 1.0, Y0 + 3.5)), (S, (X0, Y0)), (W, (X0 + 2.7, Y0 + 0.5))):
        cases.append({"s": s, "x": anchor[0], "y": anchor[1], "d": d})
        want.append(blueprints.to_ghosts(bp, anchor, d, False, sizes, fixed))
    got = _build(mcp, cases)
    for d, w, g in zip((N, E, S, W), want, got):
        assert g["import"] == 0, g["import"]
        assert len(g["ghosts"]) == len(w) == 254, (d, len(g["ghosts"]))
        diff = sorted(set(_norm(w)) ^ set(_norm(g["ghosts"])))
        assert not diff, (d, diff[:8])
