# Skills

Seven pure-Python planners Kira imports inside `factorio_compute` (`import belts`, `import power`, ...): belts, mining outposts, oil fields and pipe networks, pole grids, beacons, production ratios, solar fields. Each takes plain numbers read from the game and returns ghost lists for `kc.place_ghosts` plus a `check()` that must be `[]` (each `skills/*/SKILL.md`, "Steps").

## Main files

- `skills/<name>/SKILL.md`: when to use it, the `data_lua` to read, the calls, what is not covered. Frontmatter `name`, `description` (`skills/belts/SKILL.md:1-4`).
- `skills/<name>/lib/<name>.py`: the code, one module per skill: `beacons.py` (`place`, `evaluate`, `transmitted`, `check`), `belts.py` (`route`, `check`), `mining.py` (`plan`, `check`), `pipes.py` (`join`, `compress`, `oil_field`, `check`), `power.py` (`cover`, `link`, `plan`, `check`), `production.py` (`plan`, `adjust`, `check`), `solar.py` (`ratio`, `cell`, `field`, `check`) (`grep -n '^def [a-z]' skills/*/lib/*.py`).
- `skills/<name>/SOURCE.md`: origin of the code: upstream repo, commit read, licence, what was kept and changed. Present for beacons, mining, pipes, power, production, solar; belts states its origin in its module docstring (`skills/belts/lib/belts.py:14-15`).
- `skills/<name>/LICENSE-upstream`: the upstream licence text, verbatim: beacons, pipes (MIT, Coppermine OilOutpostPlanner), mining (MIT, rimbas Mining Patch Planner), production (Apache-2.0 Kirk McDonald + MIT FactorioLab) (`skills/*/SOURCE.md`). Power and solar are written from an idea only, so they have none (`skills/power/SOURCE.md`, `skills/solar/SOURCE.md`).

## Talks to

- Uses: the `kc` primitives in `mcp/kc/`: `pipes` takes `kc.steiner` (`skills/pipes/lib/pipes.py:36-42`), `power` takes `kc.cover`, `kc.wires`, `kc.grid.Grid` (`skills/power/lib/power.py:40-49`), `production` takes `kc.lp` (`skills/production/lib/production.py:45-52`). Beacons, belts, mining and solar use only the stdlib (same grep). `mining` imports `belts` for its check (`skills/mining/lib/mining.py:480-495`).
- Used by: the MCP server's `factorio_compute`, which copies every `skills/*/lib/*.py` flat into the run's temp dir next to `main.py`, and `mcp/kc/*.py` into `kc/`, on every call (`mcp/factorio_mcp.py:295`, `:359-369`). `SKILLS` is `<parent of mcp/>/skills`, which is the repo root or the installed app's root (`mcp/factorio_mcp.py:295`).
- Used by: Kira, through her rules, which name each skill's entry call and check (`agent/factorio-companion.md`, "Skills"), and the `factorio_compute` tool description, which lists all seven (`mcp/factorio_mcp.py`, `TOOLS`). See [Kira](kira-agent.md).
- Used by: the app package: `scripts/build-app.sh:23` rsyncs all of `skills/` (minus `__pycache__`) into `dist/factorycrew/skills/`; `app/app.json` registers all seven as Kiro Crew skills.
- Feeds: [Build helpers](build-helpers.md): every skill's output goes to `kc.place_ghosts` unchanged (each `SKILL.md`, last step).

## Must stay true

- `skills/feedback` is a text skill: `SKILL.md` only, no `lib/`, a procedure around the `factorio_feedback` tool; Kira's rules carry the same steps and name it as "skill `feedback`" (`tests/unit/test_skills_listed.py::test_kiras_rules_name_every_text_skill`).
- Stdlib only, and importable on `/usr/bin/python3` (3.9.6 here), the interpreter the repo agent's MCP server runs under (`agent/factorio-companion.json`, `mcpServers.factorio.command`; `factorio_compute` runs `sys.executable -I main.py`, `mcp/factorio_mcp.py:370`). Checked with `/usr/bin/python3 -c "import sys; sys.path[:0]=['skills/<s>/lib','mcp']; import <s>"` for all seven. The unit tests run on 3.12 (`scripts/test-unit.sh`), so they do not catch 3.10+ syntax.
- No game access and no file or network access: the code runs in a seatbelt sandbox with no network, writes only in its temp dir, no reading the home dir (`mcp/factorio_mcp.py:302-313`; `tests/unit/test_compute.py::test_sandbox_blocks_network_home_and_writes`).
- `import <skill>` works inside `factorio_compute` (`tests/unit/test_compute.py::test_skill_code_is_importable_in_compute`, for `belts` only).
- A skill that needs `kc` imports it first as a top-level package (the compute dir has `kc/` next to it) and falls back to `<repo>/mcp` on `sys.path` when run from the repo (`skills/power/lib/power.py:40-46`, `skills/pipes/lib/pipes.py:36-42`). Without the fallback the unit and level 2 tests fail to import (they put only `skills/<s>/lib` on `sys.path`: `tests/unit/test_power.py:10`, `tests/integration/test_power_game.py:16`).
- Lib file names are unique across skills and differ from `kc_compute.py`, `main.py`, `data.json`, `result.json`: the copy is flat, so a second file of the same name overwrites the first (`mcp/factorio_mcp.py:361-364`).
- Prototype names are read in `data_lua`, never hardcoded (mods such as Nullius rename entities) (each `SKILL.md`, step 1).
- Results are deterministic: ties are broken on position (`skills/beacons/SOURCE.md`, "Ties"; `skills/mining/SOURCE.md`, "What was changed"; `skills/pipes/SOURCE.md`).
- `mcp/kc/lp.py:29` and `mcp/kc/steiner.py:23` point at `skills/production/LICENSE-upstream` and `skills/pipes/LICENSE-upstream` for their own upstream licence: those files stay even if the skill is removed.

