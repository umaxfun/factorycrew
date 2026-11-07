# kc primitive library

The pure-Python building blocks Kira imports with `import kc` inside `factorio_compute`. They cover geometry, occupancy, search, network joining, cover, wiring, exact LP, blueprint strings, adapters for the mod's world readers, and validators for a plan before it is placed. Stdlib only, Python 3.9+ (`mcp/kc/__init__.py:1`). The skills in `skills/*/lib` are built on it.

## Main files

- `mcp/kc/__init__.py`: the package surface. It re-exports `geo` names and `Grid` and imports `MODULES` one by one (:32-37). It also re-exports the old `kc_compute` helpers `astar, connect, unreached, unwired` (:40). The docstring lists the layers (:3-15).
- `mcp/kc/geo.py`: points in any form, tile centres, `snap`, `footprint`, `rect`, 16-way directions, `rotate`/`mirror`, distances, `spiral`.
- `mcp/kc/grid.py`: `Grid` (tile → occupant, optional `bounds`): `free`, `passable`, `can_place`, `place`, `remove`, `first_fit` (`mcp/kc/grid.py:11-92`).
- `mcp/kc/search.py`: `bfs`, `dijkstra`, `astar` on any neighbour function, with multi-source starts and a `budget`; also `grid_neighbors`, `grid_path`, `components`, `unreached`.
- `mcp/kc/steiner.py`: `join` (one tile network touching every terminal), `mst`, `join_nodes`, `unjoined`, `is_connected`.
- `mcp/kc/cover.py`: `greedy` set cover, `prune`, `uncovered`, `square_reach`, `area_overlaps`, `candidates_around`.
- `mcp/kc/wires.py`: the shorter-reach link rule (`can_link`, `links`, `components`, `unwired`, `too_long`) and relay poles (`chain`, `relay_chain`, `join_groups`).
- `mcp/kc/lp.py`: `solve`, an exact two-phase simplex on `Fraction`, plus `as_str`/`as_float`/`check` and the raw `pivot`/`tableau_simplex`.
- `mcp/kc/bp.py`: blueprint codec (`decode`/`encode`), `kind`, `book_entries`, `summary`, `transform`, `to_ghosts`, `from_entities`, `collisions`, `check`. Licences of the ported parts are in `mcp/kc/LICENSE-blueprints.txt`.
- `mcp/kc/world.py`: the Python half of the mod's `kc.scan` / `kc.proto` / `kc.resources` / `kc.best`. It holds `grid_from_scan`, `specs_from_proto`, `pole_spec`/`drill_spec`/`belt_spec`/`beacon_spec`/`machine_spec`, `pipe_ports`, `ore_tiles`, `check_scan`, `check_specs`.
- `mcp/kc/checks.py`: `overlaps`, `blocked`, `unpowered`, `unwired`, `disconnected` and `all(plan, specs, ...)`.
- `mcp/kc/_compat.py`: the first `kc_compute` helpers (`astar`, `connect`, `unwired`, `unreached`), kept for code written against them.
- `mcp/kc_compute.py`: a shim, so `import kc_compute as kc` still works (`mcp/kc_compute.py:1-3`).
- `mcp/kc/atlas.py` and `mcp/kc/circuits.py` are in the same package and have their own docs: [Atlas](atlas.md) ([../atlas.md](../atlas.md)) and [Circuits](circuits.md) ([../circuits-format.md](../circuits-format.md)).

## Talks to

- Used by: `factorio_compute`, which copies every top-level `mcp/kc/*.py` into the sandbox as the package `kc` and puts `import kc` in the prelude (`mcp/factorio_mcp.py:284-290`, :366-369). See [MCP server](mcp-server.md).
- Used by: [Skills](skills.md) `pipes`, `power`, `production` (`grep -ln "import kc\|from kc" skills/*/lib/*.py`). `production` solves with `kc.lp` (`tests/unit/test_kc_lp.py::test_production_uses_kc_lp`).
- Used by: [Kira agent](kira-agent.md), whose rules name the functions she should call (`agent/factorio-companion.md:68-75`).
- Used by: the MCP server in-process, for `kc.atlas` only (`mcp/factorio_mcp.py:704`).
- Uses: JSON from the mod's Lua readers in `mod/kc-companion/scripts/helpers.lua` ([Build helpers](build-helpers.md)), which reaches kc as `data` from `data_lua` (`mcp/kc/world.py:1-11`). Nothing in kc talks to the game or the bridge (the imports: `grep -n "^import\|^from" mcp/kc/*.py`).

