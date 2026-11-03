# Bridge

The bridge is the only process that talks UDP to the game. It gives the dispatcher and the MCP server an HTTP API on 127.0.0.1, matches requests to results by `id`, and holds the ask queue, the reply queue and the per-tab "thinking" timers. The chain and the wire protocol are in [SPEC.md "How it works"](../../SPEC.md#how-it-works) and ["Mod ↔ bridge protocol"](../../SPEC.md#mod--bridge-protocol); this doc adds what a change to the code needs.

## Main files

- `bridge/factorio_bridge.py`: the whole bridge, stdlib only, one asyncio loop (`bridge/factorio_bridge.py:1-9`).
  - `encode`, `Reassembler`: UDP framing into `part` packets and reassembly by `id` (`:77`, `:105`).
  - `Bridge.on_game_message`: `hello`/`heartbeat`/`ask`/`library`/`stop`/`atlas_part`/`lua_error`/`result` from the game.
  - `report`, `report_exception`, `Bridge.report_lua`, `RECENT`, `bug_report_files`, `Bridge.bug_fallback`: error reports, the last log lines, and a window's bug report sent by the bridge when no dispatcher takes it in 15 s; see [Error and bug reports](../sentry.md).
  - `request`: one call to the game, waits for its `result` (`:344`).
  - `take_asks`, `confirm_asks`, `requeue_expired`: the ask queue and its lease (`:424-460`).
  - `set_thinking`, `activity`, `idle_timeout`, `end_turn`, `turn_end`, `stopped`, `reply`: the per-tab status and the idle clock (20 min, a backstop) (`:462-524`).
  - `health`, `route`, `handle_http`: `/health` and the HTTP routes (`:526-649`).
  - `AtlasStore`, `atlas`, `atlas_prefetch`: the atlas cache, see [Atlas](atlas.md).
- `Settings` (`:41-51`) and `main` (`:669`): ports and timeouts, all settable from the command line.

HTTP routes (`bridge/factorio_bridge.py:539-613`):

| Route | Caller | What it does |
|---|---|---|
| `GET /health` | MCP, app status page, tests | game link, run, queue sizes, `dispatcher_seen_s`, `agent_seen_s`, atlas versions |
| `GET /asks?wait=&lease=` | dispatcher | long-poll (wait capped at 60 s), leases every queued ask, sets `thinking` |
| `POST /asks/done {ids}` | dispatcher | confirms leased asks |
| `POST /reply {chat, text, fallback?, ask_id?}` | MCP `factorio_reply`, dispatcher | answer into a tab; ends the turn's status |
| `POST /trace`, `/progress`, `/alive` | dispatcher, MCP | signs of life of a running turn; restart the idle clock |
| `POST /query`, `/mark`, `/unmark`, `/list_marks` | MCP | forwarded to the game, answer waited for |
| `POST /atlas` | MCP `factorio_atlas` | see [Atlas](atlas.md) |
| `POST /library_result` | dispatcher | panel library answer to the game, see [Panels](panels.md) |
| `POST /bug_report_result` | dispatcher | a bug report's id (or error) to the game, see [Error and bug reports](../sentry.md) |
| `GET /recent_log` | MCP server | the bridge's last 400 log lines, for `factorio_feedback` |
| `POST /turn_end {chat, ask_id?}` | dispatcher | the turn ended in the transcript with no answer to the game: `idle`, `no_reply` |
| `POST /stopped {chat, run_id, ok, error?}` | dispatcher | the window's Stop was carried out (`idle`, `stopped`) or failed (`thinking`, `stop_failed`) |

## Talks to

- Uses: [Mod core](mod-core.md), through UDP JSON packets: it listens on 34199 and sends to the game's `--enable-lua-udp` port 34200 (`bridge/factorio_bridge.py:44-45`, `mod/kc-companion/scripts/protocol.lua:10`).
- Used by: [Dispatcher](dispatcher.md), through `GET /asks`, `POST /asks/done`, `/reply`, `/trace`, `/library_result` (`dispatcher/factorio_dispatch.py:554`, `:495`, `:325`, `:376`, `:458`).
- Used by: [MCP server](mcp-server.md), through `bridge_post` with `_agent: true` on every tool call, and `/health` (`mcp/factorio_mcp.py:265`, `:530`, `:938-939`).
- Used by: [App](app.md) backend, which runs the bridge in its own process with `fb.serve` on the ports in `<app>/data/config.json`, and whose status page reads `/health` (`app/backend/server.py:119-126`, `app/backend/status.py:64`).
- Used by: [Dev stand](dev-stand.md) tooling: `tools/ui_dev.py` starts a dev bridge (`tools/ui_dev.py:189`), `scripts/services.sh` installs it as launchd agent `dev.factorycrew.bridge` (`scripts/services.sh:60`), level 2 and the e2e stand start it from `tests/integration/conftest.py:195-198`.

## Turn contract

How a game turn ends, since 0.3.12. Before, the dispatcher learnt it by reading Kiro Crew's chat transcript, which lost answers three ways on 2026-11-01: a turn that ended under the next dispatcher wake, a bridge restart, and the gateway rewriting the transcript file.

- Kira's agent spec carries three hooks that Kiro Crew runs on every turn of hers: `userPromptSubmit`, `postToolUse` and `stop`, each the command `app/hooks/turn_hook.py` (in the app `$KIROCREW_HOME/apps/factorycrew/hooks/turn_hook.py`, on a dev stand the checkout's with `--bridge`). It posts the event to `POST /hook` and, on Stop, prints the bridge's decision.
- `UserPromptSubmit` whose prompt starts with the dispatcher's header binds the chat slot (`session_key`) to its tab. When the header's `tick` is the tab's current question, the turn becomes **hooked**: from then on its end is the Stop hook's to decide. Events of a slot whose current question is not hooked yet are ignored: the previous turn's Stop arrives about 2 s after its answer, when a quick follow-up may already be thinking.
- `PostToolUse` of a hooked turn is a sign of life and a trace line (`TOOL_LABELS`); the transcript's tool lines are dropped for it.
- `Stop` of a hooked turn that is still working (no `factorio_reply` ended it) answers `{"decision": "block", "reason": REPLY_NUDGE}` once: Kiro Crew starts a continuation turn telling Kira to call `factorio_reply` now. At the next Stop with still no answer, the bridge sends the turn's last text to the tab itself, or ends it with `no_reply` if there is none. So an answer that exists reaches the game.
- For a hooked turn the dispatcher's fallback copy and `/turn_end` are ignored, unless they carry `failure: true`: Kiro Crew does not fire Stop for a turn it killed (an error row: credits spent, model refused, turn limit), so that path stays on the transcript. A turn that is never hooked (an older Kiro Crew, a hook that does not run) keeps the transcript path as before.
- Checked: `tests/unit/test_turn_contract.py`, `tests/integration/test_bridge.py::test_the_stop_hook_sends_a_turn_without_factorio_reply_back_then_its_text_to_the_tab`, `tests/e2e/test_turn_contract_e2e.py` (a turn whose `factorio_reply` never reaches the game: the e2e stand's bridge runs with `--drop-first-reply NoReply`, a test-only flag that answers that tab's first reply "delivered" and drops it, `tests/unit/test_turn_contract.py::test_the_planted_fault_drops_only_the_first_reply_of_its_tab`; a dispatcher restart mid-turn). Measured on Kiro Crew 2026-11-02 (spike): app-spec hooks fire on the default KAS backend with `session_key`, `prompt`, `tool_name`, `assistant_text`; a block's reason arrives as an `inject` row "[Hook continuation — automatic]"; Stop fires only on the success path (`kiro_crew/dashboard/chat_runner.py`).

