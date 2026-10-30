# Mod core

The game side of the system: the `kc-companion` mod's event wiring, the run, tabs and feed in `storage`, query execution, map marks, and the UDP protocol to the bridge. The wire format is specified in [SPEC.md, "Mod ↔ bridge protocol"](../../SPEC.md#mod--bridge-protocol); this doc covers how the mod implements it.

## Main files

- `mod/kc-companion/control.lua`: every event handler, the UDP probe and send, the `remote` interface `kc` used by tests and the UI loop, and the list of level 1 test modules (`mod/kc-companion/control.lua:153`, `:207`)
- `mod/kc-companion/scripts/protocol.lua`: the protocol constants, `storage.kc` layout (`init_storage`), tabs and feed, `ask`, `run_query` and its environment, `mark`/`unmark`/`list_marks`, framing (`encode`/`decode`), and `handle` for each bridge message (`mod/kc-companion/scripts/protocol.lua:10-18`, `:22`, `:413`)
- `mod/kc-companion/info.json`: mod name, version, `factorio_version: "2.0"`, dependency `base >= 2.0.0` (`mod/kc-companion/info.json`)
- `mod/kc-companion/tests/test-protocol.lua`, `mod/kc-companion/tests/test-marks.lua`: level 1 tests for this area

## Talks to

- Uses: [Bridge](bridge.md), through `helpers.send_udp(protocol.BRIDGE_PORT, …)` and `on_udp_packet_received` (`mod/kc-companion/control.lua:11-16`, `:86`)
- Uses: [Window](window.md), through `gui.refresh_all`, `gui.tick`, `gui.on_click`, `gui.on_confirmed`, `gui.on_pointer` (`mod/kc-companion/control.lua:54-58`, `:96-99`, `:138-151`)
- Uses: [Build helpers](build-helpers.md) (`mod/kc-companion/scripts/helpers.lua`), exposed as `kc` in the query environment (`mod/kc-companion/scripts/protocol.lua:4`, `:185`)
- Uses: [Atlas](atlas.md) (`mod/kc-companion/scripts/atlas.lua`): `atlas.start` on an `atlas` message, `atlas.step` on every tick, `atlas.mark` on build/remove events, `atlas_version` and `atlas_dirty` in the heartbeat (`mod/kc-companion/scripts/protocol.lua:425-429`, `:386-397`; `mod/kc-companion/control.lua:60-80`); design in [docs/atlas.md](../atlas.md)
- Uses: [Live panels](panels.md) (`mod/kc-companion/scripts/panels.lua`): `panels.tick` every 60 ticks, `library_result` routed to `panels.on_library`; panels reuse `protocol.query_env` (`mod/kc-companion/control.lua:54-58`, `:89-90`; `mod/kc-companion/scripts/protocol.lua:188`)
- Used by: level 2 and e2e tests over RCON, through `remote.call("kc", "ask" | "state" | …)` (`tests/integration/conftest.py:212-215`, `mod/kc-companion/control.lua:153`)
- Used by: `tools/ui_dev.py`, which rewrites the `M.BRIDGE_PORT = 34199` line on deploy and drives the window through `ui_open`/`ui_click`/`ui_confirm`/`ui_inject` (`tools/ui_dev.py:148`, `:232-263`)

## Must stay true

