# Circuits

Reads any circuit network in the game into a plain JSON snapshot (Lua, `kc.circuits`) and simulates, explains and traces it in Python (`kc.circuits` in `factorio_compute`), so Kira can say why a lamp, inserter or station does what it does and what happens if its inputs change. The snapshot format is [docs/circuits-format.md](../circuits-format.md).

## Main files

- `mod/kc-companion/scripts/circuits.lua`: the reader `M.read(surface, opts)` (line 601), exposed as `kc.circuits` (`helpers.lua:802`); per-kind params (lines 104-206), control behaviors (`M.control`, line 372), seeds (line 516), wire walk (line 633).
- `mcp/kc/circuits.py`: `check`, `load` -> `Circuit` (`step`, `run`, `with_inputs`, `exogenous`, `controls`, `trace`), per-kind evaluators, `explain`, `diff`/`add`/`sub`/`wrap`. API summary in its docstring (lines 1-47).
- `docs/circuits-format.md`: the v1 snapshot contract and the semantics the simulator follows.
- `tests/unit/test_kc_circuits.py` + `tests/unit/fixtures/circuits/*.json` (6 snapshots, each for 2.0 and 2.1).
- `mod/kc-companion/tests/test-circuits.lua`: level 1 reader tests; each writes its snapshot to `script-output/kc-circuits/` (`test-circuits.lua:2-12`).
- `tests/integration/test_kc_circuits_game.py`: level 2, simulator against the live game.

## Talks to

- Uses: [Mod core](mod-core.md) query execution (`kc` table in `mod/kc-companion/scripts/protocol.lua:185`); the result travels as a `factorio_query` result, inside its 64 KB cap ([SPEC, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio)).
- Used by: `factorio_compute`, where `import kc` exposes `kc.circuits` (`mcp/kc/__init__.py:32`; `mcp/factorio_mcp.py:125`); the pattern is read in `data_lua`, then `kc.circuits.load(data)` in Python.
- Used by: the agent rules, "Circuits, anywhere" and the leftover-cargo station playbook (`agent/factorio-companion.md:74`, `:143-147`).
- Lives next to: [Build helpers](build-helpers.md), which re-export the reader from `helpers.lua`.

## Must stay true

- The reader and the simulator agree on format v1: `check()` rejects any `v` other than 1 (`mcp/kc/circuits.py:548`), the reader writes `M.FORMAT_VERSION = 1` (`circuits.lua:8`).
- Every recorded reader snapshot passes `check`, loads, runs 200 ticks, serialises to JSON and explains (`tests/unit/test_kc_circuits.py::test_recorded_fixture`).
- A simulated clock equals the game tick for tick over 5 read pairs, and its period is 60 (`tests/integration/test_kc_circuits_game.py::test_clock_simulation_matches_the_game_tick_for_tick`).
- A `with_inputs` "chest empty" prediction matches what the game does once the chest is emptied, and `explain` names the condition (`test_kc_circuits_game.py::test_what_if_scenario_matches_the_game`).
- Each combinator adds one tick of delay; AND binds tighter than OR; values wrap at 32 bits (`test_kc_circuits.py::test_one_tick_delay_per_combinator`, `::test_and_binds_tighter_than_or`, `::test_arith_ops`).
- A decider, arithmetic or selector with an off status (`no_power` and others in `OFF_STATUSES`) outputs nothing (`test_kc_circuits.py::test_unpowered_combinator_outputs_nothing`; `circuits.py:70`).
- Unsimulated selector modes are folded into the network's exogenous part, so a steady snapshot stays steady (`test_kc_circuits.py::test_unsimulated_selector_folded_into_exogenous`).
- Runs are deterministic and JSON-safe (`test_kc_circuits.py::test_determinism_and_json`).
- `mcp/kc/circuits.py` imports under the system `/usr/bin/python3` (`test_kc_circuits.py::test_python39_import`), stdlib only (`circuits.py:47`).
- `kc.circuits` is reachable as an attribute of `import kc` in `factorio_compute` (`tests/unit/test_compute.py::test_every_kc_module_is_reachable_as_kc_attribute`).
- The reader handles 2.0 and 2.1: 2.1-only fields (decider `else_outputs`, `input_networks`/`output_networks`) are absent on 2.0 (`test-circuits.lua` "reads 2.1 decider else-outputs and narrowed wire selections (absent on 2.0)").
- The reader stops at the entity limit and the byte budget and says so (`truncated`, then `wires_dropped`, then `signals_truncated`) (`test-circuits.lua` "stops at the entity limit and the byte budget and says so"; `circuits.lua:683-705`).

