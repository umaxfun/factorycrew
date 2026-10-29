# Source

Ported from **Mining Patch Planner** by Rimbas.

- Upstream: https://github.com/rimbas/mining-patch-planner (mod page: https://mods.factorio.com/mod/mining-patch-planner)
- Commit read: `3391e3e65332a365c2343c7afa67fcb2449c8c49` (master, pushed 2026-10-20)
- License: MIT (`gh api repos/rimbas/mining-patch-planner --jq .license.spdx_id` = `MIT`; LICENSE file read). Full text in `LICENSE-upstream`.

## What was ported (files at that commit)

- `layouts/simple.lua`
  - `_placement_attempt`: paired drill rows on a lattice; the top row faces the belt below it, the bottom row the belt above it; a drill is placed where its placement predicate holds, positions with ore only in the outer reach are postponed.
  - `prepare_layout_attempts`: try every lattice offset (here: every offset inside one full period, both axes).
  - `layout_attempts` / `layout_attempts_fallback`: sort by heuristic score, process postponed drills, keep the first attempt that leaves no ore unmined, else the one leaving least.
  - `process_grid` / `process_grid_convolution` (separable convolution of ore under drill body and mining area): replaced by 2D prefix sums, which give the same counts.
- `layouts/common.lua`
  - `simple_miner_placement` (inner ore > ceil((size/2)^2)) and `overfill_miner_placement` (mode `"full"`).
  - `simple_layout_heuristic`, `overfill_layout_heuristic`, `heuristic_penalties`, `finalize_heuristic_values` (densities, centricity, amount deviation).
  - `process_postponed` (sort postponed by unconsumed ore, then outer, then inner; add while they reach unmined ore).
- `mpp/mpp_util.lua` `miner_struct`: output tile from `vector_to_place_result`, rotated with the drill.

## What was changed

- Python, stdlib only, deterministic (tie-breaks on offset and position where Lua's unstable sort had none).
- No game API: ore, blocked tiles, other-resource tiles, drill and belt geometry come in as arguments.
- Instead of rotating the grid for a user-picked direction, both orientations are tried (a flip/transpose frame), and the better one is kept by (unmined ore, score).
- Mining area = tiles whose center is strictly inside drill center ± `mining_drill_radius`, computed from the radius directly (MPP's even-size parity adjustment is implied by this rule).
- Lane multiplier in the simple heuristic counts belts actually used (`1/(1+belts)`) instead of `1/(1+ceil(rows/2))` over every lattice row: MPP's form scored two half-filled pairs equal to one full pair.
- First-row penalty uses the first lattice row whose mining area reaches the patch.
- A drill is not placed where its lane is cut off from the exit by a blocked tile.
- Belts are new (not MPP's `belt_planner`): one belt per pair, all side-loading into one collector along the patch edge, ending in one exit belt at the corner nearest `toward`.
- Not ported: poles, lamps, pipes for fluid ores, beacons, landfill, deconstruction, compact/sparse/logistics/blueprint layouts, belt merge strategies, quality.
