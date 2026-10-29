---
name: power
description: Place electric poles that power every electric entity of a design with the fewest poles, wire them into one group, and link that group to the existing grid. Use whenever a build uses electricity.
---

# Pole planning

Inside `factorio_compute`: `import power`. Pure Python, no game access; you feed it the area.

## Steps

1. `data_lua` reads:
   - consumers: every electric entity of the design (`prototypes.entity[n].electric_energy_source_prototype ~= nil`), as `{x, y, w, h}` = center + `tile_width/tile_height`. Ghosts count.
   - blocked tiles: every other entity and ghost in the area (plus margin), as tile centers (`kc.footprint`), and water/cliffs. The consumers' own tiles are kept free automatically.
   - pole specs from `kc.poles()`: `p.poles` entries already are `{name, size, supply, reach}`. Use the `p.cover` entry as `pole`, the `p.reach` entry as `reach_pole`. Never hardcode names: Nullius renames poles.
   - grid poles: `kc.nearest_pole(surface, center, 300)` (or every pole near the design), as `(x, y, reach, supply, size)` from their prototypes. All grid poles you pass are assumed to be one network.
2. `r = power.plan(consumers, pole, blocked, grid_poles, reach_pole=...)`:
   - cover poles (greedy: the spot powering the most unpowered consumers first), extra `join` poles if the cover poles are not one wire group, then the fewest `reach_pole`s to a grid pole.
   - Every planned link is at least `slack` (half a tile) under the shorter reach of its two poles: substations 18.03 apart with reach 18 do NOT link.
   - No `grid_poles`: only cover + join; tell the player the design is not on a grid yet.
   - `ok: False` with `reason`: a consumer has no free spot in supply range (`uncoverable` gives indices), or a chain is walled off; clear space or widen the area, then tell the player.
   - Separate calls: `power.cover(consumers, pole, blocked, join=...)` and `power.link(group, grid_poles, blocked, reach_pole=..., consumers=...)`.
3. `power.check(consumers, r["entities"], grid_poles, blocked)` must be `[]` (every consumer powered, every new pole wired to the grid by the game's rule, nothing on blocked tiles or overlapping, each pole on its grid). `kc.unwired` gives the same wiring verdict.
4. `result = r["entities"]`, then one `factorio_query` with `kc.place_ghosts(surface, data)`. Place poles LAST, after the design's own entities. Extra keys (`size`, `supply`, `reach`, `role`) are ignored by the placer.

## Reading the result

`r["cover_poles"]`, `r["join_poles"]`, `r["link_poles"]`; each entity's `role` is `cover`, `join` or `link`. Name the pole types in the answer.

## Not covered

Lamps-in-the-dark coverage, poles for circuit wires, and choosing which of several separate grids to join: pass only the poles of the network you want.
