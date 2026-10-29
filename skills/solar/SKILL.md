---
name: solar
description: Plan a solar field: panels and accumulators for a steady load on any surface (Nauvis, other planets, Nullius), a tileable pole cell, and the field tiled over free ground. Use for "build solar", "how many accumulators", "N MW of solar here".
---

# Solar fields

Inside `factorio_compute`: `import solar`. Pure Python, no game access; you feed it the surface and the prototypes.

## Steps

1. `data_lua` reads, never hardcoding names (Nullius renames everything):
   - the surface: `ticks_per_day, dusk, evening, morning, dawn, always_day, freeze_daytime, daytime, solar_power_multiplier`, and `solar_power = surface.get_property("solar-power")` (inside pcall; 100 if it fails). Panel output = peak * `solar_power_multiplier` * `solar-power` / 100.
   - the panel: `name, tile_width, tile_height`, `peak_kw = get_max_energy_production() * 60 / 1000`.
   - the accumulator: `name, tile_width, tile_height`, from `electric_energy_source_prototype`: `capacity_mj = buffer_capacity / 1e6`, `input_kw`/`output_kw = input_flow_limit`/`output_flow_limit * 60 / 1000`.
   - the pole (substation unless told otherwise): `name, tile_width, tile_height`, `supply = get_supply_area_distance()`, `reach = get_max_wire_distance()`.
   - blocked tiles in the target area: every entity and ghost (all tiles of big ones, `kc.footprint`), water, cliffs, ore the player wants kept free.
2. `r = solar.ratio(panel["peak_kw"], acc["capacity_mj"], day, accumulator_input_kw=..., accumulator_output_kw=..., target_mw=...)` gives `accumulators_per_panel`, `average_kw_per_panel`, `limited_by` and, with `target_mw`, `panels`/`accumulators`. Vanilla: 0.84 per panel on a 25000-tick day, 0.84672 on 2.0 Nauvis (7 min), 42 kW per 60 kW panel.
3. `c = solar.cell(panel, acc, pole, ratio=r, day_night=day)`: one square cell, pole in the middle, `c["sustained_kw"]` per cell. Substation: 18x18, 24 panels + 20 accumulators.
4. `f = solar.field(c, origin, target_mw=..., blocked=...)` grows cells nearest-first from `origin`, or `solar.field(c, area=[[x0, y0], [x1, y1]], blocked=...)` fills a box. Cells that hit a blocked tile are skipped (`partial=True` keeps them and drops only the hit items, e.g. around trees). `ok: False` with `reason` when the target does not fit within `search` cells: move the origin or ask.
5. `solar.check(f["entities"], panel, acc, pole, blocked, ratio=r)` must be `[]`.
6. `result = f["entities"]`, then one `factorio_query` with `kc.place_ghosts(surface, data)`. Link the field to the base grid with `kc.unwired` + a pole line if the player wants it joined.

## Not covered

Quality (pass the quality's numbers yourself), panels or accumulators rotated, space platforms in transit (use `always_day` with the current multiplier), and fields whose accumulators sit elsewhere.
