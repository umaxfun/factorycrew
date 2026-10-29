"""Level 2: the Python mining planner against the real game.

An iron-ore patch is made on an empty corner of the test map, the drill and belt geometry is read
from prototypes the way Kira does, the plan is computed in Python (skills/mining/lib/mining.py),
placed with kc.place_ghosts and revived. Test-only power (an electric-energy-interface and medium
poles on the plan's pole rows) runs the drills, and iron ore must arrive on the exit belt.
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT, wait_until

sys.path.insert(0, str(ROOT / "skills" / "mining" / "lib"))
import mining  # noqa: E402

X0, Y0, R = 700, 700, 8  # patch center and radius, away from the belt test's corner
BOX = "{{X0 - 30, Y0 - 30}, {X0 + 30, Y0 + 30}}".replace("X0", str(X0)).replace("Y0", str(Y0))

SETUP = """
local s = game.surfaces[1]
s.request_to_generate_chunks({X0, Y0}, 2)
s.force_generate_chunk_requests()
local box = BOX
local tiles = {}
for x = X0 - 30, X0 + 29 do for y = Y0 - 30, Y0 + 29 do tiles[#tiles + 1] = {name = "grass-1", position = {x, y}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{area = box}) do if e.type ~= "character" then e.destroy() end end
for x = X0 - RADIUS, X0 + RADIUS do for y = Y0 - RADIUS, Y0 + RADIUS do
  if (x - X0) ^ 2 + (y - Y0) ^ 2 <= RADIUS * RADIUS then
    s.create_entity{name = "iron-ore", position = {x + 0.5, y + 0.5}, amount = 20000}
  end
end end
-- something in the way inside the patch
s.create_entity{name = "wooden-chest", position = {X0 + 2.5, Y0 - 1.5}, force = "player"}
local ore, blocked = {}, {}
for _, e in pairs(s.find_entities_filtered{area = box, type = "resource"}) do
  ore[#ore + 1] = {x = e.position.x, y = e.position.y, amount = e.amount}
end
for _, e in pairs(s.find_entities_filtered{area = box}) do
  if e.type ~= "resource" and e.type ~= "character" then blocked[#blocked + 1] = {e.position.x, e.position.y} end
end
local d = prototypes.entity["electric-mining-drill"]
local v = d.vector_to_place_result
return {ore = ore, blocked = blocked,
        drill = {name = d.name, size = d.tile_width, radius = d.mining_drill_radius,
                 output = {v[1] or v.x, v[2] or v.y}, mining_speed = d.mining_speed},
        belt = {name = "transport-belt", speed = prototypes.entity["transport-belt"].belt_speed * 480}}
""".replace("BOX", BOX).replace("X0", str(X0)).replace("Y0", str(Y0)).replace("RADIUS", str(R))

PLACE = """
local r = kc.place_ghosts(surface, helpers.json_to_table(ITEMS))
local revived, failed = 0, {}
for _, g in pairs(surface.find_entities_filtered{name = "entity-ghost", area = BOX}) do
  local _, e = g.revive()
  if e then revived = revived + 1 else failed[#failed + 1] = {g.ghost_name, g.position.x, g.position.y} end
end
return {placed = r.placed, failed = r.failed, revived = revived, revive_failed = failed}
"""

# test-only power: medium poles on the plan's free pole rows, an energy interface next to the first
POWER = """
local poles = helpers.json_to_table(POLES)
local n = 0
for _, p in ipairs(poles) do
  if surface.create_entity{name = "medium-electric-pole", position = p, force = "player"} then n = n + 1 end
end
local eei
local p = poles[1]
for r = 2, 4 do
  for dx = -r, r do for dy = -r, r do
    local pos = {p[1] + dx, p[2] + dy}
    if not eei and surface.can_place_entity{name = "electric-energy-interface", position = pos, force = "player"} then
      eei = surface.create_entity{name = "electric-energy-interface", position = pos, force = "player"}
    end
  end end
end
eei.power_production = 1e9
eei.electric_buffer_size = 1e10
return {poles = n, eei = {eei.position.x, eei.position.y}}
"""

STATUS = """
local working, total = 0, 0
for _, d in pairs(surface.find_entities_filtered{type = "mining-drill", area = BOX}) do
  total = total + 1
  if d.status == defines.entity_status.working or d.status == defines.entity_status.waiting_for_space_in_destination then
    working = working + 1
  end
end
local b = surface.find_entity("transport-belt", {EX, EY})
local ore = b and (b.get_transport_line(1).get_item_count("iron-ore") + b.get_transport_line(2).get_item_count("iron-ore")) or -1
return {working = working, total = total, ore = ore}
"""


def pick_poles(rows, spacing=6):
    """One pole every `spacing` tiles along each pole row, aligned across rows (wire reach 9)."""
    poles = []
    for row in rows:
        along = 0 if len({p[1] for p in row}) == 1 else 1
        base = min(p[along] for r in rows for p in r)
        poles += [p for p in row if (p[along] - base) % spacing == 0]
    return poles


def test_planned_outpost_mines_ore_onto_the_exit_belt(stack, mcp):
    world = mcp.tool("factorio_query", lua=SETUP)
    assert world["ok"], world
    w = world["data"]
    ore, blocked = w["ore"], [tuple(p) for p in w["blocked"]]
    assert len(ore) > 150 and blocked, (len(ore), blocked)
    r = mining.plan(ore, blocked, toward=(X0 - 60, Y0), drill=w["drill"], belt=w["belt"])
    assert r["ok"], r
    assert mining.check(r["entities"], ore, blocked, drill=w["drill"], belt=w["belt"], exit=r["exit"]) == []
    st = r["stats"]
    assert st["coverage"] == 100.0 and r["exit"]["x"] < X0, (st, r["exit"])
    print(f"\nplan: {st['drills']} drills, {st['lanes']} lanes, {st['belts']} belts, "
          f"coverage {st['coverage']}%, exit {r['exit']}")

    lua = PLACE.replace("ITEMS", json.dumps(json.dumps(r["entities"]))).replace("BOX", BOX)
    placed = mcp.tool("factorio_query", lua=lua)
    assert placed["ok"], placed
    d = placed["data"]
    assert d["placed"] == len(r["entities"]) and d["failed"] in ({}, []), d
    assert d["revived"] == len(r["entities"]) and d["revive_failed"] in ({}, []), d

    poles = pick_poles(r["pole_rows"])
    pw = mcp.tool("factorio_query", lua=POWER.replace("POLES", json.dumps(json.dumps(poles))))
    assert pw["ok"] and pw["data"]["poles"] == len(poles), pw

    ex = r["exit"]
    status = STATUS.replace("BOX", BOX).replace("EX", str(ex["x"])).replace("EY", str(ex["y"]))

    def all_working():
        q = mcp.tool("factorio_query", lua=status)
        return q["ok"] and q["data"]["total"] == st["drills"] and q["data"]["working"] == st["drills"] and q["data"]
    wait_until(all_working, timeout=30, step=0.5, what="every drill powered and mining")

    def arrived():
        q = mcp.tool("factorio_query", lua=status)
        return q["ok"] and q["data"]["ore"] > 0 and q["data"]
    got = wait_until(arrived, timeout=60, step=0.5, what="iron ore on the exit belt")
    print(f"exit belt: {got['ore']} iron ore; drills working {got['working']}/{got['total']}")
