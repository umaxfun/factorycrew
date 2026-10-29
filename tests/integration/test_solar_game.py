"""Level 2: the Python solar planner against the real game.

The day/night cycle and the prototypes are read from the live game, the ratio and the cell are
computed in Python (skills/solar/lib/solar.py), a small field is tiled over an area with one
cell blocked by chests, placed through the mod's kc.place_ghosts like Kira does, revived, and
every panel and accumulator must end up on ONE electric network (electric_network_id).
"""
from __future__ import annotations

import json
import sys

from conftest import ROOT, wait_until

sys.path.insert(0, str(ROOT / "skills" / "solar" / "lib"))
import solar  # noqa: E402

X0, Y0 = 800, 800  # an empty corner of the test map, away from the belts test

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0 + 30}, {Y0 + 20}}}, 3)
s.force_generate_chunk_requests()
local box = {{{{{X0 - 4}, {Y0 - 4}}}, {{{X0 + 60}, {Y0 + 42}}}}}
local tiles = {{}}
for x = {X0 - 4}, {X0 + 59} do for y = {Y0 - 4}, {Y0 + 41} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = box}}) do if e.type ~= "character" then e.destroy() end end
-- chests in the middle cell of the bottom row: the field must skip that cell
for x = {X0 + 25}, {X0 + 27} do
  s.create_entity{{name = "wooden-chest", position = {{x + 0.5, {Y0 + 27} + 0.5}}, force = "player"}}
