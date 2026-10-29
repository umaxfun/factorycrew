# Factorio ↔ Kiro Crew: v1 spec

## Why

Ask Kira about the current run while playing, without leaving the game. Example: "find yellow chests without a filter, extra items leak into them". The agent looks at the live state of the save itself, answers with text in the in-game window and puts marks on the map. When the marks are no longer needed, they are removed from the chat or with a button.

The player has several topic chats ("Logistics", "Trains", "Power"). Each in-game tab is a separate Kiro Crew chat. Chats are grouped in the sidebar by run:

```
Factorio/
  Megabase-2/
    Logistics
    Trains
  Space Age run/
    Logistics
```

So a conversation has memory ("now show the next one"), it can be continued from the dashboard, and "Logistics" of different runs never mix.

## Language

Everything in the repo and in the game is in English: docs, code, the window, the agent rules. Kira answers in the language the player writes in; the e2e checks this with an English and a Russian question.

## In v1

Ask questions from an in-game window, in chat tabs.

The agent reads the game state with Lua queries, puts marks on the map and removes them, answers into the window.

Marks can be removed three ways: ask the agent ("remove the marks"), press the button in the tab, delete them by hand on the map.

Jump the camera between marks with "next" and "previous" buttons.

One folder per run, one chat per tab inside it.

Automated tests on the three levels below: mod logic, mod + bridge + MCP integration, and e2e through Kiro Crew.

## Not in v1

The agent changing the world: setting a filter, moving or deleting an entity. That is the next version, with an "apply" button in the game.

Multiplayer. v1 is single player only.

Publishing the mod on the portal.

Attaching existing Kiro Crew chats. Factorio chats are created by the dispatcher itself, see the `not_creator` restriction below.

## What runs where

Everything is on one Mac. The game runs under the `gamer` account; the bridge, MCP server, dispatcher and Kiro Crew run under `umaxfun`. Loopback (127.0.0.1) is shared by all accounts of one Mac, so UDP between the game and the bridge needs no network. While the player is in `gamer`, the `umaxfun` processes keep running in the background through fast user switching. `umaxfun` must not log out, only switch.

Kiro Crew for Factorio is a separate gateway, not the main one: `scripts/kc.sh`, port 5477, data in `~/.kirocrew-factorio`. It does not touch the main gateway (5476, `~/.kiro/crew`, `~/.kiro/agents`): it has its own `KIROCREW_HOME` and its own kiro-cli home `KIRO_HOME=$KIROCREW_HOME/kiro`. Kiro Crew treats that layout as the instance's private agent directory (`isolated_agents_dir`), the same way its isolated pods work. So the agent template, dispatcher, cron and `agent.session_control` live in this gateway, and `scripts/kc.sh install` puts them there. The gateway starts with a clean environment: otherwise the launching session's variables (its `TMPDIR`, scratch) leak in and the cron sandbox refuses to run the script. This instance does not need the embedding model (`KIROCREW_SKIP_MODEL_DOWNLOAD=1`); memory search falls back to keywords.

`/Users/gamer/Library` is closed to `umaxfun` (`drwx------`). So the mod lives in the shared folder `/Users/Shared/kc-companion/`, and the game's mods folder under `gamer` has a symlink to it. Mod changes are made in `/Users/Shared`; in the game, restarting is enough (a reload of the save for `control.lua`, a full restart for graphics and prototypes).

## How it works

The chain:

```
in-game window ──send_udp──▶ bridge ◀──long-poll── dispatcher (KC script-cron) ──send_to_session──▶ chat Factorio/<run>/<tab>
     ▲                         ▲                                                                       │
     └──────UDP────────────────┴──────HTTP 127.0.0.1──── MCP server factorio ◀── tool calls ───────────┘
```

Four components.

**Mod `kc-companion`** (Lua, Factorio 2.x). Owns the window with tabs, query execution, marks, and UDP send and receive. It calls `helpers.recv_udp()` in `on_nth_tick(10)` and gets the data in `on_udp_packet_received`. It runs queries through `load(text, name, "t", env)`: Factorio allows text chunks and forbids only binary ones.

