---
name: production
description: Plan a production line - machines per recipe, item flows, raw inputs and byproducts for target rates, exact, with oil cracking loops and module/beacon effects. Use for "how many assemblers for X/s", ratios, oil setups, "what do I need to make Y".
---

# Production planning

Inside `factorio_compute`: `import production`. Pure Python, exact fractions; you feed it the prototypes.

## Steps

1. `data_lua` reads, from `prototypes` (never assume vanilla names; Nullius renames everything):
   - recipes: for every non-hidden recipe the player has (`force.recipes[n].enabled`), `{name, category, energy, ingredients, products, maximum_productivity, allowed_effects}` (2.0: `r.category`; 2.1 renamed it to the list `r.categories` and has no `category`: pass either key, the planner takes the first category), plus `productivity_bonus` from `force.recipes[n].productivity_bonus` when research gave one. Pass the lists as the game gives them.
   - machines: `prototypes.get_entity_filtered{{filter = "crafting-category", ...}}`, each `{name, crafting_speed = get_crafting_speed(), crafting_categories, module_inventory_size, energy_usage, allowed_effects, effect_receiver}`.
   - modules: `prototypes.item[n].module_effects`; beacons: `distribution_effectivity`, `profile`, `module_inventory_size`.
2. `r = production.plan(recipes, machines, {item: per_second}, choices, module_defs=..., beacon_defs=...)`
   - `choices = {"machine": {category or recipe: machine}, "recipe": {item: recipe or [recipes]}, "modules": {recipe or category or "*": {"modules": {module: n}, "beacons": [{"name": "beacon", "count": n, "modules": {module: n}}]}}}`. Without a machine choice the fastest of the category is taken: name the researched one.
   - `exclude=[...]` drops recipes (default drops category `recycling`); `raw=[...]` treats items as inputs (plates from the bus); items nothing makes are raw anyway.
   - `raw_priority` orders raw inputs, cheapest first: default `[{"water": 1}, {"*": 1}]`, so the plan cracks oil fully rather than burning crude. Put the planet's own water/fluids first in mods.
   - Single-recipe chains are divided out; items with several recipes, byproducts or loops go to an exact LP (`r["lp"]` says how big).
3. `production.check(r, recipes, targets)` must be `[]`. Read `r["warnings"]`: a module that was not allowed, too many modules, an item listed under `imports` (no chosen recipe can make it).
4. Tell the player per recipe `machines_ceil` x `machine` (and `machines` for the exact share), `raw` per second, `surplus` byproducts, `power_w`. Exact values are in the `*_exact` strings ("2/3").

## Not covered

Quality fan-out, spoilage, fuel for burner machines, mining drills (pass a mining "recipe" yourself if needed: `{time: mining_time, ingredients: {}, products: {ore: 1}, category: ...}`), belt/inserter throughput, and building counts as targets. Layout is another skill.
