"""Level 2: the Python beacon placer against the real game.

A row of eight assemblers, a 5x5 oil refinery, a chemical plant and three pumpjacks on crude oil
stand on a patch powered by test-only electric-energy-interfaces under substations. The layout is
computed in Python (skills/beacons/lib/beacons.py) from the real prototypes (beacon size, supply
area distance, profile, distribution effectivity, module effects), placed through kc.place_ghosts
like Kira does, the ghosts are revived and filled with speed modules. Then the game must agree
with the prediction for every machine: the number of beacons that reach it, and its `effects`.
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT, wait_until

sys.path.insert(0, str(ROOT / "skills" / "beacons" / "lib"))
import beacons  # noqa: E402

X0, Y0 = 600, 1000  # an empty corner of the test map, away from the other tests
BOX = f"{{{{{X0 - 20}, {Y0 - 25}}}, {{{X0 + 70}, {Y0 + 45}}}}}"
MODULE = "speed-module"

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0 + 25}, {Y0 + 10}}}, 3)
s.force_generate_chunk_requests()
local box = {BOX}
local tiles = {{}}
for x = {X0 - 20}, {X0 + 69} do for y = {Y0 - 25}, {Y0 + 44} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = box}}) do if e.type ~= "character" then e.destroy() end end
-- test-only power: one electric-energy-interface under every substation
for _, sx in pairs({{-1, 17, 35, 53}}) do for _, sy in pairs({{-9, 9, 27}}) do
  s.create_entity{{name = "substation", position = {{{X0} + sx, {Y0} + sy}}, force = "player"}}
  s.create_entity{{name = "electric-energy-interface", position = {{{X0} + sx + 2, {Y0} + sy}}, force = "player"}}
end end
local machines = {{}}
local function add(name, x, y, recipe)
  local e = s.create_entity{{name = name, position = {{{X0} + x, {Y0} + y}}, force = "player"}}
  if recipe then e.set_recipe(recipe) end
  local p = e.prototype
  machines[#machines + 1] = {{name = name, x = e.position.x, y = e.position.y, w = p.tile_width, h = p.tile_height,
                              collision_box = p.collision_box}}
end
for i = 0, 7 do add("assembling-machine-2", 3 * i + 0.5, 0.5, "iron-gear-wheel") end
add("oil-refinery", 40.5, 0.5, "basic-oil-processing")
add("chemical-plant", 30.5, 12.5, "sulfuric-acid")
for _, p in pairs({{{{5.5, 20.5}}, {{12.5, 22.5}}, {{8.5, 30.5}}}}) do
  s.create_entity{{name = "crude-oil", position = {{{X0} + p[1], {Y0} + p[2]}}, amount = 300000}}
  add("pumpjack", p[1], p[2])
end
local blocked = {{}}
for _, e in pairs(s.find_entities_filtered{{area = box, type = {{"electric-pole", "electric-energy-interface"}}}}) do
  blocked[#blocked + 1] = {{x = e.position.x, y = e.position.y, w = e.prototype.tile_width, h = e.prototype.tile_height}}
end
local b = prototypes.entity["beacon"]
local slots = b.module_inventory_size
if type(slots) == "function" then slots = b.module_inventory_size() end
return {{machines = machines, blocked = blocked, size = b.tile_width, supply = b.get_supply_area_distance(),
         profile = b.profile, effectivity = b.distribution_effectivity, slots = slots,
         module = prototypes.item["{MODULE}"].module_effects}}
"""

PLACE = """
local r = kc.place_ghosts(surface, helpers.json_to_table(ITEMS))
local revived, filled, failed = 0, 0, {}
for _, g in pairs(surface.find_entities_filtered{name = "entity-ghost", area = BOX}) do
  local _, e = g.revive()
  if e then
    revived = revived + 1
    filled = filled + e.get_inventory(defines.inventory.beacon_modules).insert{name = "MODULE", count = SLOTS}
  else failed[#failed + 1] = {g.ghost_name, g.position.x, g.position.y} end
end
return {placed = r.placed, failed = r.failed, revived = revived, filled = filled, revive_failed = failed,
        beacons = surface.count_entities_filtered{name = "beacon", area = BOX}}
"""

READ = """
local out = {}
for _, m in pairs(helpers.json_to_table(MACHINES)) do
  local e = surface.find_entities_filtered{name = m.name, position = {m.x, m.y}, limit = 1}[1]
  local ok, bs = pcall(function() return e.get_beacons() end)
  local fx = e.effects or {}
  out[#out + 1] = {x = e.position.x, y = e.position.y, beacons = ok and bs and #bs or -1,
                   speed = fx.speed or 0, consumption = fx.consumption or 0, quality = fx.quality or 0}
end
return out
"""


def test_beacon_layout_matches_the_games_effects(stack, mcp):
    world = mcp.tool("factorio_query", lua=SETUP)
    assert world["ok"], world
    w = world["data"]
    machines = w["machines"]
    blocked = set()
    for e in w["blocked"]:
        blocked |= beacons.footprint(e["x"], e["y"], e["w"], e["h"])
    profile = list(w["profile"]) if w.get("profile") else None
    per_beacon = beacons.transmitted([w["module"]] * int(w["slots"]), w["effectivity"])
    spec = dict(size=w["size"], supply_area_distance=w["supply"], profile=profile)
    r = beacons.place(machines, blocked, beacon_effect=per_beacon, min_utility=0.1, **spec)
    assert beacons.check(r, machines, blocked, **spec) == []
    want = [m["beacons"] for m in r["machines"]]
    assert all(n >= 1 for n in want) and max(want) >= 8, want
    print(f"\n{r['beacons']} beacons, per machine {want}, per beacon {per_beacon}")

    lua = (PLACE.replace("ITEMS", json.dumps(json.dumps(r["entities"]))).replace("BOX", BOX)
           .replace("MODULE", MODULE).replace("SLOTS", str(int(w["slots"]))))
    placed = mcp.tool("factorio_query", lua=lua)
    assert placed["ok"], placed
    d = placed["data"]
    assert d["placed"] == r["beacons"] and d["failed"] in ({}, []), d
    assert d["revived"] == r["beacons"] == d["beacons"] and d["revive_failed"] in ({}, []), d
    assert d["filled"] == r["beacons"] * int(w["slots"]), d

    read = READ.replace("MACHINES", json.dumps(json.dumps(machines)))

    def settled():  # beacons need a moment on the grid before they transmit
        q = mcp.tool("factorio_query", lua=read)
        return q["ok"] and all((g["speed"] > 0) == (n > 0) for g, n in zip(q["data"], want)) and q["data"]
    game = wait_until(settled, timeout=20, step=0.5, what="beacon effects on the machines")
    print("game:", [(g["beacons"], round(g["speed"], 4), round(g["consumption"], 4), round(g["quality"], 4)) for g in game])

    worst = 0.0
    for m, plan, g in zip(machines, r["machines"], game):
        if g["beacons"] >= 0:
            assert g["beacons"] == plan["beacons"], (m["name"], m["x"], m["y"], plan, g)
        # speed modules carry a negative quality effect; the game floors a machine's quality at 0
        want_fx = dict(plan["effects"], quality=max(0.0, plan["effects"].get("quality", 0.0)))
        for k in ("speed", "consumption", "quality"):
            diff = abs(g[k] - want_fx.get(k, 0.0))
            worst = max(worst, diff)
            assert diff < 1e-3, (m["name"], m["x"], m["y"], k, plan, g)
    print(f"largest |game - predicted| effect: {worst:.2e}")
