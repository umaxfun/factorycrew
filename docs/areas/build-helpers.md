# Build helpers and trial runs

The `kc` table in the query environment: Lua helpers Kira calls inside `factorio_query` to read the world (scan, prototypes, ore, best entity), place ghosts safely, power them, and try a build on a hidden copy of its area before she says "done". Contract for the agent: [SPEC.md, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio).

## Main files

- `mod/kc-companion/scripts/helpers.lua`: the `kc` module. `place_ghosts`, `inserter_ends`, `unpowered`, `poles`, `nearest_pole` (lines 21-215); world readers `scan`, `proto`, `resources`, `best` (lines 420-800); re-exports `circuits`, `trial_start`, `trial_report`, `grid_power` (lines 802-804).
- `mod/kc-companion/scripts/trial.lua`: `grid_power(pole)`, `start(surface, area)`, `report(opts)`; state in `storage.kc_trial`.
- `mod/kc-companion/tests/test-helpers.lua`, `mod/kc-companion/tests/test-trial.lua`: level 1 tests, called through `protocol.run_query` the way Kira calls them.
- `tests/unit/fixtures/kc_world/2.0/`, `.../2.1/`: `kc.scan`/`kc.proto`/`kc.resources` results recorded by `test-helpers.lua` (`test-helpers.lua:147-152`), read by the Python `kc.world` tests.

## Talks to

- Uses: [Mod core](mod-core.md) query execution, which puts this module in the environment as `kc` (`mod/kc-companion/scripts/protocol.lua:4`, `:185`).
- Uses: the circuit reader, re-exported as `kc.circuits` (`helpers.lua:802`); see [Circuits](circuits.md).
- Used by: [Live panels](panels.md), which add `kc.panel`, `kc.panel_remove`, `kc.show_on_map` to the same table (`mod/kc-companion/scripts/panels.lua:719-722`).
- Used by: the Python `kc` library. `mcp/kc/world.py` adapts `kc.scan`/`kc.proto`/`kc.resources`/`kc.best` results (`mcp/kc/world.py:1-7`); `mcp/kc/checks.py` validates plans in `kc.place_ghosts` format (`mcp/kc/checks.py:3`); `mcp/kc/bp.py` `to_ghosts` produces `place_ghosts` items (`mcp/kc/bp.py:306`).
- Used by: the agent, through its rules ("Building", "Done means it works": `agent/factorio-companion.md:29-66`) and the `factorio_query` tool description (`mcp/factorio_mcp.py:49-50`).

## Must stay true

- `place_ghosts` refuses an item that overlaps an entity, a ghost (also one placed earlier in the same call) or bad ground, and places nothing for it; it returns `placed` and `failed` with the item `index` (`test-helpers.lua` "places ghosts and refuses overlaps...").
- `place_ghosts` checks with `build_check_type.manual_ghost` through `can_place_entity` before `create_entity` (`helpers.lua:75-81`).
- `place_ghosts` snaps odd sizes to tile centers and even sizes to integers, with width and height swapped for east/west (`helpers.lua:66-72`) (inferred: no level 1 test feeds an unsnapped position).
- `place_ghosts` reports `unpowered` + `hint` while an electric ghost is outside every pole and pole ghost, and drops them once a pole covers it (`test-helpers.lua` "stops reporting unpowered once a pole covers the design").
- `place_ghosts` reports each inserter's `picks`/`drops` in `inserters` and `inserter_hint`, with "nothing at one end" when an end is empty (`test-helpers.lua` "place_ghosts says what each inserter picks from and drops into").
- `inserter_ends` matches a real inserter's `pickup_position`/`drop_position` in all four directions (`test-helpers.lua` "matches a real inserter's pickup and drop in every direction").
- `poles()`: `cover` is the largest supply area, `reach` the longest wire, only poles with an enabled recipe or in the player's inventory (`test-helpers.lua` `kc.poles` tests).
- `nearest_pole` ignores ghosts and returns the grid's `sources`/`production_w` (`test-helpers.lua` "finds the closest real pole and ignores ghosts"; `test-trial.lua` `kc.grid_power`).
- `grid_power` tells a producing grid from a pole with no source (`test-trial.lua` "tells a producing grid from a pole with nothing behind it").
- `scan`, `resources`, `circuits` cut their result to a JSON budget below the 64 KB query cap and say so (`truncated`, `counts`) (`test-helpers.lua` "cuts to the JSON budget and says so"; `helpers.lua:505`).
- Every world-reader field goes through `get`, so a property missing on 2.0 or 2.1 reads as absent, not an error (`helpers.lua:218-230`); both versions run level 1 and have recorded fixtures (`tests/unit/test_kc_world.py::test_fixtures_cover_both_game_versions`).
- A trial never touches the player's surface: the player's ghosts stay ghosts, the trial surface is hidden from the player force and deleted by `report{finish = true}` (`test-trial.lua` "runs a working belt plan on a hidden copy...").
- A trial shows plates arriving for a correct plan, none for backwards inserters (both reported not working), and `no_power` plus a "has no power source" note for a dead grid (`test-trial.lua` `kc.trial` tests).

