"""Level 2: the Python belt router against the real game.

The route is computed in Python (skills/belts/lib/belts.py), placed through the mod's
kc.place_ghosts like Kira does, the ghosts are revived into real belts, and an item put on the
first belt must come out at the last one, through an underground pair under a wall.
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT, wait_until

sys.path.insert(0, str(ROOT / "skills" / "belts" / "lib"))
import belts  # noqa: E402

X0, Y0 = 600, 600  # an empty corner of the test map

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0 + 20}, {Y0}}}, 2)
s.force_generate_chunk_requests()
local box = {{{{{X0 - 5}, {Y0 - 15}}}, {{{X0 + 45}, {Y0 + 15}}}}}
local tiles = {{}}
for x = {X0 - 5}, {X0 + 44} do for y = {Y0 - 15}, {Y0 + 14} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = box}}) do if e.type ~= "character" then e.destroy() end end
-- a wall of chests across the whole box at x = X0+10 (too long to go around), and a block
-- of chests in the way further on that the belt must turn around
for y = {Y0 - 15}, {Y0 + 14} do s.create_entity{{name = "wooden-chest", position = {{{X0 + 10} + 0.5, y + 0.5}}, force = "player"}} end
for x = {X0 + 20}, {X0 + 25} do for y = {Y0 - 3}, {Y0 + 3} do
  s.create_entity{{name = "wooden-chest", position = {{x + 0.5, y + 0.5}}, force = "player"}}
end end
local blocked = {{}}
for _, e in pairs(s.find_entities_filtered{{area = box}}) do
  if e.type ~= "character" then blocked[#blocked + 1] = {{e.position.x, e.position.y}} end
end
local ug = prototypes.entity["underground-belt"]
return {{blocked = blocked, reach = ug.max_underground_distance}}
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

PAIRS = """
local out = {}
for _, e in pairs(surface.find_entities_filtered{type = "underground-belt", area = BOX}) do
  -- 2.0: `neighbours`; 2.1 renamed it `underground_belt_neighbour`
  local ok, n = pcall(function() return e.underground_belt_neighbour end)
  if not ok then n = e.neighbours end
  out[#out + 1] = {x = e.position.x, y = e.position.y, kind = e.belt_to_ground_type, dir = e.direction,
                   pair = n and {n.position.x, n.position.y} or false}
end
return out
"""


def box():
    return f"{{{{{X0 - 5}, {Y0 - 15}}}, {{{X0 + 45}, {Y0 + 15}}}}}"


def test_routed_belt_carries_an_item_through_an_underground(stack, mcp):
    world = mcp.tool("factorio_query", lua=SETUP)
    assert world["ok"], world
    blocked = [tuple(p) for p in world["data"]["blocked"]]
    start, goal = (X0 + 0.5, Y0 + 0.5), (X0 + 40.5, Y0 + 0.5)
    r = belts.route(start, goal, blocked, max_underground=world["data"]["reach"], start_dir="east", goal_dir="east")
    assert r["ok"], r
    assert belts.check(r["entities"], blocked, max_underground=world["data"]["reach"]) == []
    assert r["undergrounds"] >= 1 and r["turns"] >= 2, r  # under the wall, around the block
    print(f"\nroute: {r['belts']} belts, {r['undergrounds']} underground pairs, {r['turns']} turns")

    lua = PLACE.replace("ITEMS", json.dumps(json.dumps(r["entities"]))).replace("BOX", box())
    placed = mcp.tool("factorio_query", lua=lua)
    assert placed["ok"], placed
    d = placed["data"]
    assert d["placed"] == len(r["entities"]) and d["failed"] in ({}, []), d
    assert d["revived"] == len(r["entities"]) and d["revive_failed"] in ({}, []), d

    # every underground half got its intended type and is paired with its partner
    q = mcp.tool("factorio_query", lua=PAIRS.replace("BOX", box()))
    assert q["ok"], q
    pairs = q["data"]
    want = {(e["x"], e["y"]): e["type"] for e in r["entities"] if e.get("type")}
    assert {(p["x"], p["y"]): p["kind"] for p in pairs} == want, pairs
    assert all(p["pair"] for p in pairs), pairs

    # the real test: an item put on the first belt arrives on the last one
    first, last = r["entities"][0], r["entities"][-1]
    ins = mcp.tool("factorio_query", lua=(
        f'local b = surface.find_entity("transport-belt", {{{first["x"]}, {first["y"]}}}) '
        f'return b.get_transport_line(1).insert_at_back({{name = "iron-plate", count = 1}})'))
    assert ins["ok"] and ins["data"] is True, ins

    def arrived():
        q = mcp.tool("factorio_query", lua=(
            f'local b = surface.find_entity("transport-belt", {{{last["x"]}, {last["y"]}}}) '
            f'return b.get_transport_line(1).get_item_count("iron-plate") + b.get_transport_line(2).get_item_count("iron-plate")'))
        return q["ok"] and q["data"] > 0
    # ~55 tiles of yellow belt is ~30 s of game time; the test game runs at speed 4
    wait_until(arrived, timeout=40, step=0.5, what="iron plate reaching the end of the routed belt")
