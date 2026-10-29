---
name: mining
description: Lay out an ore mining outpost on a resource patch - two rows of drills facing a shared belt, all belts merged to one exit belt toward the base. Use for "mine this patch", "build drills on iron/copper/stone/coal", outposts.
---

# Ore mining outpost

Inside `factorio_compute`: `import mining`. Pure Python, no game access; you feed it the patch.

## Steps

1. `data_lua` reads the patch and the geometry:
   - ore: every resource entity of the ONE ore to mine (`find_entities_filtered{type = "resource", name = ...}` over the patch), as `{x, y, amount}`.
   - avoid: tiles of other resources touching the patch (no drill may mine mixed ore).
   - blocked: entities, ghosts, water and cliffs in the box (patch + 10). Trees and rocks: mark them for deconstruction or pass them as blocked.
   - drill from prototypes, never hardcoded (Nullius renames drills): `name`, `size = tile_width`, `radius = mining_drill_radius`, `output = vector_to_place_result` (the north-facing vector; the planner rotates it), `mining_speed`. Pick the best researched drill whose `resource_categories` has the ore's category.
   - belt: `{name, speed = belt_speed * 480}` (items/s) of the best researched tier.
2. `r = mining.plan(ore, blocked, toward=..., drill=..., belt=..., avoid=...)`
   - `toward`: where the ore goes (base, train stop). The exit belt ends at the patch corner on that side.
   - `mode="full"` adds edge drills for 100% coverage when `"simple"` (default, fewer drills) leaves ore; `orientation` is `"auto"` (tries both) or `"horizontal"`/`"vertical"`.
   - `r["stats"]`: `coverage` (% of ore tiles in some drill's mining area), `drills`, `lanes`, `rate` (ore/s). `r["warning"]` when the drills fill more than one belt: split the patch or use a faster belt.
3. `mining.check(r["entities"], ore, blocked, drill=..., belt=..., exit=r["exit"])` must be `[]`.
4. One `factorio_query` with `kc.place_ghosts(surface, data)` where `result = r["entities"]` (drills and belts, unchanged).
5. Power is NOT included: run the power skill on `r["drill_boxes"]` (consumers) with `r["occupied"]` added to blocked. `r["pole_rows"]` lists the free rows beside every drill row, where poles fit.
6. Bring the ore home: `belts.route(r["out"], <destination>, blocked + r["occupied"])`. `r["out"]` is the free tile the exit belt feeds into.

## Not covered

Fluid ores (uranium needs sulfuric acid pipes), beacons, modules, lamps, landfill, splitting lanes over several belts, and non-square drills.