end
local blocked = {{}}
for _, e in pairs(s.find_entities_filtered{{area = box}}) do
  if e.type ~= "character" then blocked[#blocked + 1] = {{e.position.x, e.position.y}} end
end
local sp = 100
pcall(function() sp = s.get_property("solar-power") end)
local panel, acc, sub = prototypes.entity["solar-panel"], prototypes.entity["accumulator"], prototypes.entity["substation"]
local es = acc.electric_energy_source_prototype
return {{
  blocked = blocked,
  day = {{ticks_per_day = s.ticks_per_day, dusk = s.dusk, evening = s.evening, morning = s.morning, dawn = s.dawn,
         always_day = s.always_day, freeze_daytime = s.freeze_daytime, daytime = s.daytime,
         solar_power_multiplier = s.solar_power_multiplier, solar_power = sp}},
  panel = {{name = panel.name, width = panel.tile_width, height = panel.tile_height,
           peak_kw = panel.get_max_energy_production() * 60 / 1000}},
  acc = {{name = acc.name, width = acc.tile_width, height = acc.tile_height, capacity_mj = es.buffer_capacity / 1e6,
         input_kw = es.input_flow_limit * 60 / 1000, output_kw = es.output_flow_limit * 60 / 1000}},
  pole = {{name = sub.name, width = sub.tile_width, height = sub.tile_height,
          supply = sub.get_supply_area_distance(), reach = sub.get_max_wire_distance()}},
}}
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

NETWORKS = """
local ids, counts, none = {}, {}, 0
for _, e in pairs(surface.find_entities_filtered{type = {"solar-panel", "accumulator", "electric-pole"}, area = BOX}) do
  local id = e.electric_network_id
  if id then ids[tostring(id)] = (ids[tostring(id)] or 0) + 1 else none = none + 1 end
  counts[e.type] = (counts[e.type] or 0) + 1
end
return {ids = ids, counts = counts, none = none}
"""


PROBE_SETUP = f"""
local s = surface
s.request_to_generate_chunks({{{X0 + 80}, {Y0}}}, 1)
s.force_generate_chunk_requests()
for _, e in pairs(s.find_entities_filtered{{area = {{{{{X0 + 70}, {Y0 - 10}}}, {{{X0 + 90}, {Y0 + 10}}}}}}}) do
  if e.type ~= "character" then e.destroy() end
end
s.create_entity{{name = "substation", position = {{{X0 + 80}, {Y0}}}, force = "player"}}
s.create_entity{{name = "solar-panel", position = {{{X0 + 83} + 0.5, {Y0} + 0.5}}, force = "player"}}
s.create_entity{{name = "accumulator", position = {{{X0 + 77}, {Y0}}}, force = "player"}}
s.freeze_daytime = true
s.daytime = 0
return true
"""

PROBE = f"""
local s = surface
local a = s.find_entity("accumulator", {{{X0 + 77}, {Y0}}})
if MULT then
  s.solar_power_multiplier = MULT
  local ok = pcall(function() s.set_property("solar-power", SP) end)
  a.energy = 0
  return {{tick = game.tick, energy = a.energy, property_set = ok}}
end
return {{tick = game.tick, energy = a.energy}}
"""


def test_panel_output_follows_both_multipliers(stack, mcp):
    """What the skill tells Kira to multiply: solar_power_multiplier * solar-power / 100.
    A panel at noon charges an empty accumulator at its full output; the game's rate must match."""
    assert mcp.tool("factorio_query", lua=PROBE_SETUP)["ok"]
    seen = []
    for mult, sp in ((1, 100), (2, 100), (2, 50)):
        lua = PROBE.replace("MULT", str(mult)).replace("SP", str(sp))
        a = mcp.tool("factorio_query", lua=lua)
        assert a["ok"], a
        if sp != 100 and not a["data"]["property_set"]:
            continue  # this game cannot change the property at runtime
        wait_until(lambda: mcp.tool("factorio_query", lua=PROBE.replace("if MULT", "if false"))["data"]["tick"]
                   >= a["data"]["tick"] + 120, timeout=20, step=0.2, what="120 ticks")
        b = mcp.tool("factorio_query", lua=PROBE.replace("if MULT", "if false"))
        kw = (b["data"]["energy"] - a["data"]["energy"]) / (b["data"]["tick"] - a["data"]["tick"]) * 60 / 1000
        want = solar.ratio(60, 5, {"always_day": True, "solar_power_multiplier": mult, "solar_power": sp})["peak_kw_per_panel"]
        seen.append((mult, sp, round(kw, 2), want))
        assert abs(kw - want) < 0.03 * want, seen
    print(f"\npanel kW at noon (multiplier, solar-power, game, solar.py): {seen}")
    mcp.tool("factorio_query", lua='surface.solar_power_multiplier = 1 pcall(function() surface.set_property("solar-power", 100) end) surface.freeze_daytime = false return true')


def box():
    return f"{{{{{X0 - 4}, {Y0 - 4}}}, {{{X0 + 60}, {Y0 + 42}}}}}"


def test_solar_field_is_one_network(stack, mcp):
    world = mcp.tool("factorio_query", lua=SETUP)
    assert world["ok"], world
    w = world["data"]
    print(f"\nsurface: {w['day']}\npanel: {w['panel']}\naccumulator: {w['acc']}\npole: {w['pole']}")
    r = solar.ratio(w["panel"]["peak_kw"], w["acc"]["capacity_mj"], w["day"],
                    accumulator_input_kw=w["acc"]["input_kw"], accumulator_output_kw=w["acc"]["output_kw"])
    print(f"ratio: {r}")
    # the vanilla numbers, from the live game: 70 % of 60 kW, ~0.84 accumulators per panel
    assert abs(r["average_kw_per_panel"] - 42.0) < 1e-6, r
    assert 0.83 < r["accumulators_per_panel"] < 0.86, r

    c = solar.cell(w["panel"], w["acc"], w["pole"], ratio=r, day_night=w["day"])
    assert c["ok"] and c["width"] == 18, c
    blocked = [tuple(p) for p in w["blocked"]]
    f = solar.field(c, area=[[X0, Y0], [X0 + 54, Y0 + 36]], blocked=blocked)
    assert f["ok"] and f["poles"] == 5 and f["skipped"] == 1, {k: v for k, v in f.items() if k != "entities"}
    assert solar.check(f["entities"], w["panel"], w["acc"], w["pole"], blocked, ratio=r) == []
    print(f"field: {f['poles']} cells, {f['panels']} panels, {f['accumulators']} accumulators, "
          f"{f['sustained_kw']} kW sustained")

    lua = PLACE.replace("ITEMS", json.dumps(json.dumps(f["entities"]))).replace("BOX", box())
    placed = mcp.tool("factorio_query", lua=lua)
    assert placed["ok"], placed
    d = placed["data"]
    assert d["placed"] == len(f["entities"]) and d["failed"] in ({}, []), d
    assert d["revived"] == len(f["entities"]) and d["revive_failed"] in ({}, []), d

    q = mcp.tool("factorio_query", lua=NETWORKS.replace("BOX", box()))
    assert q["ok"], q
    n = q["data"]
    print(f"networks: {n}")
    assert n["none"] == 0, n
    assert n["counts"] == {"solar-panel": f["panels"], "accumulator": f["accumulators"], "electric-pole": f["poles"]}, n
    assert len(n["ids"]) == 1, n  # one electric network across all five cells