## Must stay true

- `pong` lists `working`, the chats with a live turn, queued or leased question (`Bridge.live_chats`); a new bridge lists none, so the mod drops the tabs a restart orphaned (`tests/integration/test_bridge.py::test_a_turn_lost_in_a_bridge_restart_stops_thinking`).
- Every UDP packet to the game is at most `PACKET_MAX` = 4000 bytes, escapes included, and reassembles to the original message (`tests/integration/test_bridge.py::test_bridge_packets_fit_the_games_initial_buffer`).
- Large messages both ways survive chunking with Cyrillic text (`tests/integration/test_bridge.py::test_query_errors_and_large_results`, `::test_large_reply_reaches_the_tab`).
- The HTTP API binds 127.0.0.1 by default; `--http-host 0.0.0.0` is used only inside a test container published on host loopback (`bridge/factorio_bridge.py:43`, `tests/e2e/stand.py:15`, `scripts/test-e2e.sh:64-67`; no test checks the default).
- A refused request answers HTTP 200 with `{"ok": false, "error": <code>}`; only bad JSON is 400 and an unknown route 404, so callers read `ok`, not the status (`bridge/factorio_bridge.py:635-641`; `tests/integration/conftest.py:133` asserts `isError == not ok`).
- An `ask` is always acked, and queued once per `id` (last 1000 ids remembered) (`bridge/factorio_bridge.py:291-297`; `tests/integration/test_bridge.py::test_ask_reaches_dispatcher_with_ack_and_thinking`).
- A leased ask that is not confirmed in time goes back to the front of the queue (`tests/integration/test_bridge.py::test_asks_wait_while_dispatcher_is_away`).
- Leasing an ask sends `thinking` (a `library` request or a `stop` does not); with no sign of life for `idle_after` (default 1200 s) the bridge sends `idle` with `note: no_reply` (`tests/integration/test_bridge.py::test_idle_after_timeout_without_reply`). `/turn_end` does it at once, but only for the tab's current question (`tests/integration/test_bridge.py::test_turn_end_from_the_dispatcher_ends_thinking`).
- A `stop` from the game goes to the front of the queue and drops that tab's questions still queued; `/stopped` with `ok` ends the turn with note `stopped` and drops a later fallback copy; with `ok: false` the turn goes on with activity "could not stop: …" and its answer still lands (`tests/integration/test_bridge.py::test_stop_from_the_window`, `::test_a_stop_that_failed_keeps_the_turn`).
- Only calls flagged `_agent` count as steps; each step, `/progress`, `/alive` and `/trace` restart the idle clock; a `/trace` after the answer returns `sent: false` (`tests/integration/test_bridge.py::test_long_turn_progress_reaches_the_tab`).
- A dispatcher fallback copy (`fallback: true`) is dropped when the turn already answered (`skipped: already_answered`) or when its `ask_id` is not the tab's current question (`skipped: stale`) (`tests/integration/test_bridge.py::test_dispatchers_fallback_copy_is_dropped_after_a_real_answer`).
- With no heartbeat for `offline_after` (15 s) game requests fail at once with `game_offline`; replies queue and are delivered when the game is back (`tests/integration/test_bridge.py::test_game_offline_and_reply_queue`).
- The outbox is flushed on every `hello` and `heartbeat`, keeps messages tagged with another `run_id` for later, and holds at most 200 (oldest dropped) (`bridge/factorio_bridge.py:287`, `:322-341`; no test for the cap).
- The mod notices a stopped bridge by the missing `pong` and reconnects by itself (`tests/integration/test_bridge.py::test_no_pong_when_bridge_is_down_then_recovers`).
- `mark` results carry `ids`/`failed` and `list_marks` a list as `[]`, never `{}` (`bridge/factorio_bridge.py:219-229`; `tests/integration/test_bridge.py::test_unfiltered_chests_marked_and_unmarked` asserts `failed == []`).
- Stdlib only, and it runs on the macOS system `python3` 3.9 (`README.md` "Layout", `docs/worktrees.md` "Requirements on the host").
- Every `pong` carries `Settings.app_version` when the bridge runs in the app (the backend passes the installed `app.json` version), and `/health` reports the `mod_version` from the last heartbeat (`tests/unit/test_bridge_versions.py`, `tests/integration/test_bridge.py::test_handshake`). A standalone bridge sends no `app_version`.

