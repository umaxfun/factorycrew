# Source

Ported from **Coppermine-factorio/OilOutpostPlanner**, `FindBeaconLocations` in `layout.lua`
(lines 1088-1253), plus the `AnySpotForbidden` free-footprint test it calls (lines 248-271).

- Upstream: https://github.com/Coppermine-factorio/OilOutpostPlanner
- Commit read: `72015903b0ed492c28b6b5fa7cae575873c9c9f5` (2026-06-23, branch `main`)
- License: MIT (`gh api repos/Coppermine-factorio/OilOutpostPlanner --jq .license.spdx_id` = `MIT`,
  and the LICENSE file, copied verbatim to `LICENSE-upstream`). Copyright (c) 2024 Coppermine.

## What was kept

The algorithm: list every beacon position whose footprint is free and that reaches at least one
target; score a position by the sum over its targets of `effect(n + 1) - effect(n)` with
`effect(n) = n * profile[n]` (the last profile entry past the end, `{1}` when the prototype has
none); take the best, add 1 to its targets' counts, drop positions it overlaps, rescore, and stop
when the best score is below the threshold (`min_beacon_utility` upstream, `min_utility` here).

## What changed

- Lua with live prototypes -> stdlib Python with every size, reach and profile passed in.
- Reach: upstream uses one Chebyshev radius `supply_area_distance + beacon_radius + entity_radius - 1`
  for all targets (all pumpjacks). Here the test is per axis, per machine: the beacon's footprint
  grown by `supply_area_distance` must overlap the machine's collision box (default footprint
  minus 0.3 per side, or the prototype's box). For 3x3 beacons and 3x3 machines it gives the
  same answer as upstream; it also handles 5x5 refineries and non-square entities.
- Overlap: upstream compares Chebyshev distance with `2 * beacon_radius` (square beacons only);
  here per axis with the beacon's width and height.
- Ties: upstream's `table.sort` is not stable, so equal scores pick an arbitrary position. Here:
  highest score, then smallest y, then smallest x.
- Upstream drops a position for good once its score is below the threshold; here every live
  position is rescored each round (same result for a decreasing profile, correct for others).
- Default threshold 0.5 instead of upstream's 2 (see SKILL.md: with the 2.0 `1/sqrt(n)` profile, 2
  places almost nothing around assembler rows).
- New: `min_per_machine` first phase, `max_beacons` cap, `existing` beacons, per-machine report
  of counts and effects, `evaluate()` for any layout and `check()`.