## Must stay true

- Stdlib only, importable on Python 3.9: `from __future__ import annotations`, no `match`. Live, the sandbox runs `/usr/bin/python3`, which is 3.9.6 here (`agent/factorio-companion.json:17`; `tests/unit/test_kc_world.py::test_python39_safe_source` and `tests/unit/test_kc_circuits.py::test_python39_import` check only `world.py` and `circuits.py`).
- Every public module is in `MODULES`, so `kc.<name>` works after a plain `import kc` (`tests/unit/test_compute.py::test_every_kc_module_is_reachable_as_kc_attribute`).
- Lower layers never import higher ones. `geo` imports nothing from kc. `grid`, `cover`, `wires` and `bp` import `geo`. `search` imports `geo`, `steiner` imports `geo, search`, `world` imports `geo, grid`, and `checks` imports `cover, geo, search, wires` (`grep -n "^from \." mcp/kc/*.py`; no test).
- The old names `kc.tile, xy, footprint, astar, connect, unwired, unreached` keep working (`tests/unit/test_kc_geo_grid.py::test_old_kc_compute_names_still_work`, `tests/unit/test_compute.py::test_kc_helpers_*`).
- Any point form is accepted: `(x, y)`, `[x, y]`, `{x, y}`, `{position: {x, y}}` (`mcp/kc/geo.py:8`; `tests/unit/test_compute.py::test_kc_helpers_take_dicts_too`).
- A tile is named by its centre `x.5, y.5`. Odd-sized entities sit on tile centres and even-sized ones on integers. Directions are 2.0 numbers: N 0, E 4, S 8, W 12 (`mcp/kc/geo.py:3-8`; `tests/unit/test_kc_geo_grid.py::test_points_tiles_and_snap`, `::test_directions`).
- Results are deterministic: same input, same output, with no dependence on dict or set order (`tests/unit/test_kc_search.py::test_results_are_deterministic`, `tests/unit/test_kc_steiner.py::test_join_random_fields_are_connected_and_deterministic`, `tests/unit/test_kc_bp.py::test_deterministic`, `tests/unit/test_kc_lp.py::test_deterministic_and_independent_of_dict_order`).
- Validators return a list of problems, and `[]` means fine (`mcp/kc/checks.py:1-12`; `tests/unit/test_kc_checks.py`).
- Wires: two nodes link when their distance is at most the shorter reach, plus 1e-9 (`mcp/kc/wires.py:6-8`, :37; `tests/unit/test_kc_wires.py`, `tests/unit/test_compute.py::test_kc_unwired_uses_the_shorter_reach`).
- `kc.bp.to_ghosts` puts every ghost where the game's `build_blueprint` does, in all four rotations, plain and mirrored (`tests/integration/test_kc_bp_game.py::test_build_blueprint_matches_to_ghosts`, `::test_real_print_builds_where_to_ghosts_says`).
- `kc.lp` is exact and always terminates: it switches to Bland's rule on degenerate pivots (`tests/unit/test_kc_lp.py::test_beale_cycles_under_plain_dantzig_but_solve_terminates`, `::test_random_lps_match_vertex_enumeration`).
- `kc.world` reads what both 2.0.77 and 2.1.23 send, and an empty Lua table as either `{}` or `[]` (`tests/unit/test_kc_world.py::test_fixtures_cover_both_game_versions`; `_list`, `mcp/kc/world.py:32`).

## Easy to break