## Easy to break

- `/health` keys are read by other code: the app status page (`game_online`, `last_seen_s`, `dispatcher_seen_s`, `queued_asks`, `leased_asks`, `agent_seen_s`), `tests/e2e/watch.py` and `tests/integration/test_atlas_game.py` (`atlas_version`, `atlas_dirty`) (`app/backend/status.py:136-198`, `tests/e2e/watch.py:108-131`). `dispatcher_seen_s` and `agent_seen_s` are read from a real bridge by no test, only from fake dicts in `tests/unit/test_app_status.py`, so renaming one turns the status page red with nothing else failing (grep of `tests/`).
- A new MCP tool that posts without `_agent` is not counted as a step and does not restart the idle clock, so a long silent turn can end as "Kira did not answer here" while still working (`bridge/factorio_bridge.py:600-605`, `mcp/factorio_mcp.py:938-939`).
- The UDP ports have two sides: the mod hard-codes `M.BRIDGE_PORT = 34199`, and `tools/ui_dev.py` rewrites it for dev ports. Changing the bridge default alone means no `hello` arrives and `game_online` stays false (`mod/kc-companion/scripts/protocol.lua:10`, `tools/ui_dev.py:148`).
- A running bridge keeps the code it started with. After an edit, restart it (`./scripts/stand.sh ui restart`); `./scripts/stand.sh check` reports a bridge running older code (`docs/worktrees.md` "Checking only your own stand").
- A port that answers is not proof it is your bridge: a dispatcher pointed at another session's bridge takes that session's questions (`docs/worktrees.md` "What is yours").
- `request_timeout` is 10 s: a slow query returns `game_timeout` while the game is still running it, and the late `result` is dropped (`bridge/factorio_bridge.py:49`, `:355-358`).
- Partial UDP messages are dropped after 30 s, and a lost atlas packet fails the request with `atlas_incomplete` (`bridge/factorio_bridge.py:130`, `:364-367`).
- The API has no authentication; binding it to anything but loopback lets anyone reaching the port run Lua in the game (SPEC.md "Mod ↔ bridge protocol").

