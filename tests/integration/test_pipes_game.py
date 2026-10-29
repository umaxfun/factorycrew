"""Level 2: the Python oil-field planner against the real game.

Wells are made on an empty patch, the planner (skills/pipes/lib/pipes.py) lays pumpjacks,
pipes and pipe-to-ground pairs with the output offsets and reach read from the prototypes, the
ghosts are placed with kc.place_ghosts and revived, and then the game's own fluid segments must
show every pumpjack in the sink's network, with a foreign pipe line through the field left out.
A second test pins what `pipe_to_ground_reach` means: pipes.check must agree with the game on
which pairs connect.
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "skills" / "pipes" / "lib"))
import pipes  # noqa: E402

X0, Y0 = 900, 600  # an empty corner of the test map, away from the belt test
BOX = f"{{{{{X0 - 12}, {Y0 - 12}}}, {{{X0 + 52}, {Y0 + 42}}}}}"

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0 + 20}, {Y0 + 15}}}, 3)
s.force_generate_chunk_requests()
local box = {BOX}
local tiles = {{}}
for x = {X0 - 12}, {X0 + 51} do for y = {Y0 - 12}, {Y0 + 41} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = box}}) do if e.type ~= "character" then e.destroy() end end
-- someone else's pipe line through the middle of the field: it must stay a separate network
for y = {Y0 - 4}, {Y0 + 34} do s.create_entity{{name = "pipe", position = {{{X0 + 16} + 0.5, y + 0.5}}, force = "player"}} end
local blocked = {{}}
for _, e in pairs(s.find_entities_filtered{{area = box}}) do
  if e.type ~= "character" then blocked[#blocked + 1] = {{e.position.x, e.position.y}} end
end
-- output tile per pumpjack direction, from the prototype's fluidbox
local pj = prototypes.entity["pumpjack"]
local step = {{[0] = {{0, -1}}, [4] = {{1, 0}}, [8] = {{0, 1}}, [12] = {{-1, 0}}}}
local offs = {{}}
for _, fb in pairs(pj.fluidbox_prototypes) do
  if fb.production_type == "output" then
    local c = fb.pipe_connections[1]
    for i, d in ipairs({{0, 4, 8, 12}}) do
      local p = c.positions[i]
      local od = ((c.direction or 0) + d) % 16
      offs[tostring(d)] = {{p.x + step[od][1], p.y + step[od][2]}}
    end
  end
end
return {{blocked = blocked, offsets = offs, size = pj.tile_width,
        reach = prototypes.entity["pipe-to-ground"].max_underground_distance}}
"""

BUILD = """
local data = helpers.json_to_table(DATA)
for _, w in pairs(data.wells) do
  surface.create_entity{name = "crude-oil", position = {w[1], w[2]}, amount = 300000}
end
local r = kc.place_ghosts(surface, data.items)
local revived, failed = 0, {}
for _, g in pairs(surface.find_entities_filtered{name = "entity-ghost", area = BOX}) do
  local _, e = g.revive()
  if e then revived = revived + 1 else failed[#failed + 1] = {g.ghost_name, g.position.x, g.position.y} end
end
return {placed = r.placed, failed = r.failed, revived = revived, revive_failed = failed}
"""

SEGMENTS = """
local data = helpers.json_to_table(DATA)
-- the game's own fluid connections: 2.1 `fluidbox_neighbours`, 2.0 `neighbours`
local function nbrs(e)
  local ok, n = pcall(function() return e.fluidbox_neighbours end)
  if not ok or n == nil then n = e.neighbours end
  local out = {}
  for _, list in pairs(n or {}) do for _, x in pairs(list) do out[#out + 1] = x end end
  return out
end
local function network(start)
  local seen, todo = {[start.unit_number] = true}, {start}
  while #todo > 0 do
    local e = table.remove(todo)
    for _, n in pairs(nbrs(e)) do
      if not seen[n.unit_number] then seen[n.unit_number] = true; todo[#todo + 1] = n end
    end
  end
  return seen
end
local sink = surface.find_entity("pipe", {data.sink[1], data.sink[2]})
local foreign = surface.find_entity("pipe", {data.foreign[1], data.foreign[2]})
local net = network(sink)
local jacks, size = {}, 0
for _ in pairs(net) do size = size + 1 end
for _, e in pairs(surface.find_entities_filtered{name = "pumpjack", area = BOX}) do
  jacks[#jacks + 1] = {x = e.position.x, y = e.position.y, dir = e.direction,
                       in_sink_net = net[e.unit_number] == true, links = #nbrs(e)}
end
return {jacks = jacks, net_size = size, foreign_in_net = net[foreign.unit_number] == true}
"""

