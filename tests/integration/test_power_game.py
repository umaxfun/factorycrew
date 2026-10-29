"""Level 2: the Python pole planner against the real game.

Lamps stand far from a grid pole that is fed by an electric-energy-interface (test-only power
source), with a wall of chests on the way where no pole may stand. The plan is computed in Python
(skills/power/lib/power.py) from the real prototypes, placed through kc.place_ghosts like Kira
does, the ghosts are revived, and the game must report every lamp and every new pole on the
grid pole's electric network.
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "skills" / "power" / "lib"))
import power  # noqa: E402

X0, Y0 = 800, 600  # an empty corner of the test map, away from the belt test
BOX = f"{{{{{X0 - 10}, {Y0 - 40}}}, {{{X0 + 110}, {Y0 + 40}}}}}"

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0 + 50}, {Y0}}}, 3)
s.force_generate_chunk_requests()
local box = {BOX}
local tiles = {{}}
for x = {X0 - 10}, {X0 + 109} do for y = {Y0 - 40}, {Y0 + 39} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = box}}) do if e.type ~= "character" then e.destroy() end end
-- the grid: a test-only power source and one medium pole next to it
s.create_entity{{name = "electric-energy-interface", position = {{{X0}, {Y0}}}, force = "player"}}
local gp = s.create_entity{{name = "medium-electric-pole", position = {{{X0} + 2.5, {Y0} + 0.5}}, force = "player"}}
-- a wall of chests across the way: wires pass over it, poles cannot stand on it
for y = {Y0 - 40}, {Y0 + 39} do for x = {X0 + 30}, {X0 + 33} do
  s.create_entity{{name = "wooden-chest", position = {{x + 0.5, y + 0.5}}, force = "player"}}
end end
-- lamps far away, in two clusters
for _, p in pairs({{{{75, -12}}, {{78, -12}}, {{81, -12}}, {{75, -9}}, {{81, -9}}, {{95, 15}}, {{98, 15}}, {{95, 18}}, {{101, 21}}}}) do
  s.create_entity{{name = "small-lamp", position = {{{X0} + p[1] + 0.5, {Y0} + p[2] + 0.5}}, force = "player"}}
end
local ents = {{}}
for _, e in pairs(s.find_entities_filtered{{area = box}}) do
  if e.type ~= "character" then
    ents[#ents + 1] = {{name = e.name, x = e.position.x, y = e.position.y,
                        w = e.prototype.tile_width, h = e.prototype.tile_height}}
  end
end
local function spec(n)
  local p = prototypes.entity[n]
  return {{name = n, size = p.tile_width, supply = p.get_supply_area_distance(), reach = p.get_max_wire_distance()}}
end
return {{entities = ents, cover = spec("substation"), reach = spec("big-electric-pole"),
         grid = spec("medium-electric-pole"), grid_pos = {{gp.position.x, gp.position.y}},
         grid_net = gp.electric_network_id}}
"""

PLACE = """
local r = kc.place_ghosts(surface, helpers.json_to_table(ITEMS))
local revived, failed = 0, {}
for _, g in pairs(surface.find_entities_filtered{name = "entity-ghost", area = BOX}) do
  local _, e = g.revive()
  if e then revived = revived + 1 else failed[#failed + 1] = {g.ghost_name, g.position.x, g.position.y} end
end
return {placed = r.placed, failed = r.failed, revived = revived, revive_failed = failed}
"""

NETS = """
local out = {}
for _, e in pairs(surface.find_entities_filtered{type = {"lamp", "electric-pole", "electric-energy-interface"}, area = BOX}) do
  out[#out + 1] = {name = e.name, type = e.type, x = e.position.x, y = e.position.y,
                   net = e.electric_network_id or false,
                   connected = e.type == "lamp" and e.is_connected_to_electric_network() or false}
end
return out
"""


def test_planned_poles_put_far_lamps_on_the_grid(stack, mcp):
    world = mcp.tool("factorio_query", lua=SETUP)
    assert world["ok"], world
    d = world["data"]
    lamps = [e for e in d["entities"] if e["name"] == "small-lamp"]
    others = [e for e in d["entities"] if e["name"] != "small-lamp"]
    assert len(lamps) == 9 and d["grid_net"], d
    blocked = set()
    for e in others:
        blocked |= {tuple(t) for t in power._foot(e["x"], e["y"], e["w"], e["h"])}
    gx, gy = d["grid_pos"]
    grid = [(gx, gy, d["grid"]["reach"], d["grid"]["supply"], d["grid"]["size"])]
    consumers = [{"x": e["x"], "y": e["y"], "w": e["w"], "h": e["h"]} for e in lamps]

    plan = power.plan(consumers, d["cover"], blocked, grid, reach_pole=d["reach"])
    assert plan["ok"], plan
    assert power.check(consumers, plan["entities"], grid, blocked) == []
    assert plan["link_poles"] >= 2, plan  # ~75 tiles from the grid: big poles needed
    print(f"\nplan: {plan['cover_poles']} cover, {plan['join_poles']} join, {plan['link_poles']} link poles")

    items = [{"name": e["name"], "x": e["x"], "y": e["y"]} for e in plan["entities"]]
    lua = PLACE.replace("ITEMS", json.dumps(json.dumps(items))).replace("BOX", BOX)
    placed = mcp.tool("factorio_query", lua=lua)
    assert placed["ok"], placed
    p = placed["data"]
    assert p["placed"] == len(items) and p["failed"] in ({}, []), p
    assert p["revived"] == len(items) and p["revive_failed"] in ({}, []), p

    q = mcp.tool("factorio_query", lua=NETS.replace("BOX", BOX))
    assert q["ok"], q
    got = q["data"]
    lamp_nets = [e for e in got if e["type"] == "lamp"]
    poles = [e for e in got if e["type"] == "electric-pole"]
    source = [e for e in got if e["type"] == "electric-energy-interface"]
    assert len(lamp_nets) == 9 and len(poles) == len(items) + 1 and len(source) == 1, got
    # joining networks can renumber them: compare with the grid pole's id read now
    grid_now = next(e["net"] for e in poles if [e["x"], e["y"]] == [gx, gy])
    assert grid_now and source[0]["net"] == grid_now, (grid_now, source, d["grid_net"])
    assert all(e["net"] == grid_now for e in poles), [e for e in poles if e["net"] != grid_now]
    assert all(e["net"] == grid_now and e["connected"] for e in lamp_nets), lamp_nets
