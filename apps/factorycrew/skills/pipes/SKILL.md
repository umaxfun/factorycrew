---
name: pipes
description: Plan fluid networks - an oil field (pumpjacks on wells, their directions, one pipe network to a sink with pipe-to-ground pairs), or one pipe network joining any set of tiles around obstacles. Use for "build an oil outpost", "pipe these together", "connect these pumpjacks".
---

# Pipe networks and oil fields

Inside `factorio_compute`: `import pipes`. Pure Python, no game access; you feed it the obstacles and prototype numbers.

## Steps (oil field)

1. `data_lua` reads the area and the prototypes (never hardcode names; Nullius renames everything):
   - wells: positions of the fluid resource (`find_entities_filtered{type = "resource", name = ...}`); they are the pumpjack centers.
   - blocked: tile centers of every entity and ghost in the field's box plus ~6 tiles (big entities block each tile they cover, `kc.footprint`), water, cliffs. Not the wells.
   - foreign_pipes: tiles of existing fluid entities nearby (`type = {"pipe", "pipe-to-ground", "storage-tank", "pump"}`). Also put them in blocked.
   - the pumpjack: `tile_width`, and its output tile per direction:
     ```lua
     local c; for _, fb in pairs(pj.fluidbox_prototypes) do if fb.production_type == "output" then c = fb.pipe_connections[1] end end
     local step = {[0] = {0, -1}, [4] = {1, 0}, [8] = {0, 1}, [12] = {-1, 0}}
     local offs = {}
     for i, d in ipairs({0, 4, 8, 12}) do
       local p, od = c.positions[i], ((c.direction or 0) + d) % 16
       offs[tostring(d)] = {p.x + step[od][1], p.y + step[od][2]}
     end
     ```
   - the pipe and its pipe-to-ground names, and the pipe-to-ground's `max_underground_distance` (vanilla 10 = the most tiles between the two halves).
2. `r = pipes.oil_field(wells, blocked, pumpjack_size=..., output_offsets={int(k): v for k, v in offs.items()}, pumpjack=..., pipe=..., pipe_to_ground=..., pipe_to_ground_reach=..., toward=(x, y), foreign_pipes=...)`
   - `toward`: where the oil goes (the refinery, the base); the sink is the free tile on the field's edge nearest it. Or give `sink=(x, y)` yourself.
   - Fails with `reason` (a pumpjack on a blocked tile, overlapping pumpjacks, a walled-off well); fix the input or drop that well, then tell the player.
3. `r["problems"]` must be `[]`. `pipes.check(r["entities"], blocked, sink=r["sink"], foreign_pipes=..., <same sizes, offsets, names, reach>)` re-verifies an edited layout.
4. `result = r["entities"]`, then `kc.place_ghosts(surface, data)` unchanged (pumpjacks carry `direction`; pipe-to-grounds carry `direction` only, no `type`). Power the pumpjacks after (the place_ghosts hint says so). Then pipe from `r["sink"]` to where the oil is needed.

## Other calls

- `pipes.join(terminals, blocked, margin=4)`: one network of tiles joining all terminals; a terminal may be `{"options": [tiles]}` and `choice` says which tile it got. Steiner-tree heuristic, not the shortest per pair.
- `pipes.compress(tiles, pipe=..., pipe_to_ground=..., pipe_to_ground_reach=..., ports=[(x, y, side_of_machine)], keep=[sink])`: bury straight runs as pairs; side branches and machine ports stay connected.

## Not covered

Crossing a foreign pipe or belt line underground (the join only goes around it: widen `margin` or move the sink), beacons, poles, heat pipes, and fluids that must not mix within the field (one field = one fluid). About 18% fewer pipe tiles than `kc.connect` with all pumpjacks facing one way.
