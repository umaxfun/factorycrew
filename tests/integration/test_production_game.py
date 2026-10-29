"""Level 2: the production planner against the real game.

The recipes, machines, modules and beacon are read from the live `prototypes` the way Kira's
data_lua does, planned in Python (skills/production/lib/production.py), and then the game is
asked to agree: an assembler with the planned modules and one under a beacon must report the
same speed/productivity bonuses the planner applied, and must actually craft at the planned
rate per machine.
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction

from conftest import ROOT, wait_until

sys.path.insert(0, str(ROOT / "skills" / "production" / "lib"))
import production  # noqa: E402

X0, Y0 = 700, 600  # clear of the belt test's corner

READ = """
local function get(f) local ok, v = pcall(f) if ok then return v end end
local out = {recipes = {}, machines = {}, modules = {}, beacon = {}}
for _, n in pairs(RECIPES) do
  local r = prototypes.recipe[n]
  if r then
    out.recipes[n] = {name = n, category = (function() local ok, c = pcall(function() return r.category end) if ok then return c end local cs = r.categories return cs[1] or next(cs) end)(), energy = r.energy,
      maximum_productivity = get(function() return r.maximum_productivity end),
      allowed_effects = get(function() return r.allowed_effects end),
      ingredients = r.ingredients, products = r.products}
  end
end
for _, n in pairs(MACHINES) do
  local e = prototypes.entity[n]
  out.machines[n] = {name = n, crafting_speed = get(function() return e.get_crafting_speed() end) or e.crafting_speed,
    crafting_categories = e.crafting_categories, module_inventory_size = e.module_inventory_size,
    energy_usage = e.energy_usage, allowed_effects = get(function() return e.allowed_effects end),
    effect_receiver = get(function() return e.effect_receiver end)}
end
for _, n in pairs(MODULES) do out.modules[n] = {module_effects = prototypes.item[n].module_effects} end
local b = prototypes.entity["beacon"]
out.beacon = {distribution_effectivity = b.distribution_effectivity, profile = get(function() return b.profile end),
              module_slots = b.module_inventory_size}
