---
name: blueprints
description: Read, write and place Factorio 2.0 blueprint strings (blueprints, books, deconstruction/upgrade planners). Use when the player pastes a string or link, asks what a print contains, wants a print built as ghosts (rotated or mirrored), or wants a layout you computed saved as a string.
---

# Blueprint strings

Inside `factorio_compute`: `import blueprints`. Pure Python, no game access.

## Read a string

1. `bp = blueprints.decode(s)`: takes `0eN...` strings (spaces and line breaks are fine) and raw JSON.
2. `blueprints.summary(bp)`: type, label, game version, entity counts, size in tiles, recipes, wires, quality, parameters, snap-to-grid. A book lists its entries; a planner shows only its settings.
3. For a book, `blueprints.book_entries(bp)` gives a flat list with `path`, `type`, `label`, `entities` and `item` (the entry itself). Pick one and use its `item` below.

## Build it as ghosts

1. `data_lua` reads, for every name in `summary(bp)["counts"]`, `prototypes.entity[n]` (`nil` means the save does not have it), plus `tile_width`/`tile_height` as `sizes = {name: [w, h]}` and the names with `not p.supports_direction` as `fixed` (poles, chests, lamps: the game never turns them). Never assume vanilla values for modded names.
2. `blueprints.check(bp, known=set_of_existing_names)` must be `[]`, or every line must be understood:
   - "does not exist in this save": vanilla print in a modded save (Nullius). Tell the player; do not place it partially.
   - "parameterised": the print has `parameter-N` placeholders. Substitute them before placing.
   - "made by 1.x": directions are converted, but some names changed in 2.0 (1.x `stack-inserter` is `bulk-inserter`).
   - "snap-to-grid": `to_ghosts` centres it like a free print. Tell the player the grid is ignored.
3. `items = blueprints.to_ghosts(bp, anchor, direction=0, mirror=False, sizes=sizes, fixed=fixed)`. This gives the same positions as the game's `build_blueprint{position=anchor, direction=...}`: the print's bounding box is centred on `anchor`.
   - `direction` is 0/4/8/12 (or the words), clockwise. `mirror` is `True`/`"horizontal"` (the F key) or `"vertical"` (G), applied before the rotation.
   - Each item is `{name, x, y, direction, type?, extra}`. `type` is input/output on underground halves. `extra` holds the recipe, quality, lamp colour/always_on, splitter priorities, filters, control_behavior, items and `entity_number`.
4. Check the target area is free with the same blocked-tiles query you use for belts, then `kc.place_ghosts(surface, items)` in one `factorio_query`. `place_ghosts` places only name, position, direction and type. Settings in `extra` and the wires (`blueprints.wires(bp)`, matched through `extra.entity_number`) are not applied yet; tell the player which settings are missing.

## Write a string

`blueprints.from_entities(items, label=..., wires=...)` turns any planner's ghost list (belts, poles, pipes) into a 2.0 blueprint. Then `blueprints.encode(bp)` gives the string, for the player to import or for `https://fbe.factorygamefan.com/#source=<string>` as a preview. `encode(decode(s))` keeps the contents unchanged.

## Not covered

Curved/diagonal rails under a mirror (refused), snap-to-grid and absolute snapping (centred like a free print), tiles (listed in `summary`, not placed), parameter substitution, and applying `extra` settings and wires in the game.