- Python 3.10+ syntax in any kc module passes `./scripts/test-unit.sh`, which runs on 3.12 (`scripts/test-unit.sh:15`). It then fails Kira's `import kc` as a whole, because the guard in `__init__.py` catches only `ImportError` and not `SyntaxError` (`mcp/kc/__init__.py:34-37`). Check with `cd mcp && /usr/bin/python3 -c "import kc"`.
- A new module that is left out of `MODULES` imports fine in unit tests (`from kc import x`) but is missing as `kc.x` in the sandbox (`tests/unit/test_compute.py::test_every_kc_module_is_reachable_as_kc_attribute`).
- Only top-level `mcp/kc/*.py` files reach the sandbox. A subpackage or a data file next to the modules is not copied (`mcp/factorio_mcp.py:366`).
- Renaming or changing a function's arguments breaks the skills that call it and Kira's rules that name it (`agent/factorio-companion.md:71-75`, `skills/pipes/lib/pipes.py`, `skills/power/lib/power.py`, `skills/production/lib/production.py`).
- When `kc.scan`/`kc.proto`/`kc.resources`/`kc.best` change shape in the mod, `kc.world` stays green on old fixtures until they are re-recorded. The level 1 tests in `mod/kc-companion/tests/test-helpers.lua:147-148` write them to `script-output/kc-world/`, and they are copied into `tests/unit/fixtures/kc_world/<version>/` by hand: no script in `scripts/` or `tools/` mentions them (`grep -rl "kc-world\|kc_world" scripts tools` is empty).
- `kc.bp.to_ghosts` returns only name, position, direction and type. Entity settings (`extra`) and wires are not applied by `kc.place_ghosts` (`mcp/kc/bp.py` docstring). Curved or diagonal rails under a mirror are refused (`mcp/kc/bp.py` docstring, "Not covered").
- A tuple of two numbers counts as one tile node in `kc.search`. Starting from the integer nodes 1 and 2 needs the list `[1, 2]` (`mcp/kc/search.py` docstring).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_kc_geo_grid.py tests/unit/test_kc_search.py tests/unit/test_kc_steiner.py tests/unit/test_kc_cover.py tests/unit/test_kc_wires.py tests/unit/test_kc_lp.py tests/unit/test_kc_bp.py tests/unit/test_kc_world.py tests/unit/test_kc_checks.py tests/unit/test_compute.py`: the library, and the library inside the real compute sandbox. 168 tests, ~6 s.
- `./scripts/test-unit.sh tests/unit/test_pipes.py tests/unit/test_power.py tests/unit/test_production.py`: the skills built on kc.
- `cd mcp && /usr/bin/python3 -c "import kc; print(sorted(m for m in kc.MODULES if not hasattr(kc, m)))"`: must print `[]` on the interpreter Kira uses.
- `./scripts/test-integration.sh -k kc_bp` (Docker, headless 2.1.24): `tests/integration/test_kc_bp_game.py`, `kc.bp` against the game's own `build_blueprint`. The whole level 2 run is ~65 s.
- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh` (~11 s each): the Lua readers whose output `kc.world` parses (`mod/kc-companion/tests/test-helpers.lua`).
- Fixtures: `tests/unit/fixtures/kc_world/{2.0,2.1}/{scan,proto,resources,best_belt}.json` (recorded in game, read by `test_kc_world.py`). `tests/data/blueprints/*.txt` holds real blueprint, book and planner strings read by `test_kc_bp.py`; `red-circuits-block.txt` is also read by `test_kc_bp_game.py`.
- End to end: `tests/app_e2e/test_uncoded.py` (Kira using `kc.scan/proto`, `kc.wires/cover/checks` on her own) under `./scripts/test-app-e2e.sh`.

## Decisions

- When SPEC, README and code disagree, the code wins (confirmed by the repository owner, [decisions.md](../decisions.md#when-spec-readme-and-the-code-disagree-the-code-wins-and-the-docs-are-fixed)). No other kc decision is written down with a reason in the repo docs. The module docstrings record origins and licences (`mcp/kc/steiner.py`, `mcp/kc/lp.py`, `mcp/kc/bp.py`, `mcp/kc/wires.py`).

## Terms

- **tile**: a tile named by its centre `(x.5, y.5)`.
- **plan**: the list Kira hands to `kc.place_ghosts`: `[{name, x, y, direction?, type?}, ...]` (`mcp/kc/checks.py:3`).
- **specs**: `kc.world.specs_from_proto(proto)`, a map from name to a normalised prototype. A name missing from it counts as 1×1 in `kc.checks`.
- **terminal**: a point that `kc.steiner.join` must connect. It may offer several alternative tiles.
- **shorter-reach rule**: the game links two poles only within the smaller of their two wire reaches.
- **`kc` (Lua)**: the mod's helpers inside `factorio_query` / `data_lua` (`kc.scan`, `kc.place_ghosts`...). A different thing from this Python package, though the two share the name.
