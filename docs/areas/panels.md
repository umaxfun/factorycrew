# Live panels

In-game windows that Kira writes once with `factorio_panel` and the mod then keeps up to date by itself, with no model turns. Every panel that works is saved as a Kiro Crew artifact, and the window's Library opens or deletes saved panels without a model. The full user-facing contract is in [SPEC.md, "Agent tools", the `factorio_panel` entry](../../SPEC.md#agent-tools-mcp-factorio). This doc covers what a contributor needs on top of it.

## Main files

- `mod/kc-companion/scripts/panels.lua`: the panel store (`storage.kc.panels`, `storage.kc.panel_order`), the refresh loop (`M.tick`), drawing in place (`slot`, `draw_block`, `cell`), link and action buttons (`M.on_click`, `M.go_to`, `M.run_action`), library answers (`M.on_library`, `M.library_tick`), and the `kc.panel` / `kc.panel_remove` / `kc.panels` / `kc.show_on_map` / `kc.fmt` helpers (`panels.lua:719-729`)
- `mod/kc-companion/scripts/gui.lua`: the "Panels:" toggle row and the Library button in Kira's window (`refresh_panels`, `gui.lua:292`), the "Panel library" frame (`refresh_library`, `gui.lua:321`), and the library clicks (`gui.lua:493-510`)
- `mod/kc-companion/scripts/protocol.lua`: `library_request` (`protocol.lua:156`), the shared query environment (`M.query_env`, `protocol.lua:188`), and the drop of `library_result` for another run (`protocol.lua:417`)
- `mod/kc-companion/control.lua`: `panels.tick` and `panels.library_tick` on `on_nth_tick(60)` (`control.lua:54-58`), click and drop-down events (`control.lua:136-146`), `library_result` from UDP (`control.lua:90-91`), and the `kc` remote interface functions the tests use: `panels`, `panel_set`, `panel_input`, `panel_remove`, `panel_collapse`, `library_request`, `library_result`, `library` (`control.lua:190-199`)
- `mcp/factorio_mcp.py`: the `factorio_panel` and `factorio_saved_panels` tool schemas (`factorio_mcp.py:142-208`), `panel_lua` / `panel` (`factorio_mcp.py:426-455`), and saving to artifacts: `library_save`, `library_list`, `library_get`, `panel_markdown`, `view_markdown` (`factorio_mcp.py:458-698`)
- `dispatcher/factorio_dispatch.py`: the library served from inside the gateway: `library_list`, `library_spec`, `library` (`factorio_dispatch.py:380-462`), started in a thread from the wake loop (`factorio_dispatch.py:560-564`)
- `bridge/factorio_bridge.py`: the `library` message put in the ask queue (`factorio_bridge.py:300-309`), no "thinking" for it (`factorio_bridge.py:455`), and `POST /library_result` (`factorio_bridge.py:559-565`)
- `agent/factorio-companion.md`, section "Live panels": Kira's rules for writing panels (`agent/factorio-companion.md:116-129`)

## Talks to

- Uses: the bridge, through `POST /query` with `_agent: true` (the panel spec travels inside one query) and `GET /health` for the current `run_id` (`factorio_mcp.py:445`, `current_run_id` at `factorio_mcp.py:501`)
- Uses: the mod's query environment, through `protocol.query_env()`: a panel body sees what a `factorio_query` sees, plus `input` and `state` (`panels.lua:36-45`, `panels.lua:62-65`)
- Uses: Kiro Crew artifacts, through the `kirocrew artifact update|save|list|show|delete` CLI. The MCP server takes the one next to the gateway's Python (the gateway starts app servers with its own interpreter), then `PATH`, then `~/.local/bin/kirocrew`, and passes it the home from `kirocrew_home` (`factorio_mcp.py:468-501`); the dispatcher takes `PATH` or `~/.local/bin/kirocrew` (`factorio_dispatch.py:428`)
- Uses: Kira's window, through `gui.refresh_all()` after every create and remove (`panels.lua:479`, `panels.lua:500`)
- Used by: Kira (agent `factorio-companion`), through the `factorio_panel` and `factorio_saved_panels` tools (`factorio_mcp.py:929-932`)
- Used by: the dispatcher, which answers `kind: "library"` asks from the bridge queue and posts `/library_result` (`factorio_dispatch.py:438-462`)

## Must stay true