- The bridge's port is the literal line `M.BRIDGE_PORT = 34199` in protocol.lua; the dev stand finds and replaces that exact text (`tools/ui_dev.py:148`) and the mod digest skips it (`tools/ui_dev.py:106`)
- `run_id` is minted once per map and never changes on later loads (`mod/kc-companion/tests/test-protocol.lua::run_id is minted once and stays stable`)
- `run_query` never raises: it returns `{ok=false, error=bad_request|no_such_surface|compile_error|runtime_error|not_serializable|result_too_large}` (`mod/kc-companion/scripts/protocol.lua:191-215`; `mod/kc-companion/tests/test-protocol.lua::run_query`)
- A result over 64 KB of JSON is refused, not cut (`mod/kc-companion/tests/test-protocol.lua::refuses a result over 64 KB`)
- A `result` carries the `id` of the request it answers (`mod/kc-companion/tests/test-protocol.lua::answers a query with a result carrying the same id`; `tests/integration/test_bridge.py::test_query_errors_and_large_results`)
- Mark text starts with `[KC] `, and `unmark` only touches tags the mod placed; the player's own tags stay (`mod/kc-companion/tests/test-marks.lua::removes all of a chat, or all, and leaves player tags alone`)
- `unmark` by `ids` removes only ids of the named chat (`mod/kc-companion/tests/test-marks.lua::removes by ids only within the chat`)
- A tag deleted on the map, with or without `on_chart_tag_removed`, disappears from `list_marks` and from `storage` (`mod/kc-companion/tests/test-marks.lua::forgets a tag the player deleted on the map`)
- A `reply`, `status`, `trace` or `library_result` with another `run_id` is dropped (`mod/kc-companion/scripts/protocol.lua:417`; `mod/kc-companion/tests/test-marks.lua::ignores a reply meant for another run`)
- The feed keeps at most 30 entries per tab and the trace at most 12 (`mod/kc-companion/tests/test-marks.lua::keeps the feed at FEED_LIMIT`; `mod/kc-companion/scripts/protocol.lua:17`)
- A large message splits into parts and reassembles in any order, without splitting a UTF-8 sequence (`mod/kc-companion/tests/test-protocol.lua::splits and reassembles a large message, with quotes to escape`; `mod/kc-companion/tests/test-marks.lua::does not split UTF-8 sequences across parts`; `tests/integration/test_bridge.py::test_large_reply_reaches_the_tab`)
- `add_chart_tag` places a tag on an uncharted chunk, which the mark design relies on (`mod/kc-companion/tests/test-protocol.lua::can be placed on an uncharted chunk`)
- Without `--enable-lua-udp` the mod stays quiet: one failed `recv_udp` probe sets `udp_state = "off"` and nothing is sent after it (`mod/kc-companion/control.lua:9-16`, `:39-48`)
- Neither side checks the `v` field: `decode` and the bridge's reassembler ignore it (`mod/kc-companion/scripts/protocol.lua:364-381`; `grep '"v"' bridge/factorio_bridge.py`)

## Easy to break