PROBE = """
-- the game's own fluid connections: 2.1 `fluidbox_neighbours`, 2.0 `neighbours`
local function nbrs(e)
  local ok, n = pcall(function() return e.fluidbox_neighbours end)
  if not ok or n == nil then n = e.neighbours end
  local out = {}
  for _, list in pairs(n or {}) do for _, x in pairs(list) do out[#out + 1] = x end end
  return out
end
local reach = prototypes.entity["pipe-to-ground"].max_underground_distance
local out = {}
for row, k in ipairs({reach - 1, reach, reach + 1, reach + 2}) do
  local y = PROBE_Y + 2 * row + 0.5
  local a = surface.create_entity{name = "pipe-to-ground", position = {PROBE_X + 0.5, y}, direction = defines.direction.west, force = "player"}
  local b = surface.create_entity{name = "pipe-to-ground", position = {PROBE_X + 0.5 + k, y}, direction = defines.direction.east, force = "player"}
  local linked = false
  for _, n in pairs(nbrs(a)) do
    if n == b then linked = true end
  end
  out[#out + 1] = {k = k, linked = linked}
end
return out
"""


def _q(mcp, lua, **subs):
    for k, v in subs.items():
        lua = lua.replace(k, v)
    r = mcp.tool("factorio_query", lua=lua)
    assert r["ok"], r
    return r["data"]


def test_planned_oil_field_is_one_fluid_segment(stack, mcp):
    world = _q(mcp, SETUP)
    offsets = {int(k): tuple(v) for k, v in world["offsets"].items()}
    print(f"\npumpjack outputs from the prototype: {offsets}, pipe-to-ground reach {world['reach']}")
    assert offsets == pipes.PUMPJACK_OUTPUTS, "the vanilla default offsets disagree with the game"
    blocked = [tuple(p) for p in world["blocked"]]
    foreign = [(X0 + 16.5, Y0 + y + 0.5) for y in range(-4, 35)]
    wells = [(X0 + x + 0.5, Y0 + y + 0.5) for x, y in [
        (2, 2), (8, 1), (12, 6), (3, 9), (9, 12), (21, 3), (27, 2), (22, 10), (28, 14),
        (4, 18), (11, 22), (20, 20), (26, 26), (6, 28), (14, 30)]]
    r = pipes.oil_field(wells, blocked, pumpjack_size=world["size"], output_offsets=offsets,
                        pipe_to_ground_reach=world["reach"], toward=(X0 - 60, Y0 + 15), foreign_pipes=foreign)
    assert r["ok"] and r["problems"] == [], r
    print(f"field: {r['pumpjacks']} pumpjacks, {r['pipe_tiles']} pipe tiles -> {r['pipes']} pipes + "
          f"{r['pipe_to_ground']} pipe-to-ground, sink {r['sink']}")
    assert r["pipe_to_ground"] >= 2

    data = json.dumps({"wells": wells, "items": r["entities"]})
    built = _q(mcp, BUILD, DATA=json.dumps(data), BOX=BOX)
    assert built["placed"] == len(r["entities"]) and built["failed"] in ({}, []), built
    assert built["revived"] == len(r["entities"]) and built["revive_failed"] in ({}, []), built

    segs = _q(mcp, SEGMENTS, DATA=json.dumps(json.dumps({"sink": r["sink"], "foreign": list(foreign[0])})), BOX=BOX)
    jacks = segs["jacks"]
    assert len(jacks) == len(wells), jacks
    want = {(e["x"], e["y"]): e["direction"] for e in r["entities"] if e["name"] == "pumpjack"}
    assert {(j["x"], j["y"]): j["dir"] for j in jacks} == want
    print(f"game network from the sink: {segs['net_size']} entities (planned {len(r['entities'])})")
    assert all(j["links"] >= 1 for j in jacks), [j for j in jacks if j["links"] < 1]
    assert all(j["in_sink_net"] for j in jacks), [j for j in jacks if not j["in_sink_net"]]
    assert segs["net_size"] == len(r["entities"]), segs["net_size"]  # every planned piece is in it
    assert not segs["foreign_in_net"], "the planner's network merged with the foreign pipe line"

def test_pipe_to_ground_reach_means_what_check_says(stack, mcp):
    probe = _q(mcp, PROBE, PROBE_X=str(X0 + 38), PROBE_Y=str(Y0 + 28))  # cleared, east of the field
    reach = probe[1]["k"]
    print(f"\npipe-to-ground pairs in game (reach {reach}): {probe}")
    for p in probe:
        pair = [{"name": "pipe-to-ground", "x": 0.5, "y": 0.5, "direction": 12},
                {"name": "pipe-to-ground", "x": 0.5 + p["k"], "y": 0.5, "direction": 4}]
        assert (pipes.check(pair, pipe_to_ground_reach=reach) == []) == p["linked"], p
