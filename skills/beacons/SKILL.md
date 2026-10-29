---
name: beacons
description: Place beacons around machines (assembler rows, refineries, pumpjack fields) so each beacon reaches as many machines as possible, and predict each machine's beacon count and effect. Use for "add beacons", "beacon this row/outpost", or checking a beacon layout.
---

# Beacon placement

Inside `factorio_compute`: `import beacons`. Pure Python, no game access; you feed it the machines, the occupied tiles and the beacon prototype.

## Steps

1. `data_lua` reads:
   - the machines to boost: `{name, x, y, w = tile_width, h = tile_height, collision_box}` from each entity's prototype.
   - blocked tiles: every other entity and ghost in the box (machines' reach + 6 tiles): belts, inserters, pipes, poles, chests (`kc.footprint` for big ones), plus water and cliffs. Leave the inserter/belt lanes free of beacons by listing those tiles too, even if they are still empty.
   - the beacon from prototypes, never by name alone (Nullius renames it): `tile_width`, `get_supply_area_distance(quality)`, `profile` (2.0 diminishing returns, a list; may be nil), `distribution_effectivity` (+ `distribution_effectivity_bonus_per_quality_level` x quality level), `module_inventory_size`, and the module's `prototypes.item[m].module_effects`.
   - beacons already standing near the machines, `{x, y}`: pass them as `existing`.
2. `fx = beacons.transmitted([module_effects] * slots, distribution_effectivity)` is what one full beacon passes on.
3. `r = beacons.place(machines, blocked, beacon=<name>, size=..., supply_area_distance=..., profile=..., beacon_effect=fx, existing=...)`
   - `min_utility` (default 0.5): a beacon is placed only while it adds at least this many beacons' worth, summed over the machines it reaches, after the profile. Lower (0.1) fills every spot; higher saves beacons and power. With the vanilla profile 0.5 gives the middle of an assembler row 8 beacons.
   - `min_per_machine`: first make every machine reach this count where free tiles allow; `r["short"]` lists the ones that could not.
   - `max_beacons`: hard cap.
   - `r["machines"][i]` = `{x, y, beacons, effect, effects}`: predicted count and effect (`effects.speed` etc., before the machine's own modules and the game's clamps).
4. `beacons.check(r, machines, blocked, size=..., supply_area_distance=..., profile=..., existing=..., min_per_machine=...)` must be `[]` (no overlaps, counts recomputed from geometry).
5. `result = r["entities"]`, then one `factorio_query` with `kc.place_ghosts(surface, data)`; then power them (beacons draw power) and put the modules in (`insert_plan` on the ghosts, or ask the player).

## Templates

For a regular row, the classic layouts are what the greedy finds too: assemblers touching (pitch 3) with a beacon row directly above and below, shifted one tile: 8 beacons per machine. Assemblers 6 apart with two beacons between neighbours and rows 1 tile above / 2 below: 12. `beacons.evaluate(beacon_positions, machines, ...)` scores any layout you draw by hand.

## Not covered

Inserter, belt and pole placement (reserve their tiles in `blocked` first), mixed beacon types or module sets in one call, and machine-side clamps (e.g. speed and consumption floor at -80 %). The greedy is not optimal; for a hand-made template compare `total_effect` with `evaluate`.