- A new `tests/test-*.lua` file that is not added to the list in `control.lua:208` never runs, and level 1 stays green without it (`mod/kc-companion/control.lua:207-208`)
- A new bridge→game message that changes the window must be added to `REFRESH`; otherwise the window shows the change only after the next unrelated refresh (`mod/kc-companion/control.lua:83-99`)
- New fields in `storage.kc` are created by `init_storage`, which runs only from `on_init` and `on_configuration_changed` (`mod/kc-companion/control.lua:23-31`). Code that reads a new field on an existing save needs an `or {}` guard, as `kc.ui` and `kc.library` have (`mod/kc-companion/scripts/gui.lua:17`, `mod/kc-companion/scripts/protocol.lua:158`). A `control.lua`-only change does not fire `on_configuration_changed` (inferred: Factorio fires it on mod version or prototype changes)
- Anything left out of `query_env` is invisible to queries and to panel bodies; `load`, `require`, `setmetatable` and `rawget` are not in it (`mod/kc-companion/scripts/protocol.lua:168-186`)
- `query_env` takes `game.players[1]` as `player`, so the default surface is wrong in multiplayer (`mod/kc-companion/scripts/protocol.lua:169`; multiplayer is out of v1, [SPEC.md, "Not in v1"](../../SPEC.md#not-in-v1))
- A query with an endless loop hangs the game: Factorio Lua has no instruction limit ([SPEC.md, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio))
- A mark `icon` is always looked up as an item; a fluid or signal name fails that mark with `reason = "error"` (`mod/kc-companion/scripts/protocol.lua:254`; `mod/kc-companion/tests/test-marks.lua::reports what could not be placed and why`)
- An empty Lua table reaches the bridge as `{}`, not `[]`; list fields that may be empty are left out (`mod/kc-companion/scripts/pointers.lua:99-101`) or fixed up by the bridge ([SPEC.md, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio))
- Partial messages live in a module-local table, not in `storage`; a part lost in flight leaves its slices there until the save is reloaded (`mod/kc-companion/scripts/protocol.lua:360-381`). The leak is a few KB per lost message (inferred: one entry per incomplete `id`, at most its received slices)
- `info.json`'s `version` is overwritten with the app's version by `scripts/build-app.sh:44-50`, so bumping it by hand changes nothing for players
- The 2.1 level 1 run rewrites `"factorio_version": "2.0"` with a regex; a different spelling of that line is not rewritten, and the script's `grep` prints which version each copy ended up with (`scripts/test-mod-2.1.sh:23-26`)

## How to check a change

- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh`: level 1 on headless 2.0.77 and 2.1.23, all `mod/kc-companion/tests/*.lua`, ~11 s each. This area: `mod/kc-companion/tests/test-protocol.lua`, `mod/kc-companion/tests/test-marks.lua`
- `./scripts/test-integration.sh`: level 2 (~65 s), for any protocol or framing change. Covers handshake, ack and thinking, marks via MCP, large Cyrillic replies, packet size, idle by timeout, `game_offline`, pong loss (`tests/integration/test_bridge.py`)
- `./scripts/check.sh`: unit, both level 1 runs and level 2 (~105 s)
- `./scripts/test-e2e.sh` (~4 min) and `./scripts/test-app-e2e.sh` (~20 min) when the ask/reply path changes (`tests/e2e/test_e2e.py`, `tests/app_e2e/test_app_e2e.py`)
- No Python unit test covers this area
- By hand: save and reload with marks on the map; the run, tabs, feed and marks should be back ([SPEC.md, "v1 done criteria"](../../SPEC.md#v1-done-criteria))

## Decisions

- The code wins when SPEC, README and the code disagree: confirmed by Michael, 2026-10-29
- Queries run arbitrary Lua through `load` with their own environment, with no sandbox and no fallback to primitives; read-only is an agent rule: [SPEC.md, "Decided", "`load` in the mod runtime"](../../SPEC.md#decided) and "Agent tools"; confirmed by Michael, 2026-10-29
- A run is a `run_id` in `storage`, named by the player: the Lua API has no save name. [SPEC.md, "Decided", "Save name in the Lua API"](../../SPEC.md#decided) and "What a run is"
- The mod declares `factorio_version: "2.0"`; level 1 runs on 2.0.77 and 2.1.23: the player's game is 2.0.77 and headless 2.0 crashes on UDP. [SPEC.md, "Decided", "Game version"](../../SPEC.md#decided)
- The mod slices 4096 bytes of source JSON per part: the bridge handles the ~8 KB packets this makes, while the game's own receive buffer starts at 4 KB. [SPEC.md, "Mod ↔ bridge protocol"](../../SPEC.md#mod--bridge-protocol)
- Marks may go on uncharted territory: `add_chart_tag` accepts it. [SPEC.md, "Decided", "Marks on uncharted territory"](../../SPEC.md#decided)

## Terms

- **run**: one map, identified by `storage.kc.run_id`; "Save as" keeps it, a new map gets a new one
- **tab / chat**: one name in `storage.kc.tab_order`; the same string is the `chat` field on the wire and one Kiro Crew chat per run
- **feed**: a tab's last 30 entries, `from = player | kira | trace_summary`
- **trace**: the live steps of a running turn (`trace` messages), folded into one `trace_summary` entry on `reply`
- **mark**: a chart tag the mod placed, text prefixed `[KC] `, remembered in `storage.kc.marks[chat][tag_number]`
- **part**: a `{type="part", id, part, parts, data}` slice of a message too large for one packet
- **udp_state**: `unknown` until the first `recv_udp` probe, then `on` or `off`