## How to check a change

- `./scripts/test-integration.sh`: level 2, mod + bridge + MCP in Docker on headless 2.1.23, ~65 s; logs in `.test-data/integration/bridge.log`. Pytest arguments pass through, e.g. `./scripts/test-integration.sh -k test_bridge` (`scripts/test-integration.sh:20`). The harness runs the bridge with short timeouts (`--offline-after 3 --idle-after 2 --request-timeout 5`) and game speed 4 (`tests/integration/conftest.py:30-31`).
- The bridge has no unit tests of its own. Unit tests that fake it: `tests/unit/test_app_status.py` (a fake `/health`), `tests/unit/test_e2e_watch.py` (health dicts), `tests/unit/test_panel_tool.py`, `tests/unit/test_kc_atlas.py`, `tests/unit/test_compute.py` (fake `bridge_post` in the MCP server). Run them with `./scripts/test-unit.sh tests/unit/test_app_status.py tests/unit/test_e2e_watch.py` (~5 s).
- Test files that drive the real bridge: `tests/integration/test_bridge.py` (the protocol, queues, timers), every other `tests/integration/test_*_game.py` through the MCP fixture, `tests/integration/test_atlas_game.py` (`/atlas`, `/health` atlas keys), `tests/integration/test_panels_game.py` (`library` asks, `/library_result`).
- Full chain: `./scripts/test-e2e.sh` (~4 min, real model, `tests/e2e/`) and `./scripts/test-app-e2e.sh` (~20 min, real client, the bridge inside the app backend, `tests/app_e2e/`).
- Syntax on the system Python: `/usr/bin/python3 -c "import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]" bridge/factorio_bridge.py` (run, passes on 3.9.6).
- By hand on a stand: `./scripts/stand.sh ui restart`, then `./scripts/stand.sh check` ([worktrees.md](../worktrees.md)).

## Decisions

- When SPEC, README and the code disagree, the code wins: confirmed by Michael on 2026-10-29.
- HTTP on loopback only, no authentication: whoever reaches the port can run Lua in the game. SPEC.md "Mod ↔ bridge protocol".
- Packets to the game at most 4000 bytes: the game's receive buffer starts at 4 KB and grows only after dropping a packet. SPEC.md "Mod ↔ bridge protocol".
- An ask queue with a lease: a question survives a dispatcher restart and a dispatcher dying halfway. SPEC.md "How it works".
- The bridge sends `idle` itself after 20 min without a sign of life, and at once on the dispatcher's `/turn_end`: "thinking…" must not hang when the agent does not answer, and a model thinking for minutes must not be cut off (90 s did that until 0.3.3). SPEC.md "How it works" (the agent paragraph and "Who sets the status"), "v1 done criteria".
- `factorio_query` runs arbitrary Lua with no sandbox; read-only is an agent rule: confirmed by Michael on 2026-10-29, SPEC.md "Agent tools".

## Terms

- **ask**: a question from a tab, `{id, chat, text, run_id, run_name, tick, player, pointers}` (`bridge/factorio_bridge.py:297`).
- **lease**: the time a taken ask stays out of the queue unless confirmed; 120 s default, the dispatcher asks for 60 s (`bridge/factorio_bridge.py:51`, `dispatcher/factorio_dispatch.py:554`).
- **outbox**: messages for the game held while it is offline (`bridge/factorio_bridge.py:332-341`).
- **step**: one `_agent` tool call counted for the tab's "thinking… step K" line. **activity**: the latest `factorio_progress` text, cut to 80 characters (`bridge/factorio_bridge.py:476-493`).
- **fallback copy**: the dispatcher's forward of a turn's last text when it saw no `factorio_reply` (see [Dispatcher](dispatcher.md)).
- **online**: a `hello`/`heartbeat`/`ask` arrived within `offline_after` (`bridge/factorio_bridge.py:273-274`).
