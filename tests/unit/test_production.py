"""Production planner (skills/production/lib/production.py): pure Python, no game.

Expected numbers are vanilla 2.0 answers worked out by hand (the same ones Kirk McDonald's
calculator shows): 1 electronic circuit/s in assembler-2 = 2/3 circuit assemblers + 1 cable
assembler; 1 advanced oil processing craft/s with all heavy and light oil cracked = 97.5
petroleum gas/s from 5 refineries, 5/4 heavy and 17/4 light cracking plants, 100 crude and
132.5 water.
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "production" / "lib"))
import production  # noqa: E402

RECIPES = {
    "electronic-circuit": {"time": 0.5, "ingredients": {"iron-plate": 1, "copper-cable": 3},
                           "products": {"electronic-circuit": 1}},
    "copper-cable": {"time": 0.5, "ingredients": {"copper-plate": 1}, "products": {"copper-cable": 2}},
    "iron-gear-wheel": {"time": 0.5, "ingredients": {"iron-plate": 2}, "products": {"iron-gear-wheel": 1}},
    "advanced-oil-processing": {"time": 5, "category": "oil-processing",
                                "ingredients": {"crude-oil": 100, "water": 50},
                                "products": {"heavy-oil": 25, "light-oil": 45, "petroleum-gas": 55}},
    "basic-oil-processing": {"time": 5, "category": "oil-processing", "ingredients": {"crude-oil": 100},
                             "products": {"petroleum-gas": 45}},
    "heavy-oil-cracking": {"time": 2, "category": "chemistry", "ingredients": {"heavy-oil": 40, "water": 30},
                           "products": {"light-oil": 30}},
    "light-oil-cracking": {"time": 2, "category": "chemistry", "ingredients": {"light-oil": 30, "water": 30},
                           "products": {"petroleum-gas": 20}},
    "lubricant": {"time": 1, "category": "chemistry", "ingredients": {"heavy-oil": 10},
                  "products": {"lubricant": 10}},
    "uranium-processing": {"time": 12, "category": "centrifuging", "ingredients": {"uranium-ore": 10},
                           "products": {"uranium-235": {"amount": 1, "probability": 0.007},
                                        "uranium-238": {"amount": 1, "probability": 0.993}}},
    "kovarex-enrichment-process": {"time": 60, "category": "centrifuging",
                                   "ingredients": {"uranium-235": 40, "uranium-238": 5},
                                   "products": {"uranium-235": {"amount": 41, "ignored_by_productivity": 40},
                                                "uranium-238": {"amount": 2, "ignored_by_productivity": 2}}},
}
MACHINES = {
    "assembling-machine-1": {"speed": 0.5, "categories": ["crafting"], "module_slots": 0},
    "assembling-machine-2": {"speed": 0.75, "categories": ["crafting", "advanced-crafting"], "module_slots": 2,
                             "energy_usage": 150000},
    "assembling-machine-3": {"speed": 1.25, "categories": ["crafting", "advanced-crafting"], "module_slots": 4,
                             "energy_usage": 375000},
    "oil-refinery": {"speed": 1, "categories": ["oil-processing"], "module_slots": 3},
    "chemical-plant": {"speed": 1, "categories": ["chemistry"], "module_slots": 3},
    "centrifuge": {"speed": 1, "categories": ["centrifuging"], "module_slots": 2},
}
ASM2 = {"machine": {"crafting": "assembling-machine-2"}}


def ex(r, name, field="machines_exact"):
    return Fraction(r["recipes"][name][field])


def test_electronic_circuits_in_assembler_2():
    r = production.plan(RECIPES, MACHINES, {"electronic-circuit": 1}, ASM2)
    assert ex(r, "electronic-circuit") == Fraction(2, 3) and ex(r, "copper-cable") == 1, r["recipes"]
    assert r["recipes"]["electronic-circuit"]["machines_ceil"] == 1
    assert r["raw_exact"] == {"copper-plate": "3/2", "iron-plate": "1"}
    assert r["items"]["copper-cable"]["produced_exact"] == "3" and r["surplus"] == {} and r["imports"] == {}
    assert r["lp"] is None and r["recipes"]["copper-cable"]["solver"] == "ratio"
    assert r["machines"] == {"assembling-machine-2": 2}
    assert r["recipes"]["copper-cable"]["power_w"] == 150000.0
    assert production.check(r, RECIPES, {"electronic-circuit": 1}) == []
    json.dumps(r)


def test_default_machine_is_the_fastest_of_the_category():
    r = production.plan(RECIPES, MACHINES, {"iron-gear-wheel": 1})
    assert r["recipes"]["iron-gear-wheel"]["machine"] == "assembling-machine-3"
    assert ex(r, "iron-gear-wheel") == Fraction(2, 5)


def test_advanced_oil_processing_with_full_cracking_beats_basic():
    r = production.plan(RECIPES, MACHINES, {"petroleum-gas": 97.5})
    assert set(r["recipes"]) == {"advanced-oil-processing", "heavy-oil-cracking", "light-oil-cracking"}, r["recipes"]
    assert ex(r, "advanced-oil-processing") == 5
    assert ex(r, "heavy-oil-cracking") == Fraction(5, 4) and ex(r, "light-oil-cracking") == Fraction(17, 4)
    assert r["raw_exact"] == {"crude-oil": "100", "water": "265/2"}
    assert r["surplus"] == {} and r["imports"] == {} and r["warnings"] == []
    assert r["lp"]["recipes"] == 4 and all(e["solver"] == "lp" for e in r["recipes"].values())
    assert production.check(r, RECIPES, {"petroleum-gas": 97.5}) == []


def test_without_heavy_cracking_heavy_oil_is_a_surplus_byproduct():
    r = production.plan(RECIPES, MACHINES, {"petroleum-gas": 85}, exclude=["heavy-oil-cracking"],
                        choices={"recipe": {"petroleum-gas": ["advanced-oil-processing", "light-oil-cracking"]}})
    assert ex(r, "advanced-oil-processing") == 5 and ex(r, "light-oil-cracking", "rate_exact") == Fraction(3, 2)
    assert r["surplus_exact"] == {"heavy-oil": "25"}
    assert production.check(r, RECIPES) == []


def test_lubricant_and_petroleum_take_heavy_oil_before_cracking_it():
    t = {"lubricant": 10, "petroleum-gas": 50}
    r = production.plan(RECIPES, MACHINES, t)
    assert production.check(r, RECIPES, t) == [], production.check(r, RECIPES, t)
    assert r["surplus"] == {} and "basic-oil-processing" not in r["recipes"]
    # all crude goes through advanced processing; the 10 heavy oil for lubricant is not cracked
    aop = ex(r, "advanced-oil-processing", "rate_exact")
    heavy_cracked = ex(r, "heavy-oil-cracking", "rate_exact") * 40
    assert heavy_cracked == aop * 25 - 10


def test_forced_basic_oil_processing():
    r = production.plan(RECIPES, MACHINES, {"petroleum-gas": 45},
                        choices={"recipe": {"petroleum-gas": "basic-oil-processing"}})
    assert set(r["recipes"]) == {"basic-oil-processing"} and ex(r, "basic-oil-processing") == 5
    assert r["raw_exact"] == {"crude-oil": "100"}
    assert production.check(r, RECIPES) == []


def test_productivity_modules_on_circuits():
    cfg = dict(ASM2, modules={"*": {"modules": {"productivity-module-3": 2}}})
    r = production.plan(RECIPES, MACHINES, {"electronic-circuit": 1}, cfg)
    c = r["recipes"]["electronic-circuit"]
    assert c["effects"]["productivity"] == "1/5" and c["effects"]["speed"] == "7/10"
    assert c["products_exact"] == {"electronic-circuit": "6/5"}
    assert ex(r, "electronic-circuit", "rate_exact") == Fraction(5, 6)
    assert ex(r, "electronic-circuit") == Fraction(50, 63)  # 5/6 * 0.5 / (0.75 * 0.7)
    assert r["raw_exact"]["iron-plate"] == "5/6"
    # cables with prod too: 2.5 cables/s from 2.4 per craft
    assert ex(r, "copper-cable", "rate_exact") == Fraction(25, 24)
    assert c["effects"]["consumption"] == "13/5" and c["power_w"] == float(Fraction(50, 63) * 150000 * Fraction(13, 5))
    assert production.check(r, RECIPES) == []


def test_productivity_cap_and_base_effect():
    recipes = {"x": {"time": 1, "ingredients": {"a": 1}, "products": {"x": 1}, "maximum_productivity": 0.5,
                     "productivity_bonus": 0.3}}
    machines = {"foundry": {"speed": 4, "categories": ["crafting"], "module_slots": 4,
                            "base_effect": {"productivity": 0.5}}}
    r = production.plan(recipes, machines, {"x": 3}, {"modules": {"x": {"modules": {"productivity-module-3": 4}}}})
    assert r["recipes"]["x"]["effects"]["productivity"] == "1/2"  # 0.3 + 0.5 + 0.4 capped at 0.5
    assert r["raw_exact"] == {"a": "2"}
    assert production.check(r, recipes) == []


def test_recipe_that_ignores_productivity_refuses_prod_modules():
    recipes = {"pipe": {"time": 0.5, "ingredients": {"iron-plate": 1}, "products": {"pipe": 1},
                        "allowed_effects": ["speed", "consumption", "pollution"]}}
    r = production.plan(recipes, MACHINES, {"pipe": 1},
                        dict(ASM2, modules={"pipe": {"modules": {"productivity-module-3": 2}}}))
    e = r["recipes"]["pipe"]
    assert e["effects"]["productivity"] == "0" and e["effects"]["speed"] == "1"
    assert any("productivity-module-3 not allowed" in w for w in r["warnings"]), r["warnings"]
    assert r["raw_exact"] == {"iron-plate": "1"}
    # base productivity of the machine does not apply either
    m = {"em": {"speed": 2, "categories": ["crafting"], "module_slots": 5, "base_effect": {"productivity": 0.5}}}
    r = production.plan(recipes, m, {"pipe": 1})
    assert r["recipes"]["pipe"]["effects"]["productivity"] == "0" and r["raw_exact"] == {"iron-plate": "1"}


def test_ignored_by_productivity_kovarex_loop():
    # U-238 comes from a bus (no producer chosen), U-235 only from the kovarex loop itself
    choices = {"modules": {"kovarex-enrichment-process": {"modules": {"productivity-module-3": 2}}},
               "recipe": {"uranium-235": "kovarex-enrichment-process", "uranium-238": []}}
    r = production.plan(RECIPES, MACHINES, {"uranium-235": 1}, choices)
    k = r["recipes"]["kovarex-enrichment-process"]
    # only the 41st U-235 gets productivity: 40 + 1 * 1.2; U-238 out is all ignored
    assert k["products_exact"] == {"uranium-235": "206/5", "uranium-238": "2"}
    assert k["solver"] == "lp" and ex(r, "kovarex-enrichment-process", "rate_exact") == Fraction(5, 6)
    assert ex(r, "kovarex-enrichment-process") == Fraction(500, 7)  # 5/6 * 60 / 0.7
    assert r["raw_exact"] == {"uranium-238": "5/2"} and r["imports"] == {}
    assert production.check(r, RECIPES) == []


def test_probabilistic_products_uranium_processing():
    r = production.plan(RECIPES, MACHINES, {"uranium-238": 1}, exclude=["kovarex-enrichment-process"])
    u = r["recipes"]["uranium-processing"]
    assert ex(r, "uranium-processing", "rate_exact") == Fraction(1000, 993) and u["solver"] == "lp"
    assert r["surplus_exact"] == {"uranium-235": str(Fraction(7, 993))}
    assert r["raw_exact"] == {"uranium-ore": str(Fraction(10000, 993))}
    assert production.check(r, RECIPES) == []


def test_beacons_with_distribution_effectivity_and_profile():
    def speed(beacons):
        r = production.plan(RECIPES, MACHINES, {"iron-gear-wheel": 1},
                            {"modules": {"iron-gear-wheel": {"beacons": beacons}}},
                            beacon_defs={"beacon": {"distribution_effectivity": 1.5, "module_slots": 2}})
        assert production.check(r, RECIPES) == []
        return Fraction(r["recipes"]["iron-gear-wheel"]["effects"]["speed"]), r

    s, r = speed([{"name": "beacon", "count": 1, "modules": {"speed-module-3": 2}}])
    assert s == Fraction(5, 2)  # 1 + 0.5 * 2 * 1.5
    assert ex(r, "iron-gear-wheel") == Fraction(1, 2) / (Fraction(5, 4) * Fraction(5, 2))
    s, _ = speed([{"name": "beacon", "count": 1, "modules": {"speed-module-3": 2}, "distribution_effectivity": 0.5}])
    assert s == Fraction(3, 2)
    s, _ = speed([{"name": "beacon", "count": 2, "modules": {"speed-module-3": 2}, "profile": [1, 0.7071]}])
    assert s == 1 + Fraction(1) * Fraction(3, 2) * 2 * Fraction("0.7071")
    s, r = speed([{"name": "beacon", "count": 1, "modules": {"speed-module-3": 3}}])
    assert any("3 modules in a beacon with 2 slots" in w for w in r["warnings"])


def test_consumption_and_speed_floor_at_20_percent():
    r = production.plan(RECIPES, MACHINES, {"iron-gear-wheel": 1},
                        {"modules": {"iron-gear-wheel": {"modules": {"efficiency-module-3": 4}}}})
    g = r["recipes"]["iron-gear-wheel"]
    assert g["effects"]["consumption"] == "1/5" and g["power_w"] == float(Fraction(2, 5) * 375000 / 5)
    slow = {"slow": {"speed": "-0.9"}}
    r = production.plan(RECIPES, MACHINES, {"iron-gear-wheel": 1},
                        {"modules": {"iron-gear-wheel": {"modules": {"slow": 2}}}}, module_defs=slow)
    assert r["recipes"]["iron-gear-wheel"]["effects"]["speed"] == "1/5"


def test_loop_that_cannot_run_falls_back_to_an_import():
    recipes = {"a": {"time": 1, "ingredients": {"x": 1}, "products": {"y": 1}},
               "b": {"time": 1, "ingredients": {"y": 1}, "products": {"x": 1}}}
    r = production.plan(recipes, {"m": {"speed": 1, "categories": ["crafting"]}}, {"y": 1})
    assert sum(r["imports"].values()) == 1 and r["warnings"], r
    assert production.check(r, recipes) == []


def test_deep_chain_is_produced_not_imported():
    # 10**13 ore per target: far more than the first last-resort import price
    recipes = {"i0": {"time": 1, "ingredients": {"ore": 10}, "products": {"i0": 1}}}
    for k in range(1, 12):
        recipes[f"i{k}"] = {"time": 1, "ingredients": {f"i{k - 1}": 10}, "products": {f"i{k}": 1}}
    recipes["t-cheap"] = {"time": 1, "ingredients": {"i11": 10}, "products": {"t": 1}}
    recipes["t-dear"] = {"time": 1, "ingredients": {"i11": 20}, "products": {"t": 1}}
    r = production.plan(recipes, {"m": {"speed": 1, "categories": ["crafting"]}}, {"t": 1})
    assert r["imports"] == {} and r["raw_exact"] == {"ore": str(10 ** 13)}, (r["imports"], r["raw"])
    assert set(r["recipes"]) == set(recipes) - {"t-dear"} and r["lp"]["solves"] >= 2
    assert production.check(r, recipes) == []


def test_raw_items_stop_the_walk():
    r = production.plan(RECIPES, MACHINES, {"electronic-circuit": 1}, ASM2, raw=["copper-cable"])
    assert set(r["recipes"]) == {"electronic-circuit"} and r["raw_exact"]["copper-cable"] == "3"


def test_water_priority_changes_the_answer():
    # if water were as dear as crude, advanced processing + cracking costs 232.5 units per 97.5
    # petroleum (2.38 each) against 100 per 45 (2.22) for basic processing, so basic wins
    r = production.plan(RECIPES, MACHINES, {"petroleum-gas": 90}, raw_priority=[{"*": 1}])
    assert set(r["recipes"]) == {"basic-oil-processing"} and r["raw_exact"] == {"crude-oil": "200"}
    assert production.check(r, RECIPES) == []
    # and without basic processing, heavy cracking (60 water per 20 petroleum) stops paying
    r = production.plan(RECIPES, MACHINES, {"petroleum-gas": 85}, raw_priority=[{"*": 1}],
                        exclude=["basic-oil-processing"])
    assert "heavy-oil-cracking" not in r["recipes"] and r["surplus_exact"] == {"heavy-oil": "25"}
    assert production.check(r, RECIPES) == []


def test_check_catches_tampering():
    r = production.plan(RECIPES, MACHINES, {"petroleum-gas": 97.5})
    assert production.check(r) == []
    bad = json.loads(json.dumps(r))
    bad["recipes"]["light-oil-cracking"]["rate_exact"] = "2"
    bad["recipes"]["light-oil-cracking"]["rate"] = 2.0
    p = production.check(bad)
    assert any("not conserved" in x for x in p) and any("machines" in x for x in p), p
    bad = json.loads(json.dumps(r))
    del bad["raw_exact"]["water"]
    assert any(x.startswith("water: not conserved") for x in production.check(bad))
    bad = json.loads(json.dumps(r))
    bad["recipes"]["advanced-oil-processing"]["ingredients_exact"]["water"] = "40"
    assert any("ingredients" in x for x in production.check(bad, RECIPES))
    assert production.check({"recipes": {}}) and production.check(r, targets={"petroleum-gas": 1})


def test_deterministic():
    a = production.plan(RECIPES, MACHINES, {"lubricant": 10, "petroleum-gas": 50, "electronic-circuit": 2})
    b = production.plan(dict(reversed(list(RECIPES.items()))), MACHINES,
                        {"electronic-circuit": 2, "petroleum-gas": 50, "lubricant": 10})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# What a data_lua query returns for prototypes.recipe / crafting machines, serialised with
# helpers.table_to_json: ingredient and product lists, `energy`, Lua sets as {name: true},
# energy_usage in J/tick, and empty tables as [].
PROTO_RECIPES = json.loads("""{
 "electronic-circuit": {"name": "electronic-circuit", "category": "electronics", "energy": 0.5,
   "hidden": false, "maximum_productivity": 3,
   "allowed_effects": {"consumption": true, "speed": true, "productivity": true, "pollution": true, "quality": true},
   "ingredients": [{"type": "item", "name": "iron-plate", "amount": 1},
                   {"type": "item", "name": "copper-cable", "amount": 3}],
   "products": [{"type": "item", "name": "electronic-circuit", "amount": 1, "probability": 1,
                 "ignored_by_productivity": 0}]},
 "copper-cable": {"name": "copper-cable", "category": "electronics", "energy": 0.5, "maximum_productivity": 3,
   "ingredients": [{"type": "item", "name": "copper-plate", "amount": 1}],
   "products": [{"type": "item", "name": "copper-cable", "amount": 2, "probability": 1}]},
 "light-oil-cracking": {"name": "light-oil-cracking", "category": "chemistry-or-cryogenics", "energy": 2,
   "ingredients": [{"type": "fluid", "name": "water", "amount": 30},
                   {"type": "fluid", "name": "light-oil", "amount": 30}],
   "products": [{"type": "fluid", "name": "petroleum-gas", "amount": 20, "temperature": 25}]},
 "iron-gear-wheel-recycling": {"name": "iron-gear-wheel-recycling", "category": "recycling", "energy": 0.03125,
   "hidden": true, "ingredients": [{"type": "item", "name": "iron-gear-wheel", "amount": 1}],
   "products": [{"type": "item", "name": "iron-plate", "amount": 0.5, "extra_count_fraction": 0}]},
 "parameter-0": {"name": "parameter-0", "category": "parameters", "energy": 0.5, "hidden": true,
   "ingredients": [], "products": []}
}""")
PROTO_MACHINES = json.loads("""{
 "assembling-machine-2": {"name": "assembling-machine-2", "type": "assembling-machine", "crafting_speed": 0.75,
   "crafting_categories": {"basic-crafting": true, "crafting": true, "advanced-crafting": true,
                           "crafting-with-fluid": true, "electronics": true},
   "module_inventory_size": 2, "energy_usage": 2500,
   "allowed_effects": {"consumption": true, "speed": true, "productivity": true, "pollution": true, "quality": true}},
 "electromagnetic-plant": {"name": "electromagnetic-plant", "type": "assembling-machine", "crafting_speed": 2,
   "crafting_categories": {"electronics": true, "electromagnetics": true}, "module_inventory_size": 5,
   "energy_usage": 30000, "effect_receiver": {"base_effect": {"productivity": 0.5}}}
}""")


def test_prototype_shaped_input_from_data_lua():
    choices = {"machine": {"electronics": "assembling-machine-2"}}
    r = production.plan(PROTO_RECIPES, PROTO_MACHINES, {"electronic-circuit": 1}, choices)
    assert ex(r, "electronic-circuit") == Fraction(2, 3) and ex(r, "copper-cable") == 1
    assert r["recipes"]["copper-cable"]["power_w"] == 150000.0  # 2500 J/tick
    assert production.check(r, PROTO_RECIPES, {"electronic-circuit": 1}) == []
    # the electromagnetic plant's +50% base productivity, with no choice: it is the fastest
    r = production.plan(PROTO_RECIPES, PROTO_MACHINES, {"electronic-circuit": 1})
    e = r["recipes"]["electronic-circuit"]
    assert e["machine"] == "electromagnetic-plant" and e["effects"]["productivity"] == "1/2"
    assert ex(r, "electronic-circuit", "rate_exact") == Fraction(2, 3)
    assert r["raw_exact"] == {"copper-plate": "2/3", "iron-plate": "2/3"}  # cables made with +50% too
    assert production.check(r, PROTO_RECIPES) == []
    # normalised plain dicts round-trip
    plain = production.recipes_from_prototypes(PROTO_RECIPES)
    assert plain["electronic-circuit"]["ingredients"] == {"copper-cable": "3", "iron-plate": "1"}
    assert production.machines_from_prototypes(PROTO_MACHINES)["assembling-machine-2"]["energy_usage"] == "150000"
    r2 = production.plan(plain, production.machines_from_prototypes(PROTO_MACHINES), {"electronic-circuit": 1})
    assert r2["recipes"] == r["recipes"]


def test_simplex_small_lp():
    # max 3a + 2b s.t. a + b <= 4, a + 3b <= 6  -> a = 4, b = 0, value 12
    A = [[Fraction(1), Fraction(1), Fraction(1), Fraction(0), Fraction(4)],
         [Fraction(1), Fraction(3), Fraction(0), Fraction(1), Fraction(6)],
         [Fraction(-3), Fraction(-2), Fraction(0), Fraction(0), Fraction(0)]]
    production.simplex(A, [2, 3])
    assert A[-1][-1] == 12