## Easy to break

- A new skill reaches Kira only if her rules, the `factorio_compute` description and `app/app.json` name it: `factorio_compute` ships every lib, but Kira has no file tools and reads no `SKILL.md` (`agent/factorio-companion.json`). `tests/unit/test_skills_listed.py` fails when one of the three misses a skill.
- An edit to a skill reaches the stand at the next `factorio_compute` call, with no restart, because the files are copied per call (`scripts/stand.sh:161-163`, `mcp/factorio_mcp.py:359`). An installed app keeps its own copy until `scripts/app-dev.sh` rebuilds and reinstalls it (`scripts/app-dev.sh`).
- A skill that imports another skill (`mining` -> `belts`) works in compute because the libs sit in one flat dir, and in the repo only through its own `sys.path` fallback (`skills/mining/lib/mining.py:480-495`).
- Results over 64 KB come back as `result_too_large` (`mcp/factorio_mcp.py:410-412`); a big field or outpost needs a compact result.
- Porting code from an upstream with no usable licence: only the idea may be taken (`skills/power/SOURCE.md`, `skills/solar/SOURCE.md`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_beacons.py tests/unit/test_belts.py tests/unit/test_mining.py tests/unit/test_pipes.py tests/unit/test_power.py tests/unit/test_production.py tests/unit/test_solar.py`: pure Python, 110 tests, ~5 s (run 2026-10-29). Add `tests/unit/test_compute.py` for the import-in-compute path.
- `./scripts/test-integration.sh` (~65 s): `tests/integration/test_{beacons,belts,mining,pipes,power,production,solar}_game.py`, one or two tests each. Each reads real prototypes on headless 2.1.24, plans in Python, places with `kc.place_ghosts`, revives the ghosts and checks the game agrees: an item crosses the routed belt, ore reaches the exit belt, pumpjacks share the sink's fluid segment, lamps and poles join the grid network, beacon counts and `effects` match, planned craft rates match, the solar field is one network (each file's docstring).
- `./scripts/test-app-e2e.sh -k uncoded` (real model): `tests/app_e2e/test_uncoded.py` makes Kira compose `kc` and `belts` herself; `test_moves_items_by_belt_around_a_wall` and `test_powers_scattered_lamps_from_the_grid` check the built result in the game.
- By hand after a change to a lib's imports: `/usr/bin/python3 -c "import sys; sys.path[:0]=['skills/<s>/lib','mcp']; import <s>"`.

## Decisions

- Power and solar are written from scratch: their upstreams have no usable licence, so only the idea was taken. `skills/power/SOURCE.md`, `skills/solar/SOURCE.md`, `docs/research/01-outposts.md`.
- Beacons default `min_utility` is 0.5, not upstream's 2: with the 2.0 `1/sqrt(n)` profile, 2 places almost nothing around assembler rows. `skills/beacons/SOURCE.md`, "What changed".
- Pipe and belt tier, pole, drill and beacon names come from prototypes: Nullius and other mods rename them. Each `SKILL.md`, step 1.

## Terms

- **Skill**: a folder `skills/<name>/` with `SKILL.md` (how Kira uses it) and `lib/<name>.py` (the importable module).
- **`kc` primitives**: the general library in `mcp/kc/` that skills build on (`mcp/kc/__init__.py:1-19`).
- **`check()`**: each skill's validator; it re-derives the result from geometry and must return `[]` before placing.
- **`data_lua`**: the Lua body `factorio_compute` runs in the game first; its return value is `data` in Python ([SPEC.md, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio)).
