# Atlas

The base as a graph for Kira: the mod streams the structure of a surface, the bridge keeps it per surface, and the MCP server turns it into blocks, zones and what feeds what, answering `factorio_atlas` at map, zone, block and trace level with statuses read live. Design, tasks and measurements are in [docs/atlas.md](../atlas.md); this page covers the code.

## Main files

- `mod/kc-companion/scripts/atlas.lua`: the structure dump, paced over ticks (`M.step`), the dump format in its header comment, and the dirty-chunk set in `storage` (`M.mark`, `M.take_dirty`, `M.dirty_count`).
- `mod/kc-companion/control.lua`: the events that mark chunks dirty (lines 60-74), `on_tick` stepping the dump (77-80), `mark_all` on `on_configuration_changed` (31), remote `kc.atlas_dump` that writes a whole dump to `script-output` (181-188).
- `mod/kc-companion/scripts/protocol.lua`: the `atlas` request (425-429); `atlas_version` and `atlas_dirty` in the heartbeat (394-395).
- `bridge/factorio_bridge.py`, atlas parts only: `AtlasStore` (139-213) merges full and partial dumps by chunk; `Bridge.atlas` (374-409) decides full / partial / cached; `atlas_prefetch` (411-420); `atlas_part` collection and the `atlas_incomplete` check (310-314, 351-367); `POST /atlas` (588-599). The rest of the bridge is in [Bridge](bridge.md).
- `mcp/kc/atlas.py`: `parse`, then `Atlas` builds belts, blocks, stops, links, edges, zones; belt lanes (`_lanes`, `belt_supply`); views `map_view`, `zone_view`, `block_view`, `trace`; `resolve_item`.
- `mcp/factorio_mcp.py`: the `factorio_atlas` schema (211-238), `ATLAS_CACHE` and `_atlas` (508, 702-732), `_live_status` with `STATUS_LUA` (511-527, 735-757), `atlas_tool` (760-841), `trace_view` with `TRAINS_LUA` (842-924).
- `scripts/atlas-fixture.sh`: loads `FC_ATLAS_SAVE` with `FC_ATLAS_MODS` in headless 2.0.77 (Docker, RCON) and writes the dump (`dump`), or keeps the game up (`up`, `lua`, `down`).
- `tests/data/atlas/dump.txt.gz`: the committed fixture dump of a player's Nullius save, read by the unit tests ([docs/atlas.md, Fixture](../atlas.md#fixture)).
- `tools/atlas_proto/`: the throwaway prototype (JSON dump over RCON, benchmarks). Nothing outside it imports it; `tests/unit/test_kc_atlas.py:162` names `dump.lua` for the optional reference read (`git grep atlas_proto`).

## Talks to