- Every `kirocrew artifact` call (MCP server `_kc`, dispatcher `artifact_cli`, the status page's library probe) runs with the home directory as its cwd: the CLI reads its cwd on start, and an updated app's processes keep the deleted old install folder as theirs (`tests/unit/test_cli_cwd.py`; gamer, 2026-11-02, after 0.4.0).
- A panel body's first failure in a row and every failed button are queued as `lua_error` with the panel's code, at most 20 between sends (`mod/kc-companion/tests/test-panels.lua::queues a panel's first failure and every failed button for the developer, with the code`). See [Error and bug reports](../sentry.md).
- A panel is created, replaced or removed by one `query` that calls `kc.panel{...}` / `kc.panel_remove(...)`, and its spec reaches the game byte for byte, including `]]` and Cyrillic in the body (`tests/unit/test_panel_tool.py::test_long_string_never_closes_early`, `::test_panel_lua_carries_the_spec_as_json`, `tests/integration/test_panels_game.py::test_bad_body_is_reported_and_kept`)
- The body runs once on create and the tool returns its `view` (tables cut to 30 rows, `rows_total`) or `error` (`panels.lua:414-430`, `mod/kc-companion/tests/test-panels.lua` "runs the body once on create...")
- A panel whose body fails is kept and shows "data failed: ..." until the same `name` fixes it (`test-panels.lua` "keeps a panel whose body fails...", `test_panels_game.py::test_bad_body_is_reported_and_kept`)
- A runtime error keeps the last good view; 5 errors in a row pause the panel, and a paused panel does not refresh (`test-panels.lua` "keeps the last good view on a runtime error and pauses...")
- A panel refreshes every `every` seconds (1-600, default 2) and not before, also when nobody looks at it (`test-panels.lua` "refreshes by itself every `every` seconds", `panels.lua:555-573`, `test_panels_game.py::test_logistics_panel_live`)
- Changing an input refreshes at once; replacing the panel keeps the player's choice when it is still offered (`test-panels.lua` "passes inputs to the body...")
- A refresh updates the GUI elements in place, so the scroll position stays (`test-panels.lua` "scrolls by default and refreshes in place...")
- Collapse is per player; a collapsed panel keeps refreshing its data, and a replaced panel stays collapsed (`test-panels.lua` "collapses to the title bar...")
- A link cell is a button that opens the remote view at its position; the character does not move (`test-panels.lua` "draws link cells as buttons...")
- An action button runs the panel's `actions[name]` with `player`, `args`, `state` and `input`; an error goes to the player and to `action_error`, never stops the game; the panel refreshes after (`test-panels.lua` "runs Kira's own Lua on a button click...")
- The description goes only into the saved artifact, never into the game window (`test-panels.lua` "runs the body once on create...", SPEC.md `factorio_panel` entry)
- At most 12 panels; a 13th is refused (`panels.lua:17`, `panels.lua:438-440`)
- One artifact per panel name per run: slug `factorio-panel-<run_id>-<name>`, tags `factorio-panel` and `factorio-run-<run_id>`; a change is a new version of the same artifact (`test_panel_tool.py::test_a_working_panel_is_saved_versioned_and_restored`)
- The MCP server and the dispatcher agree on the slug prefix, the `json kc-panel` fence, the run tag, and the CLI list-line format (`test_panel_tool.py::test_dispatcher_and_mcp_agree_on_names`)
- The window's library opens and deletes only slugs from the asking run's own list (`test_panel_tool.py::test_library_lists_opens_and_deletes_only_this_runs_panels`)
- A library request starts no model turn and no "thinking" status (`test_panels_game.py::test_library_round_trip_through_the_bridge`, `tests/e2e/test_panels_e2e.py::test_window_library_opens_and_deletes_without_kira`)
- A `library_result` for another run is dropped by the mod (`test_panels_game.py::test_library_round_trip_through_the_bridge`)
- An installed app saves panels although the gateway gives its MCP server no `KIROCREW_HOME`: the home is read from the install path `<home>/apps/<app>/mcp/` (`kirocrew_home`, `factorio_mcp.py:468`; `test_panel_tool.py::test_an_installed_app_saves_without_kirocrew_home`). Up to app 0.3.4 players' panels were never saved because of this, while the e2e stands, which export `KIROCREW_HOME`, passed.
- A working panel that is in the game but has no artifact (made while installs saved none) is saved once per run by the dispatcher, see [Dispatcher](dispatcher.md) (`test_panel_tool.py::test_dispatcher_saves_panels_the_game_has_but_no_artifact`).
- Outside Kiro Crew (no `KIROCREW_HOME` and not under an `apps/` dir, or `FC_NO_ARTIFACTS` set) nothing is saved and the result says `saved.ok = false` (`test_panel_tool.py::test_outside_kiro_crew_nothing_is_saved`, `factorio_mcp.py:481`)
- A panel name with non-ASCII letters gets a short hash of the full name in its slug, so two Cyrillic names do not share one artifact; ASCII names keep their old slug (`test_panel_tool.py::test_cyrillic_panel_names_get_their_own_artifacts`).

## Easy to break

- A new field in the panel spec must be added in three places, or it is lost on the way to the game or on save and restore: the key list in `panel_lua` (`factorio_mcp.py:431`), the key list in `panel_markdown` (`factorio_mcp.py:597`), and `M.set` (`panels.lua:432`). `test_panel_tool.py::test_actions_reach_the_game_and_the_saved_artifact` covers `actions` only.
- Drawing reuses the i-th child only when its `tags.kc_k` kind matches (`slot`, `panels.lua:117-124`). A `clear()` of the body, or a kind string that changes on every refresh, rebuilds the view and throws the scroll back to the top; the in-place test catches this by comparing element indexes.
- The dispatcher reads the human `kirocrew artifact list` output with a regex (`LIST_LINE`, `factorio_dispatch.py:390`). A line that does not match is skipped (`factorio_dispatch.py:416-419`), so if the CLI's columns change, the library shows "Nothing saved yet" with no error (`gui.lua:355-356`). `test_dispatcher_and_mcp_agree_on_names` pins one real list line.
- The window's 10-second timeout only clears the pending request (`panels.lua:707-717`). A late answer still applies, because `on_library` falls back to the message's own `op` (`panels.lua:684`), so a late "open" opens the panel after the error showed.
- `actions` bodies are only compiled for syntax at create time, against an empty environment (`panels.lua:460`); a wrong global shows only on click, as `action_error`.
- Action `args` travel through GUI element tags, so a game object in `args` cannot be stored; the button gets an explanatory tooltip instead (`panels.lua:220-224`).
- A panel body runs inside a game tick on every refresh: an endless loop hangs the game and a heavy body costs every refresh (SPEC.md `factorio_panel` entry, "Errors"; footer shows the profiler time, `panels.lua:356-366`).
- Compiled bodies are cached per load in a module-local table (`compiled`, `panels.lua:22`) and rebuilt from the stored text when the source differs, so code must never keep a function in `storage`.
- Level 2 has no Kiro Crew, so saving and the dispatcher's CLI calls are covered only by the fake CLI in the unit tests and by the e2e (`test_panel_tool.py` `FAKE_CLI`, `tests/e2e/test_panels_e2e.py`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_panel_tool.py`: the query the tool builds, the artifact markdown, save/version/restore and the dispatcher's library against a stand-in `kirocrew` CLI, ~1 s for this file (measured: 10 passed, 1.2 s)
- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh`: `mod/kc-companion/tests/test-panels.lua` (store, refresh, errors, inputs, in-place drawing, collapse, links, actions, the Library frame, `kc.panel` from a query), ~11 s each
- `./scripts/test-integration.sh`: `tests/integration/test_panels_game.py` with the planted scenarios in `tests/integration/panel_scenarios.py` (science by period, logistics shortfall that drops delivered iron, bad bodies, the library round trip through the bridge with a multi-packet spec), part of the ~65 s run
- `./scripts/test-e2e.sh`: `tests/e2e/test_panels_e2e.py`, Kira builds the science and logistics panels from questions, restores a saved one, and the window's library lists, opens and deletes with no chat growing (real model, ~4 min for all of tests/e2e)
- `./scripts/check.sh` runs the unit, level 1 and level 2 steps together (~105 s)
- The app e2e (`tests/app_e2e`) has no panel test (`grep -i panel tests/app_e2e/*.py` finds nothing).
- By hand: how a panel and the Library frame look is checked only with the UI loop (README.md, "UI loop"); `tools/ui_dev.py` has no panel step, so create one with a `kc.panel{...}` query first (`grep -i panel tools/*.py` finds nothing).

## Decisions

- Panels go through one ordinary `query`: so the bridge and the protocol stay unchanged for creating panels. SPEC.md, `factorio_panel` entry.
- Panels refresh even when nobody looks: so `kc.panels()` and the tests read live data, also on a headless server. SPEC.md, `factorio_panel` entry, "Refresh".
- Panels live in `player.gui.left`: Factorio stacks frames there, so panels never overlap. SPEC.md, `factorio_panel` entry, "Window".
- No description in the game window: the player's rule; the description goes only into the artifact. SPEC.md, `factorio_panel` entry, "description".
- The dispatcher answers library requests in a thread off its loop: each CLI call takes about 2 s. SPEC.md, `factorio_panel` entry, "Library in the window" (measurements), `factorio_dispatch.py:561`.
- Panels are not sandboxed: a panel is arbitrary Lua like `factorio_query`, which has no sandbox and is read-only only by agent rule; this is accepted for a single-player game on the player's own machine. README.md, "Live panels"; confirmed by Michael 2026-10-29.
- When SPEC, README and code disagree, the code wins. Confirmed by Michael 2026-10-29.

## Terms

- **panel**: one entry in `storage.kc.panels`, keyed by `name`, drawn as frame `kc_panel:<name>`.
- **body** (data source): the panel's Lua (`lua`), run every `every` seconds; it must return a view.
- **view**: a list of blocks: a string, a table `{title?, columns?, rows, empty?}`, or a bar `{bar, max, caption?, color?}`.
- **cell**: one table entry: string, number, icon, bar, colored text, link, or action.
- **link cell**: a cell with `gps`; a button that opens the remote view there.
- **action**: a named Lua body in the panel's `actions`, run by a cell `{action = name, args?}` on click.
- **input**: a drop-down above the view; its current value reaches the body as `input.<key>`, a string.
- **state**: a table kept between refreshes of one panel, also across replacement.
- **paused**: the state after 5 failed refreshes in a row; reopening the panel or changing an input retries.
- **saved panel**: the Kiro Crew artifact of a working panel, markdown with a snapshot and the spec in a `json kc-panel` block.
- **library**: the run's saved panels (tag `factorio-run-<run_id>`), shown by the Library button and by `factorio_saved_panels`.