## Easy to break

- Calling `surface.create_entity{name = "entity-ghost"}` instead of `place_ghosts`: it silently replaces a ghost it lands on, which once put a substation over a pumpjack ([SPEC, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio)).
- Reading pole stats as fields: on 2.0 prototypes expose `get_supply_area_distance()` / `get_max_wire_distance()` as methods only (`agent/factorio-companion.md:32`; `helpers.lua:33`).
- A type name the running game lacks in one `find_entities_filtered{type = {...}}` raises; `grid_power` queries one type at a time under `pcall` (`trial.lua:84-85`).
- `unpowered` searches poles within radius 64 of each item (`helpers.lua:169`); a pole type with a larger supply distance would be missed (inferred).
- A fresh level 1 data dir starts with Space Age on, and `kc.best` then ranks its entities too; `scripts/test-mod.sh` writes a DLC-off `mod-list.json` first (`scripts/test-mod.sh:21-23`).
- `grid_power` reads 5-second flow statistics, so a test must wait a full window (330 ticks) and use a load that draws by day (a radar, not a lamp) (`test-trial.lua` `kc.grid_power` test comments).
- The trial copies only the area: sources and destinations outside it do not exist there (`trial.lua:196` note; agent rules `agent/factorio-companion.md:55`), so a too-small area reports a working design as starved (inferred).
- Only one trial exists at a time: `start` deletes every `kc-trial-*` surface and overwrites `storage.kc_trial` (`trial.lua:106-120`, `:198`).
- A stand-in is added for every network in the area with `sources > 0`, even one that produced nothing in the last 5 s (`trial.lua:165`); the comments at `trial.lua:113-115` and `:155` say "produces power now".

## How to check a change

- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh`: level 1 on 2.0.77 and 2.1.23, ~11 s each; covers `test-helpers.lua` and `test-trial.lua`, and rewrites `.test-data/level1*/script-output/kc-world/*.json`.
- After a reader change, copy the new `kc-world` JSON into `tests/unit/fixtures/kc_world/<version>/` by hand (no script does it: `grep -rn kc-world scripts/` finds nothing), then `./scripts/test-unit.sh tests/unit/test_kc_world.py tests/unit/test_kc_checks.py` (~1 s).
- `./scripts/test-integration.sh` (~65 s): `tests/integration/test_belts_game.py`, `test_pipes_game.py`, `test_power_game.py`, `test_solar_game.py`, `test_mining_game.py`, `test_beacons_game.py` place plans through `kc.place_ghosts` and revive them.
- `./scripts/test-app-e2e.sh -k uncoded` (real client, real model): `tests/app_e2e/test_uncoded.py` checks Kira calls `kc.trial_start` before replying (lamp power, belt around a wall) and uses `kc.grid_power` on the test grid.

## Decisions

- Ghosts only, through `kc.place_ghosts`: a plain ghost `create_entity` replaces ghosts silently. [SPEC, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio) and ["Agent rules"](../../SPEC.md#agent-rules).
- Kira changes existing entities only after the player's consent. Confirmed by Michael, 2026-10-29.
- Every build is tried with `kc.trial_start`/`kc.trial_report` before "done", so the agent sees it work without touching the player's surface. [SPEC, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio), agent rules "Done means it works".
- These helpers run inside `factorio_query`, which executes arbitrary Lua with no sandbox; read-only is an agent rule. Confirmed by Michael, 2026-10-29; [SPEC, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio).

## Terms

- **ghost**: an `entity-ghost`, a build plan the player's robots or hands complete.
- **cover / reach pole**: the researched pole with the largest supply area / longest wire (`kc.poles()`).
- **trial surface**: `kc-trial-<tick>`, the hidden lab-tile copy a trial runs on.
- **stand-in**: an `electric-energy-interface` plus a pole wired to the copied grid pole, replacing the player's power plant in a trial.
- **grid sources**: generators, solar panels, accumulators and the like on a pole's electric network (`trial.lua:72`).