return out
"""

BUILD = """
local s = surface
s.request_to_generate_chunks({X0 + 20, Y0}, 2)
s.force_generate_chunk_requests()
local box = {{X0 - 5, Y0 - 10}, {X0 + 45, Y0 + 10}}
local tiles = {}
for x = X0 - 5, X0 + 44 do for y = Y0 - 10, Y0 + 9 do tiles[#tiles + 1] = {name = "grass-1", position = {x, y}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{area = box}) do if e.type ~= "character" then e.destroy() end end
local function power(x, y, px, py)
  local eei = s.create_entity{name = "electric-energy-interface", position = {x, y}, force = "player"}
  eei.electric_buffer_size = 1e9
  eei.power_production = 1e7 / 60
  eei.energy = 1e9
  s.create_entity{name = "medium-electric-pole", position = {px, py}, force = "player"}
end
-- a machine does not craft an unresearched recipe (status recipe_not_researched); the stand has none
game.forces.player.recipes["electronic-circuit"].enabled = true
local function asm(x, y)
  local a = s.create_entity{name = "assembling-machine-2", position = {x, y}, force = "player", recipe = "electronic-circuit"}
  a.insert{name = "iron-plate", count = 100}
  a.insert{name = "copper-cable", count = 200}
  return a
end
-- A: two productivity modules
power(X0, Y0, X0 + 2.5, Y0 + 0.5)
local a = asm(X0 + 5.5, Y0 + 0.5)
a.get_module_inventory().insert{name = "productivity-module-3", count = 2}
-- B: no modules, one beacon with two speed modules
power(X0 + 31, Y0 + 5, X0 + 32.5, Y0 + 2.5)
local b = asm(X0 + 35.5, Y0 + 0.5)
local beacon = s.create_entity{name = "beacon", position = {X0 + 35.5, Y0 + 3.5}, force = "player"}
beacon.get_module_inventory().insert{name = "speed-module-3", count = 2}
return true  -- the query env has no `storage`; SAMPLE finds the machines again by position
"""

SAMPLE = """
local s = game.surfaces[1]
local t = {a = s.find_entity("assembling-machine-2", {X0 + 5.5, Y0 + 0.5}),
           b = s.find_entity("assembling-machine-2", {X0 + 35.5, Y0 + 0.5})}
local function one(e) return {count = e.get_output_inventory().get_item_count("electronic-circuit"),
  speed = e.speed_bonus, productivity = e.productivity_bonus,
  status = (function() for k, v in pairs(defines.entity_status) do if v == e.status then return k end end end)(),
  energy = e.energy, network = e.electric_network_id,
  why = {recipe = e.get_recipe() and e.get_recipe().name, input = e.get_inventory(defines.inventory.crafter_input).get_contents(), ingredients = e.get_recipe() and e.get_recipe().ingredients}} end
return {tick = game.tick, a = one(t.a), b = one(t.b)}
"""


def lua_list(names):
    return "{" + ", ".join(json.dumps(n) for n in names) + "}"


def test_planned_rates_and_effects_match_the_game(stack, mcp):
    recipes = ["electronic-circuit", "copper-cable", "advanced-oil-processing", "basic-oil-processing",
               "heavy-oil-cracking", "light-oil-cracking"]
    machines = ["assembling-machine-2", "oil-refinery", "chemical-plant"]
    modules = ["productivity-module-3", "speed-module-3"]
    q = mcp.tool("factorio_query", lua=READ.replace("RECIPES", lua_list(recipes))
                 .replace("MACHINES", lua_list(machines)).replace("MODULES", lua_list(modules)))
    assert q["ok"], q
    d = q["data"]
    assert set(d["recipes"]) == set(recipes), d["recipes"].keys()

    # the oil answer from real prototypes
    oil = production.plan(d["recipes"], d["machines"], {"petroleum-gas": 97.5})
    assert production.check(oil, d["recipes"], {"petroleum-gas": 97.5}) == []
    got = {k: v["machines_exact"] for k, v in oil["recipes"].items()}
    assert got == {"advanced-oil-processing": "5", "heavy-oil-cracking": "5/4", "light-oil-cracking": "17/4"}, got
    assert oil["raw_exact"] == {"crude-oil": "100", "water": "265/2"}, oil["raw_exact"]

    # circuits: A with 2 productivity modules, B under one beacon with 2 speed modules
    cat = d["recipes"]["electronic-circuit"]["category"]
    base = {"machine": {cat: "assembling-machine-2", "crafting": "assembling-machine-2"}}
    beacon = dict(d["beacon"], name="beacon", count=1, modules={"speed-module-3": 2})
    plan_a = production.plan(d["recipes"], d["machines"], {"electronic-circuit": 1},
                             dict(base, modules={"electronic-circuit": {"modules": {"productivity-module-3": 2}}}),
                             module_defs=d["modules"])
    plan_b = production.plan(d["recipes"], d["machines"], {"electronic-circuit": 1},
                             dict(base, modules={"electronic-circuit": {"beacons": [beacon]}}), module_defs=d["modules"])
    for p in (plan_a, plan_b):
        assert production.check(p, d["recipes"]) == [] and not p["warnings"], p["warnings"]
    ea, eb = plan_a["recipes"]["electronic-circuit"], plan_b["recipes"]["electronic-circuit"]
    # circuits per second from one machine = products per craft / seconds per craft
    rate_a = Fraction(ea["products_exact"]["electronic-circuit"]) / Fraction(ea["craft_time_exact"])
    rate_b = Fraction(eb["products_exact"]["electronic-circuit"]) / Fraction(eb["craft_time_exact"])
    print(f"\nplanned per machine: A {float(rate_a):.4f}/s (prod), B {float(rate_b):.4f}/s (beacon)")

    built = mcp.tool("factorio_query", lua=BUILD.replace("X0", str(X0)).replace("Y0", str(Y0)))
    assert built["ok"] and built["data"] is True, built

    def sample():
        s = mcp.tool("factorio_query", lua=SAMPLE.replace("X0", str(X0)).replace("Y0", str(Y0)))
        assert s["ok"], s
        return s["data"]

    first = sample()
    wait_until(lambda: sample()["tick"] >= first["tick"] + 120, timeout=30, step=0.2, what="warm-up")
    s0 = sample()
    # the game reports the same bonuses the planner applied
    assert abs(s0["a"]["speed"] - (float(Fraction(ea["effects"]["speed"])) - 1)) < 1e-6, (s0, ea["effects"])
    assert abs(s0["a"]["productivity"] - float(Fraction(ea["effects"]["productivity"]))) < 1e-6, (s0, ea["effects"])
    assert abs(s0["b"]["speed"] - (float(Fraction(eb["effects"]["speed"])) - 1)) < 1e-6, (s0, eb["effects"])
    span = 900
    wait_until(lambda: sample()["tick"] >= s0["tick"] + span, timeout=60, step=0.2, what="crafting window")
    s1 = sample()
    secs = (s1["tick"] - s0["tick"]) / 60
    made_a, made_b = s1["a"]["count"] - s0["a"]["count"], s1["b"]["count"] - s0["b"]["count"]
    print(f"game over {secs:.2f}s: A {made_a} (planned {float(rate_a) * secs:.1f}), "
          f"B {made_b} (planned {float(rate_b) * secs:.1f})")
    print("status", s1["a"].get("status"), s1["b"].get("status"), s1["a"].get("why"))
    assert abs(made_a - float(rate_a) * secs) <= 2, (made_a, float(rate_a) * secs, s1)
    assert abs(made_b - float(rate_b) * secs) <= 2, (made_b, float(rate_b) * secs, s1)