## Easy to break

- Adding a module to `mcp/kc/` without listing it in `MODULES`: unit tests that import it directly pass, while `kc.<module>` fails inside `factorio_compute` (`tests/unit/test_compute.py:163-164`).
- `game.get_entity_by_unit_number` resolves only prototypes flagged for it (not lamps, chests); the reader falls back to one surface scan for those ids (`circuits.lua:523-537`).
- An unknown type name in a `find_entities_filtered` type list raises, so `CIRCUIT_TYPES` is filtered by the loaded prototypes before use (`circuits.lua:530-533`).
- Leaving out `status` on a combinator makes the simulator treat an unpowered one as working (`test_kc_circuits.py::test_unpowered_combinator_outputs_nothing` docstring: found by the in-game test).
- Reading the same signal from both wires counts it twice; per-signal `*_networks` selection decides it (`test_kc_circuits.py::test_condition_networks`; [format, "Semantics"](../circuits-format.md#semantics-the-simulator-follows)).
- Exogenous inference assumes the snapshot is steady; a combinator-only network caught mid-oscillation gets no exogenous part so it is not distorted (`circuits.py:716-720`; `test_kc_circuits.py::test_clock_snapshot_mid_cycle_not_distorted`).
- `with_inputs` on an entity that shares its network with other sources raises; override the network instead (`circuits.py:735-740`; `test_kc_circuits.py::test_with_inputs_ambiguous_and_unknown`).
- Without `order` (Factoriopedia order, not in the snapshot) Anything outputs and select ties use sorted keys, which may differ from the game (`circuits.py:29-30`).
- Shift masking, `^` and some constant/division edge cases are documented choices not checked against the game (`circuits.py:35-37`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_kc_circuits.py` (85 tests, under 1 s measured): semantics, simulator, check, explain, recorded fixtures.
- `./scripts/test-unit.sh tests/unit/test_compute.py -k kc_module`: `kc.circuits` reachable in `factorio_compute`.
- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh` (~11 s each): `mod/kc-companion/tests/test-circuits.lua` on 2.0.77 and 2.1.24; snapshots land in `.test-data/level1/script-output/kc-circuits/` and `.test-data/level1-2.1/script-output/kc-circuits/`.
- After a reader change, copy those snapshots into `tests/unit/fixtures/circuits/` (2.1 ones with a `-2.1` suffix, as the committed files are named) by hand: no script does it (`grep -rn kc-circuits scripts/` finds nothing). Then rerun the unit tests.
- `./scripts/test-integration.sh` (~65 s): `tests/integration/test_kc_circuits_game.py`.
- `./scripts/test-app-e2e.sh -k uncoded` (real client, real model): `tests/app_e2e/test_uncoded.py::test_explains_why_a_lamp_never_lights` and `::test_finds_why_trains_leave_with_cargo` check Kira reads the network with `kc.circuits`.
- A change to the format: update [docs/circuits-format.md](../circuits-format.md), the reader, `check()` and the fixtures together.

## Decisions

- One plain-JSON format for any circuit network anywhere, not only train stations. [docs/circuits-format.md](../circuits-format.md), intro.
- Non-combinator contributions are not readable, so the simulator takes them as fixed exogenous signals (observed minus combinator output), and changing them is how a scenario is tested. [docs/circuits-format.md, "Semantics the simulator follows"](../circuits-format.md#semantics-the-simulator-follows).
- The reader runs arbitrary Lua inside `factorio_query`, with no sandbox; read-only is an agent rule. Confirmed by Michael, 2026-10-29.
- Where the format doc and the reader disagree, the code wins. Confirmed by Michael, 2026-10-29.

## Terms

- **snapshot**: one `kc.circuits` result, format v1.
- **signal key**: `"<type>/<name>[@quality]"`, e.g. `item/iron-plate@rare`.
- **network id**: the game's circuit `network_id` as a string; snapshot `networks` and `connectors` use it.
- **kind**: `decider`, `arithmetic`, `constant`, `selector`, or `other` (any non-combinator circuit entity, with `control`).
- **exogenous**: what non-combinator entities (and unsimulated combinators) put on a network (`Circuit.exogenous()`).
- **seeds**: the entities a read starts from (`entities`, `area`, or `position` + `radius`) before it follows wires.
