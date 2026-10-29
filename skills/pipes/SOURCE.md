# Source

`lib/pipes.py` ports two functions of **Oil Outpost Planner** by Coppermine:

- Upstream: https://github.com/Coppermine-factorio/OilOutpostPlanner
- Commit read: `72015903b0ed492c28b6b5fa7cae575873c9c9f5` (main, 2026-06-23)
- License: MIT (`gh api repos/Coppermine-factorio/OilOutpostPlanner --jq .license.spdx_id` = MIT, and
  the LICENSE file read). Copied verbatim to `LICENSE-upstream`.
- File: `layout.lua`
  - `FloodFill` (L327) and `SolveSteinerTree` (L372) -> `join()`
  - `FindPipePaths` / `ReplacePipes` (L867-1031) -> `compress()`
  - the four output tiles per pumpjack as one terminal's alternatives (`Plan`, L1586-1710) -> `oil_field()`

## What changed

- Lua -> stdlib Python 3.9; tables keyed by tile tuples instead of `Pos2Str` strings; the
  `choose_subtarget` callback became a returned `choice` list; iteration over targets is in
  index order so the result is deterministic.
- Dropped: debug rendering, `forbid_adjacency` (heat pipes / poles only), `skip_flood_fill`.
  The 10 000-entry and 500-tile caps became one `budget` on expanded tiles.
- Bounds: the terminals' bounding box plus `margin` (upstream: pumpjacks plus a padding for
  beacons and poles).
- `compress()`: upstream turns a run end into a pipe-to-ground even when it faces nothing; here a
  run end is buried only if it faces a pipe or the machine port it serves, otherwise the run is
  shortened by one. Ports are passed in explicitly instead of upstream's pumpjack-direction map.
  The reach is the most tiles between the two halves (`max_underground_distance`, checked in game);
  upstream's chain splits a run into `ceil((L+1)/(reach+1))` pairs, here `ceil(L/(reach+1))`.
- Output tiles come from `output_offsets` (read from the prototype by Kira) instead of the
  mod's own `pipe_connections[1].positions` lookup, so any fluid-output mining drill works.
- New: the sink tile on the field's edge, foreign-pipe avoidance, and `check()`.
