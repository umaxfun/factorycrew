---
name: belts
description: Route a transport belt between two points around obstacles, with underground pairs where they pay off. Use for any "belt from A to B", feeding a machine, or connecting an outpost to the base.
---

# Belt routing

Inside `factorio_compute`: `import belts`. Pure Python, no game access; you feed it the obstacles.

## Steps

1. `data_lua` reads the area and the tier:
   - blocked tiles: every entity and ghost in the box around start and goal (plus margin), as tile centers. Big entities block every tile they cover (`kc.footprint` in Python, from `tile_width/tile_height`). Add water and cliffs.
   - existing undergrounds of the SAME tier on the way, `{x, y, direction}`: a new pair must not span one on the same axis.
   - the tier from prototypes, never hardcoded names (Nullius renames belts): the belt's `related_underground_belt.name` and that prototype's `max_underground_distance`. Take the best researched tier unless told otherwise.
2. `r = belts.route(start, goal, blocked, belt=..., underground=..., max_underground=..., start_dir=..., goal_dir=..., undergrounds=...)`
   - `start_dir`/`goal_dir`: 0 north, 4 east, 8 south, 12 west (or the words). `goal_dir` is where the LAST belt points, e.g. into the machine's input.
   - Fails with `reason` when the goal is walled off within `margin` (default 32) or the budget is spent; widen `margin` once, then tell the player.
3. `belts.check(r["entities"], blocked, undergrounds, max_underground)` must be `[]`.
4. `result = r["entities"]`, then one `factorio_query` with `kc.place_ghosts(surface, data)`. The list is in flow order and carries `type = "input"/"output"` on underground halves; pass it unchanged.

## Costs

Each tile costs 1, a turn +0.5, an underground pair its tiles + 1.5. So a pair is used only when it saves at least 2 tiles of detour. Raise `underground_cost` to save undergrounds, lower `turn_cost` for fewer constraints.

## Not covered

Joining an existing belt (side-loading, lane choice), splitters, balancers, and several parallel belts. Route each belt separately; add the earlier belts to `blocked` before routing the next.