- Uses: Mod ↔ bridge protocol, through `{type="atlas", surface, chunks?, budget?, send_bytes?, max_chunks?}`, answered by `atlas_part` messages `{id, seq, text}` and a closing `result` with `data.parts` (`mod/kc-companion/scripts/atlas.lua:1-8`, [SPEC, Agent tools](../../SPEC.md#agent-tools-mcp-factorio)).
- Uses: the heartbeat's `atlas_version` (bumped on every marking event, `control.lua:66`) and `atlas_dirty` (dirty chunks on `nauvis`, -1 = everything) to decide what to re-read (`bridge/factorio_bridge.py:284-285, 380-388`).
- Uses: [Bridge](bridge.md), through `POST /atlas` with `surface`, `refresh`, `have_version`, or `raw` to bypass the store (`bridge/factorio_bridge.py:588-599`); `GET /health` for `run_id` and `tick` (`mcp/factorio_mcp.py:706`); `POST /query` for live statuses and trains (`mcp/factorio_mcp.py:746, 911`).
- Used by: Kira, through the `factorio_atlas` tool and the rules in `agent/factorio-companion.md` "The base: factorio_atlas".
- Used by: `factorio_compute`, which offers the same module as `kc.atlas` (`mcp/kc/__init__.py:15, 32`).
- Used by: the app status page and tests, through `atlas_version`, `atlas_dirty`, `atlas_cached` in `/health` (`bridge/factorio_bridge.py:530-531`).

## Must stay true

- The dump never calls `helpers.table_to_json` (`tests/unit/test_kc_atlas.py::test_the_dump_never_builds_json`).
- The dump holds structure only. Statuses, belt contents and trains are read live on every call (`mcp/factorio_mcp.py:735-757, 842-924`).
- A dump never raises out of `on_tick`: `M.step` wraps the work in `pcall`, answers `ok=false, error="atlas_failed"` and puts the taken dirty chunks back (`mod/kc-companion/scripts/atlas.lua:325-336`; no test plants a failure).
- One dump at a time: the mod answers `atlas_busy` (`atlas.lua:242`, `protocol.lua:428`), and the bridge holds `atlas_lock` around every dump, raw ones included (`bridge/factorio_bridge.py:375, 596`).
- A cut dump is refused, never served: the bridge checks part numbers 1..`parts` (`atlas_incomplete`, `bridge/factorio_bridge.py:363-367`), and `parse` checks the `Z` footer count (`tests/unit/test_kc_atlas.py::test_parse_refuses_a_cut_dump`).
- A marked entity marks every chunk its box touches; a partial dump reads only dirty chunks; mod changes make the next dump full (`mod/kc-companion/tests/test-atlas.lua`, `tests/integration/test_atlas_game.py::test_only_the_changed_chunks_are_read_again`).
- With nothing changed, the second call answers from the cache in under 200 ms, statuses included (`tests/integration/test_atlas_game.py::test_second_call_is_fast_and_a_removal_is_seen`).
- Fluids move only on pipe edges, items never on pipe edges; a belt shared by two blocks joins them only when one's product is the other's ingredient (`tests/unit/test_kc_atlas.py::test_fluids_only_by_pipe_items_never_by_pipe`, `::test_shared_belt_does_not_join_unrelated_blocks`).
- A block gets an item from a logistic network only when it does not get it physically (`tests/unit/test_kc_atlas.py::test_no_network_edge_for_what_a_block_gets_by_belt`).
- A full dump on the real client keeps UPS at 50 or above (`tests/app_e2e/test_atlas.py::test_ups_during_a_full_dump`).
- The unfiltered map of the fixture stays under 3.5k tokens (`tests/unit/test_kc_atlas.py::test_map_unfiltered_fits_in_a_turn`).

## Easy to break

- The dump format has three readers: `atlas.lua` writes it, `AtlasStore.apply`/`text` re-emits it, `parse` reads it. The store keeps only `H N E R T C` lines and writes a new `Z` footer, so a new record type added in the mod silently disappears on its way to the MCP server (`bridge/factorio_bridge.py:160-213`).
- Turning numbers into text with `..` or `table.concat` in the dump loop costs ~6 µs per number and makes the dump several times slower; numbers go through `string.format` (`atlas.lua:158-160`, [docs/atlas.md, Task 1 measurements](../atlas.md#1-one-zone-end-to-end)).
- Raising per-tick limits (`M.BUDGET`, `M.MAX_CHUNKS`, `M.SEND_BYTES`, `atlas.lua:33-40`) drops UPS on the real client; only `test_ups_during_a_full_dump` shows it, no fast test does (`tests/app_e2e/test_atlas.py`).
- Reading a property that a 2.1 entity lacks raises (`e.fluidbox`); such reads sit in `pcall` (`atlas.lua:96, 109`).
- A machine without a recipe (or a drill without a target) forms no block, so a test that builds machines must set recipes (`mcp/kc/atlas.py:261-268`).
- Indexing Nullius' script helper entities (`UNINDEXED`) takes most of the build time; `test_build_time` allows 20 s and would not catch it (`mcp/kc/atlas.py:33-35`, `tests/unit/test_kc_atlas.py::test_build_time`).
- `B`, `Z`, `S` numbers are stable for one dump only; any rebuild may renumber them, so an id from an earlier answer can point elsewhere (`mcp/kc/atlas.py:15-16`).
- The mod files an entity under the chunk holding its exact position, the bridge under its rounded doubled coordinates // 64 (`atlas.lua:402`, `bridge/factorio_bridge.py:166`). (inferred: an off-grid entity within a quarter tile of a chunk border can land in the neighbouring chunk and be duplicated after a partial update)
- Loading an earlier save of the same run leaves the bridge's store newer than the game: a partial re-read only happens when the game tick is past the store's tick (`bridge/factorio_bridge.py:387`), and loading marks nothing dirty. (inferred: until `refresh=true`, answers come from the later timeline)
- A dump in progress is module state and is lost on save/load; the bridge then times out after `ATLAS_TIMEOUT` (`atlas.lua:55`, `bridge/factorio_bridge.py:26`). (inferred: the dirty chunks it took are lost if the game saves mid-dump)
- Live statuses cover at most `STATUS_LIMIT` = 20 000 machines; the rest read `?` (`mcp/factorio_mcp.py:510, 738`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_kc_atlas.py`: parse, blocks, zones, edges, lanes, trace, map, the MCP tool on a fake bridge, all on the fixture dump; ~4 s for this file (27 passed, 1 skipped without `FC_ATLAS_REFERENCE`). `FC_ATLAS_OUT=<dump>` runs it on a fresh dump.
- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh`: `mod/kc-companion/tests/test-atlas.lua` (dirty set in `storage`, border entities, partial dump, `mark_all`), ~11 s each.
- `./scripts/test-integration.sh -k atlas`: `tests/integration/test_atlas_game.py`, a small chain through the real mod, UDP, bridge and MCP on headless 2.1.23 (blocks and direct feed, re-read after a build, one chunk read again, 200 ms second call, removal seen).
- `./scripts/test-app-e2e.sh -k atlas`: real 2.0.77 client, ~20 min for the whole suite. `tests/app_e2e/test_atlas.py` needs the fixture save (`FC_UI_SAVE`, `FC_UI_MODS`, `tools/ui_dev.py:119-167`) and checks UPS and Kira's answers on it; `-k events` runs `tests/app_e2e/test_atlas_events.py` (rotate, paste, recipe in the machine window, inventory); `-k planted` runs `tests/app_e2e/test_atlas_planted.py` (planted faults A, B, C with controls, built on the plain dev map).
- After a change to the dump format or the mod's dump code: `FC_ATLAS_SAVE=... FC_ATLAS_MODS=... scripts/atlas-fixture.sh dump`, then re-run the unit tests with `FC_ATLAS_OUT` on that dump; replace `tests/data/atlas/dump.txt.gz` only when the format changed.
- By hand after touching the per-tick limits: the UPS figures `test_ups_during_a_full_dump` prints; `FC_ATLAS_UPS_SWEEP=1` adds the sweep (`tests/app_e2e/test_atlas.py::test_ups_sweep`).

## Decisions

- The dump is hand-built text, not JSON: `table_to_json` was the only slow phase, 14 times slower for the same data. [docs/atlas.md, Reading the base](../atlas.md#reading-the-base-without-stalling-the-game)
- Structure is cached and updated by dirty chunk; state is never cached and is read live: structure changes only when the player builds, state every tick. [docs/atlas.md, Reading the base](../atlas.md#reading-the-base-without-stalling-the-game)
- One tool with flat parameters: weak models handle it better than composing Python. [docs/atlas.md, Tool](../atlas.md#tool)
- Per-tick limits of 3 chunk lookups, 150 cost units, 4000 bytes: looking at chunks is what costs frames, and sending ~500 KB/s stopped the client answering. [docs/atlas.md, Task 1 measurements](../atlas.md#1-one-zone-end-to-end)
- Planted-fault cases are builds the test makes on the dev map, not committed saves: reviewable in the diff, no binary, no mods, each setup checked against the game first. [docs/atlas.md, task 5](../atlas.md#5-selection-versus-block-on-synthetic-saves)
- The code wins when SPEC, README and code disagree: confirmed by Michael, 2026-10-29.

## Terms

- **block**, **zone**, **edge**, **logistic network**, **feed mode**: as defined in [docs/atlas.md, Levels](../atlas.md#levels); `BLOCK_GAP` = 6, `ZONE_GAP` = 5 (`mcp/kc/atlas.py:25-26`).
- **structure** / **state**: what stands and how it is set up (dumped, cached) versus what it is doing now (read live) (`atlas.lua:8-9`).
- **dirty chunk**: a 32×32 chunk touched by a build, removal, rotation, flip, settings paste or machine GUI close since the last dump (`control.lua:60-74`).
- **full / partial dump**: every chunk with player entities, or only the dirty ones, the latter listing them as `C|cx|cy` lines (`atlas.lua:250-262`).
- **cost unit**: the per-tick budget's unit; a plain entity is 1, a crafting machine or drill 5, a pump 4 (`atlas.lua:52-54`).
- **node ids**: `B<n>` block, `Z<n>` zone, `S<n>` train stop, `N<id>` logistic network, `W:<fluid>` offshore pump (`mcp/kc/atlas.py:15`).
- **stop items**: the items a train stop handles, read from `[item=...]`/`[fluid=...]` tags in its name (`mcp/kc/atlas.py:296-297`).