**Bridge** (Python, asyncio, a separate background process under `umaxfun`). Listens to UDP from the game and serves an HTTP API on 127.0.0.1 for the dispatcher and the MCP server. Matches requests to results by `id`, chunks large messages and reassembles them. Holds two queues: `reply` while the game is unreachable, and `ask` until the dispatcher picks the question up (for example between dispatcher restarts).

**MCP server `factorio`** (stdio, Python). Gives the agent its tools and talks to the bridge over HTTP.

**Dispatcher** (a script-cron of the factorycrew gateway, `~/.kirocrew-factorio/crons/factorio_dispatch.py:run`, source `dispatcher/factorio_dispatch.py`). Kiro Crew caps the script subprocess at 3600 seconds, so one wake long-polls for 3500 seconds and exits, and the cron with `every=30` (Kiro Crew rounded it to a minute), `timeout=3600`, `timeout_secs=3610` starts the next one; only one runs at a time. Questions asked in between wait in the bridge queue. `ctx.message` is optional JSON that overrides settings (bridge address, wake length). Do not recreate the cron without need: the chats it opened accept messages only from the same job (`not_creator`). A stopped gateway does not kill its child script, so the dispatcher exits by itself when its parent gateway has changed; otherwise after a gateway restart two dispatchers share one queue (this happened once). A wake compiles the script once, so it also exits when the installed file changes (`scripts/kc.sh install` copies only on change); otherwise old code keeps serving for up to an hour (it once sent a stale chat header). `scripts/test-e2e.sh` waits until the wake serving the run started after the installed script.

The dispatcher long-polls the bridge for new questions (`GET /asks?wait=25`). The bridge leases a question: if the dispatcher has not confirmed it (`POST /asks/done`) within 120 seconds, it goes back to the queue. The dispatcher confirms after `send_to_session`, so a question is not lost if the dispatcher dies halfway. For each question it:
1. Finds or creates the `Factorio` folder and the run folder inside it. It looks folders up with `ctx.list_session_folders()` and creates one only if it is missing. The public `ctx.create_session_folder(name)` can only create top-level folders, so the dispatcher creates the nested one with the private `ctx._post("/api/chat/folders", {"name": ..., "parent_id": ...})`. That call is wrapped to fail with a clear error if a Kiro Crew update breaks it. The request to add `parent_id` to the public method: [issue #25210](https://github.com/kirodotdev/KiroCrew/issues/25210). We switch once it lands.
2. Opens the chat with the private `ctx._post("/api/chat/slots", {name, title, folder_id, agent="factorio-companion"})` and, on first open, calls `ctx.set_session_mode(slot, "trust_reads")`. The public `open_session` does not fit: it sends one string as both key and title, and the session key is global, so "Logistics" of two runs would land in one chat. The key is `factorio-<hash of run_id and tab>`, the title is the tab name.
3. Sends the message: `ctx.send_to_session(slot, message)`. A chat only takes messages from the cron job that opened it, and an app cron gets a new id on every reinstall and every disable/enable. On `not_creator` the dispatcher opens a fresh chat for the tab (key `factorio-<hash>-<gen>`, same title and folder) and resends; the old chat stays in the sidebar with its history, and the new one starts without the agent's memory of it. If any of this fails, the question is not confirmed and returns by lease (60 s). After the third failure the tab gets "Could not pass the question to Kiro Crew: <reason>", and the question is dropped.

It keeps "`run_id` + tab → slot" and "`run_id` → folder" in `~/.kirocrew-factorio/factorio/dispatch-state.json`. The key is `run_id`, not the name. Renaming a run after its folder exists is not carried over to the folder yet (open question). If two different runs have the same name, the second folder gets a `run_id` suffix.

**Agent `factorio-companion`** (a Kiro Crew agent template, `agent/factorio-companion.json`, rules in `agent/factorio-companion.md`). Tools are `@factorio` only, all auto-approved; the agent has no shell and no files. `scripts/kc.sh install` puts the template into `~/.kirocrew-factorio/kiro/agents/` with absolute paths into this repo. Seen in e2e: on a question with an obvious answer ("NOTHING") the model once answered with text in the chat and did not call `factorio_reply`, so the player saw nothing. Once the prompt said plainly that the player sees only `factorio_reply`, it did not happen again. The safety net for that case: "thinking…" goes out after 90 seconds with "Kira did not answer here".

## What a "run" is

The Factorio Lua API does not expose the save file name: `runtime-api.json` 2.1.24 has neither such a field nor a save event. There is only `script.level` (`freeplay`/`base` for a normal game), which does not tell runs apart. So the mod defines the run itself:
- in `on_init` (a new game, or the mod's first time on an old save) it generates a `run_id` and stores it in `storage`. It survives save and load;
- the player names the run once in the mod window, default "Run 1", and can rename it later;
- "Save as" to a new file keeps the same run: it is the same game. A new map gives a new run.

## Mod ↔ bridge protocol

Each UDP packet holds one JSON string `{"v":1,"id":"…","type":"…",...}`. A long message is split into parts with `part` and `parts` fields; the receiver reassembles them by `id`. The bridge sends the game packets of at most 4000 bytes: the game's receive buffer starts at 4 KB and doubles only after it has dropped a packet that did not fit (measured on 2.1.23: an 8000-byte packet lost, the next 8150-byte one arrived). The mod slices 4096 bytes of source JSON without breaking UTF-8; its packets reach ~8 KB, which the bridge handles. The bridge listens on a fixed port (34199 by default), the game on the port from `--enable-lua-udp`, the bridge HTTP API on 127.0.0.1:34198. The HTTP API has no authentication: whoever reaches the port can run Lua in the game. So the bridge binds to loopback only.

The game sends the bridge:
- `hello` on load and `heartbeat` every 5 seconds, with `run_id`, `run_name`, `tick` and the list of tabs;
- `ask`: `chat`, `text`, and `player` `{name, surface, x, y}` (where the asking player stands; the dispatcher puts it in the chat header, and it is the default place for "here" and for anything Kira builds), and `pointers` when the player made some (see "Pointers");
- `result`: the answer to `query`, `mark` or `unmark`, with the same `id` and `ok`, `data` or `error`.

The bridge sends the game:
- `pong`: the answer to `hello` and `heartbeat`. The window knows the link is up from it;
- `ack`: `id`, confirms that the `ask` was accepted and queued;
- `query`: `lua` and `surface?`. Run the query and return the result;
- `mark`: `chat` and `marks: [{surface, position, text, icon?}]`;
- `unmark`: `chat` and one of `ids`, `all_in_chat` or `all`;
- `list_marks`: `chat?`, return the live marks;
- `reply`: `chat` and `text`, the agent's answer into the tab;
- `status`: `chat` and `state` (`thinking` or `idle`), for the "thinking…" indicator; while thinking also `steps` (the agent's tool calls so far) and `activity` (its latest `factorio_progress` line);
- `trace`: `chat`, `kind` (`text` or `tool`) and `text`, one step of the running turn.

Long turns. The window shows "thinking… N s · step K · <activity>" and, under the question, the turn as it runs: what Kira writes between tool calls and which tools ran (repeats fold into "×N"), up to 12 lines. On the answer the trace folds into one gray line "(worked: K steps, N s)". Sources: the bridge counts every MCP call (a call without a chat counts for every thinking tab of the run, normally one); `factorio_progress(chat, text)` sets the activity; the dispatcher follows the chat transcript (`<KIROCREW_HOME>/sessions/dashboard_<slot>.jsonl`, appended as the turn runs) and posts each new line to the bridge's `/trace` until `factorio_reply`. Every such sign of life restarts the 90-second "did not answer" clock, so a long turn that keeps working is not cut off. The transcript is Kiro Crew's own storage: if its shape changes, the trace stops and nothing else breaks. Measured on the real client: the 47-second logo build showed 33 steps and Kira's text from second ~5.

Who sets the status: the bridge sends `thinking` when the dispatcher picks up the question and `idle` after a `reply` into that tab. If there is no `reply` for 90 seconds, the bridge sends `idle` itself and the tab shows "Kira did not answer here; the answer may be in the chat".

If there is no `heartbeat` from the game for more than 15 seconds, the bridge considers it unreachable. MCP requests then fail at once with `game_offline`, and `reply` messages queue up and are delivered after the next `hello`.

If there is no `pong` from the bridge for more than 15 seconds, the window shows "Kira is offline" all the time, without waiting for a question. If an `ask` gets no `ack` within 3 seconds, the tab marks that message as not delivered. When `pong` returns, the indicator clears by itself.

A paused game does not receive packets, because no ticks run. That is fine: the answer arrives when the game resumes. The mod window does not pause the game.

## Agent tools (MCP `factorio`)

`factorio_query(lua, surface?)`. Runs a Lua body on the save and returns JSON (whatever `return` gave). Used for any question about the game state. The query environment has `game`, `helpers`, `defines`, `surface` (the player's current surface or the one passed) and `player`. The result is capped at 64 KB; a larger one returns `result_too_large` with a hint to narrow the query. The query runs inside a game tick: while it computes, the game stands still. Factorio Lua has no instruction limit, so an infinite loop hangs the game. The tool result has `ms` so the agent sees what its queries cost. It is the round trip through the bridge including the polling wait (up to ~170 ms): Factorio Lua cannot read execution time as a number. An empty Lua table comes from the game as `{}`. In list fields (`ids`, `failed`, the list of marks) the bridge turns it into `[]`; in an arbitrary query result the agent must expect both.

`factorio_mark(chat, marks)`. Puts marks on the map and returns the `ids` placed and the list not placed with a reason (`add_chart_tag` returned `nil`, for example a position off the map or a surface that does not exist). Uncharted territory is not a reason: a tag is placed on an uncharted chunk too (checked on 2.1.23 and by a level 1 test on 2.0.77). A mark is a chart tag with a `[KC]` prefix in its text. The mod remembers each mark in `storage.kc.marks[chat][id]`.

`factorio_unmark(chat, ids? | all_in_chat? | all?)`. Removes marks and returns how many were removed. Only marks the mod placed; the player's tags are left alone.

`factorio_list_marks(chat?)`. The live marks with positions and text. For questions like "what did you mark" and "remove the ones on Nauvis".

`factorio_compute(python, data_lua?, timeout?)`. Python 3 (stdlib) for anything heavier than a lookup: clustering, layouts, pipe routing, pole grids, ratios. `data_lua` runs first in the game and its return value is `data`; the code sets `result`. It runs off the game tick, so the game does not freeze. Helpers as `kc` (`mcp/kc_compute.py`): `connect` (one connected pipe/belt network joining all outputs around blocked tiles), `astar`, `footprint`, `unreached` (connectivity), `unwired` (copper-wire links by the shorter reach), `tile`. Sandbox: macOS seatbelt without network, writes or reading the home dir; under Kiro Crew the MCP server already runs inside the gateway's sandbox and macOS refuses a nested one, so there the code runs under the outer sandbox only (`sandboxed: false` in the result). Pattern: read with `data_lua`, compute, then place with one `factorio_query` calling `kc.place_ghosts`.

Mod helpers in the query environment (`kc`, `mod/kc-companion/scripts/helpers.lua`, tested at level 1): `kc.place_ghosts(surface, items)`, the only way the agent places ghosts. It checks each item like the player's ghost cursor (`build_check_type.manual_ghost`), refuses overlaps with entities, other ghosts and bad ground, snaps positions to the entity grid, and reports `unpowered` electric ghosts with a hint to power them in the same turn, plus what each placed inserter picks from and drops into (`inserters`, `inserter_hint`; a backwards inserter is the usual silent failure). A plain `create_entity{name="entity-ghost"}` silently replaces ghosts it lands on; that once put a substation over a pumpjack. Also `kc.poles()`, `kc.nearest_pole()` (with the grid's `sources` and `production_w`), `kc.grid_power(pole)`, `kc.unpowered()`, `kc.inserter_ends()`.

Trial runs (`mod/kc-companion/scripts/trial.lua`): `kc.trial_start(surface, area)` copies the area to a hidden surface (`kc-trial-<tick>`, hidden from every force, lab tiles, peaceful), revives every ghost there, and stands in for the player's grid with an energy interface wherever a real pole in the area belongs to a network with power sources. The game then runs it; `kc.trial_report{finish = true}` returns non-working entities with their status, item deltas per container/machine, finished crafts and belt item counts, and deletes the surface. The player's surface is not touched. The agent must try every build this way before it replies "done" (agent rules, "Done means it works").

`factorio_atlas(level?, id? | at? | box?, item?, refresh?, surface?)`. The base as blocks (one recipe standing together), zones (blocks standing together and fed the same way) and what feeds what (belt, inserter, chest, pipe, train stop, logistic network); design and measurements in docs/atlas.md. The bridge's `POST /atlas` asks the mod for the structure dump (`mod/kc-companion/scripts/atlas.lua`): a few chunks per tick with an entity budget, sent as `atlas_part` messages and closed by the usual `result` with the part count, so a lost packet makes the request fail (`atlas_incomplete`) instead of returning a cut base. The MCP server parses it (`mcp/kc/atlas.py`) and caches it per surface and run; statuses are read live with one query per call.

`factorio_reply(chat, text)`. Shows the answer in the tab. If the question came from the game, the agent must end its turn with this call.

Writing to the world is not technically restricted in v1, because a Lua query can call anything. Read-only is an agent rule, not a sandbox. This is a deliberate v1 risk: the game is single player, the save is yours, and every query is visible in the chat. A sandbox with a read-only proxy may come in v2 together with writes behind confirmation.

## Agent rules (steering `factorio-companion`)

Answer in the player's language, whatever language the rules and the window use.

Answer briefly, a few lines: the in-game window is small. Put details in the chat if needed and give a summary in the game.

Write narrow queries and aggregate on the Lua side: count, group, return coordinates. Do not return entity dumps. A query freezes the game while it runs: search by area or filter, not by walking the whole map. If a query took more than 100 ms, make the next one narrower.

If the answer points at places on the map, mark them. Before a new series of marks on the same topic, remove this chat's old marks unless the player asked to keep them. If some places were not marked, say so and why.

On "remove", "clear", "enough marks" (in any language) call `factorio_unmark`.

If the save has several surfaces (Space Age), say which surfaces were searched.

Building is done with ghosts only (build plans the player's robots or hands complete), near the player by default, on free ground (`can_place_entity`), with a mark at the center. Existing entities are never removed, rotated or reconfigured, and no real entities or items are created. This is an agent rule, not a sandbox.

## In-game window

Hotkey `Ctrl+Shift+K`; a button with Kira's face on the top bar does the same.

Above the tabs: the run name, renamed with the pencil button.

Tabs on top, four per row in fixed-width buttons, so any number of tabs stays inside the window. A name longer than 9 characters is cut with "…", and the full name is in the tooltip. Up to three rows are shown; more scroll, with the selected tab kept in view. "+" creates a new tab and asks for its name, the close button closes the tab. Closing a tab does not delete its Kiro Crew chat; a tab with the same name can be opened again.

In a tab:
- a feed of the last 30 messages, Kira's lines with her icon;
- an input field, a "Send" button, and Enter to send;
- a "thinking…" indicator;
- a link indicator ("online" / "Kira is offline" / "UDP is off"), from `pong`;
- a marks row "Marks: 14 ◀ ▶ 🗑". The arrows open the map view on the previous and next mark with `player.set_controller{type=remote, position, surface}` (2.0 has no `zoom_to_world`), Esc returns to the character; the trash button removes all marks of the tab.

- a pointers row, shown while there are pointers: "@1 assembling-machine-2 · electronic-circuit", "@2 box 30×20 · 42", and a trash button; next to the input a Point button that puts the pointer tool in the hand.

The run, tabs, feed and marks live in `storage` and survive save and load.

### Pointers

How the player says "there", "this factory", "like that station". The pointer is a selection tool (`kc-pointer`, like the deconstruction planner): the Point button in the window, `Alt+K`, or the shortcut bar. A click is a point: the entity under it (a building before the ore beneath it; ore looked up by tile, its collision box is ±0.1), or bare ground with its tile. A drag is a box, sent with a summary: entity counts (top 12), recipes, ore amounts. Shift-drag removes all pointers.

Each pointer gets the next number for that player (up to 9), is drawn on the ground for that player only (a circle or a rectangle with "@n"), and puts "@n " at the end of the question field, opening the window if needed. The next question takes all pointers along and clears them and their drawings; the feed shows "(+2 pointers)" after the question.

What a pointer MEANS is Kira's job, not the mod's: a click on one assembler in "why is @1 slow" means the connected group it belongs to, which she finds by following belts, inserters and pipes (agent rules, "Pointers"). The mod only says where and what is under the cursor.

Wire form (`ask.pointers`): `{n, kind="point", surface, x, y, tile, entity?={name, type, x, y, recipe?, mining?, amount?, status?, ghost?}, resource?}` or `{n, kind="box", surface, area={x0,y0,x1,y1}, w, h, count, counts?=[{name,n}], recipes?=[{name,n}], resources?=[{name,amount}]}`.

If the player deletes a `[KC]` mark on the map (`on_chart_tag_removed`), the mod drops it from `storage` and the agent no longer sees it.

## The message the dispatcher puts in the chat

```
[Factorio · run "Megabase-2" · tab "Logistics" · tick 12345678]
find yellow chests without a filter
```

With pointers, one line each between the header and the question:

```
[Factorio · run "Megabase-2" · tab "Build" · tick 12345678 · player p at (3.2, -1.5) on nauvis · reply in English]
[@1: click at (12.4, -30.2) on nauvis; assembling-machine-2 (assembling-machine) at (12.5, -30.5), recipe electronic-circuit, status item_ingredient_shortage]
[@2: box (10, -40)-(40, -20), 30×20 on nauvis; 42 entities: 30 transport-belt, 1 train-stop; recipes: 8 electronic-circuit]
why is @1 slow? and build one like @2 here
```

The agent sees which tab the question came from and answers into it. If the player writes into the same chat from the dashboard, the agent answers in the dashboard as usual and writes into the game only when asked.

## What to enable

In the factorycrew gateway `agent.session_control = true`; without it the dispatcher gets `session_control_disabled`. `scripts/test-e2e.sh` sets it (or `scripts/kc.sh config set agent.session_control true`).

The `factorio-dispatch` script-cron in the factorycrew gateway. `scripts/test-e2e.sh` registers it if it is missing.

The `factorio-companion` agent template: `scripts/kc.sh install`. Nothing goes into the shared `~/.kiro/agents/`.

Under the `gamer` account:
- the Steam launch option `--enable-lua-udp 34200`;
- the symlink `~/Library/Application Support/factorio/mods/kc-companion` → `/Users/Shared/kc-companion`, and the mod enabled.

Adding the mod to an existing save does not break the world. But with any mod, Steam achievements no longer count on that save; only the local "modded" ones remain. The code the mod runs through `load` disables nothing extra: the console `/c` does, not the mod.

The bridge and the factorycrew gateway start automatically: `scripts/services.sh install` installs the launchd agents `dev.factorycrew.bridge` and `dev.factorycrew.gateway` (KeepAlive, RunAtLoad), `uninstall` removes them. `scripts/deploy-mod.sh` copies the mod to `/Users/Shared/kc-companion` for the game.

## Tests

The levels: 1 is mod logic, 2 is an integration test of mod + bridge + MCP without Kiro Crew, 3 is e2e, the full cycle from an action in the game through Kiro Crew and back. Only level 3 is e2e.

Environment for levels 1 and 2 (checked 2026-10-28 on this Mac):
- a `linux/amd64` Docker image (`docker/Dockerfile`): `factoriotools/factorio` plus `node:22-slim` and Python. On Apple Silicon it runs through Rosetta and the binary starts directly. The `arm64` image of the same tag runs the x64 binary through `box64` inside, and a direct start there fails with a Rosetta error. We use `amd64`.
- The headless server needs `auto_pause: false` in `server-settings.json` (otherwise with no players no ticks run and `recv_udp` is never called) and an open stdin (it exits on EOF).
- The `factorio-test` mod on the portal needs a Factorio login and token. Instead it is built from source when the image is built and placed in the test data dir's `mods`. Factorio 2.0 needs tag `cli-v3.5.1` (mod 3.0.2): from 3.6.0 the mod requires 2.1. The CLI of the same version, `factorio-test-cli@3.5.1`, calls `npx fmtk`, so the image also has `factoriomod-debug`. No credentials needed.
- The 2.0.77 headless server crashes on the first UDP packet the mod receives (`TickClosure.cpp:91: Trying to add invalid input action`). It is a game bug fixed in 2.1: [forums.factorio.com, t=134790](https://forums.factorio.com/viewtopic.php?t=134790). The client is not affected: UDP works in the real 2.0.77 game (checked with the UI loop below). So level 1 runs on 2.0.77 (it needs no UDP) and level 2 on headless 2.1.23. The test copy of the mod declares the `factorio_version` it runs on.

**Level 1, mod logic, on every change.** [FactorioTest](https://github.com/GlassBricks/FactorioTest), headless 2.0.77 in Docker under `umaxfun`, `./scripts/test-mod.sh`, ~10 seconds including container start. Covers: `query` and its result shape, query errors, the 64 KB cap, chunking of large messages (including without breaking UTF-8), `mark`/`unmark`/`list_marks` by `ids`, by chat and all at once, reasons for failed marks, manual tag deletion, other tags left alone, `run_id`, tabs, `ask`/`ack`/`reply`/`status`, a `reply` for another run ignored, the feed capped at 30. The window: a headless FactorioTest run has a LuaPlayer, so the GUI tree is really built and checked in code: the top-bar button, open and close, the default tab, the link indicator before and after `pong`, Enter sends to the selected tab, "thinking…", the answer, "Kira did not answer here", new, switch, close and reopen a tab with its feed, run rename, the marks counter, ◀ ▶ wrapping around, and the trash button touching only its own tab. How the window looks is checked with the UI loop.

**Level 2, mod + bridge + MCP integration, on every bridge or protocol change.** No Kiro Crew in this loop; the test plays the dispatcher and the agent. Headless Factorio 2.1.23, bridge, MCP server and pytest in one container: UDP is loopback only, so they share a network namespace. The test acts for the player over RCON (`remote.call("kc", "ask", …)`) and reads the mod state the same way; the code under test does not use RCON. Game speed is raised to 4 so the mod's tick timeouts pass in seconds. `./scripts/test-integration.sh`, ~20 seconds. Scenario: 20 `storage-chest`s, 7 without a filter. Through MCP the test calls `factorio_query` the way the agent would, places `factorio_mark` and checks 7 `[KC]` tags at the right coordinates. Then `factorio_unmark(all_in_chat)`: 0 `[KC]` tags, the player's tag in place, and `factorio_reply` reaches the tab. Also: an `ask` reaches the dispatcher with `ack` and `thinking`; mark and query errors; large messages both ways in Cyrillic (on purpose, to catch broken UTF-8); bridge packet size; `idle` by timeout without `reply`; the `ask` queue and a lease returning an unconfirmed question; `game_offline` and the `reply` queue while the game process is stopped (SIGSTOP), and delivery after it resumes; losing `pong` when the bridge is down, and recovery.

**Level 3, e2e, before handing over.** The full cycle: a question from the game → bridge → dispatcher → Kiro Crew chat `Factorio/<test run>/<tab>` → agent `factorio-companion` with MCP → marks and answer in the game. Parts:
- automated, `./scripts/test-e2e.sh`: a fresh headless 2.1.23 with a bridge in Docker (bridge HTTP and RCON published on the host's 127.0.0.1 only), the real dispatcher, factorycrew gateway and agent with a real model. A new stand is a new `run_id`, so every run gets its own folder `Factorio/e2e MM-DD HH:MM` and empty chats. Questions go into the mod window over RCON. Checks: the 7 unfiltered chests are found and marked (a mark within a tile of each chest, the player's tag in place) and the answer is in English; the follow-up "how many did you mark" is answered from the chat context; "remove the marks" removes them all; a new tab is a separate chat without the other tab's history; a question in Russian is answered in Russian. Measured 2026-10-28 (before the language check): a question reaches the chat in 0.2–0.3 s, an answer with a search and marks in ~20 s, the first question in a new tab in ~6–9 s;
- the e2e takes the host bridge out of launchd for the run (the stand needs its port 34198) and puts it back afterwards;
- by hand under `gamer` on 2.0.77: the same from the real window, and save/load.

**UI loop, while working on the window.** `tools/ui_dev.py` runs a real 2.0.77 client under `umaxfun` from the player's copy `/Users/Shared/factorio.app`, with its own data dir `~/.factorycrew-dev/factorio` and a dev bridge on ports 34298/34299/34300, so it never clashes with the game under `gamer`. `start` deploys the mod, starts bridge and game and waits until the mod is online; `shot NAME [STEP ...]` drives the window over the bridge (open, click, type, reply, status) and takes `game.take_screenshot{show_gui=true}` into `~/.factorycrew-dev/factorio/script-output/kc/NAME.png`. The client renders while the console is on `gamer`.

## v1 done criteria

On your save, a question about unfiltered chests from the in-game window gets an answer in the tab, and every such chest is marked. The agent's turn starts within 5 seconds for an already open chat. The first question in a new tab is measured separately: it adds a cold session start. The whole answer arrives within a minute.

"Remove the marks" in the chat and the trash button remove all marks of the tab and leave your own tags alone.

Two tabs give two separate chats in the run folder, and their context does not mix. Two runs give two folders inside `Factorio`, and "Logistics" of one run does not see "Logistics" of the other.

After reloading the save the run, tabs, feed and marks are in place, and questions go to the same chats.

With the bridge down the window shows "Kira is offline" and does not hang. When the bridge is back, the link recovers by itself. A question asked while the dispatcher restarts is not lost.

If the agent did not answer, "thinking…" does not hang forever.

Kira answers in the language of the question.

Level 1 and 2 tests pass with the commands from the README, and the level 3 e2e passes.

## Decided

**Where the game runs.** On this Mac under the `gamer` account, see "What runs where". No network or tunnels.

**`load` in the mod runtime.** Available for text chunks (Factorio Lua API docs, Libraries). `factorio_query` takes arbitrary Lua; no fallback to primitives.

**Achievements.** Turned off by the mod being added at all, not by code through `load`. See "What to enable".

**Chat key.** `run_id` + tab name, one folder per run.

**Save name in the Lua API.** Not there (checked in `runtime-api.json` 2.1.24). The run is a `run_id` in `storage`, the name comes from the player.

**FactorioTest on headless.** Works: the CLI starts headless by default. Level 1 runs in Docker under `umaxfun`; the game under `gamer` is not needed for tests. Details in "Tests".

**Headless Factorio in Docker on Apple Silicon.** Works in the `linux/amd64` image through Rosetta. A probe mod on a 2.1.23 headless server checked the whole protocol path:
- UDP both ways;
- `load` with its own environment;
- a compile or runtime error comes back as text;
- `run_id` from `on_init`;
- round trip ~85 ms over 10 pings (polling every 10 ticks adds up to ~166 ms).

The same run confirmed that a query can write to the world (`game.speed = 2` worked): there is no sandbox, as said above.

**Marks on uncharted territory.** Placed: `add_chart_tag` returned a tag on an uncharted chunk.

**Game version.** 2.0.77 under `gamer`. The mod declares `factorio_version: "2.0"`. Level 1 runs on headless 2.0.77, level 2 on 2.1.23 because of the headless 2.0 UDP bug. UDP on the 2.0.77 client works (UI loop and the player's own game).

**Screenshots for UI work.** `game.take_screenshot{show_gui=true}` needs a real client; headless renders nothing. Solved with the UI loop above.

## Open questions

**Private `_post` calls.** Nested folders until [issue #25210](https://github.com/kirodotdev/KiroCrew/issues/25210); a session title separate from its key is not requested in an issue yet.

**Run rename.** A folder gets the run name at the first question; a later rename is not carried over. That needs a folder PATCH, which `ScriptContext` does not have.
