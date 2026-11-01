# factorycrew

Ask Kiro Crew about the current Factorio run from an in-game window. Design: [SPEC.md](SPEC.md). Changing the code: start with [AGENTS.md](AGENTS.md) (commands, rules, a doc per area).

## Layout

| Path | What |
|---|---|
| `mod/kc-companion/` | Factorio 2.0 mod: in-game window (Ctrl+Shift+K), query execution, map marks, tabs, UDP to the bridge |
| `bridge/factorio_bridge.py` | Bridge: UDP to the game, HTTP API on 127.0.0.1:34198 for the dispatcher and the MCP server |
| `mcp/factorio_mcp.py` | MCP server `factorio` (stdio): `factorio_query`, `_mark`, `_unmark`, `_list_marks`, `_reply`, `_progress`, `_compute`, `_atlas`, `_panel`, `_saved_panels` ([docs/areas/mcp-server.md](docs/areas/mcp-server.md)) |
| `mcp/kc/` | the `kc` primitive library Kira imports in `factorio_compute`; atlas and circuits live here too |
| `skills/` | Python skills (belts, pipes, power, ...) importable in `factorio_compute` |
| `app/` | the Kiro Crew app package: manifest, backend (runs the bridge), status page |
| `tools/ui_dev.py` | the real-client UI loop and screenshots |
| `docker/Dockerfile` | Test image: headless Factorio + FactorioTest (built from source, no portal login) + Python |
| `dispatcher/factorio_dispatch.py` | Script-cron: asks from the bridge -> chats `Factorio/<run>/<tab>` |
| `agent/` | Agent template `factorio-companion` and its rules |
| `scripts/kc.sh` | The separate Kiro Crew gateway for this project (port 5477, `~/.kirocrew-factorio`) |
| `scripts/stand.sh` | A whole stack per worktree (game, bridge, gateway, Kira) on its own ports: [docs/worktrees.md](docs/worktrees.md) |
| `tests/integration/` | Level 2: mod + bridge + MCP |
| `tests/e2e/` | Level 3: the full cycle through the factorycrew gateway |

All Python is stdlib only (tests use pytest).

## Several worktrees at once

Each worktree runs its own copy of everything: `./scripts/stand.sh up <slot>`. Ports, homes and what not to touch: [docs/worktrees.md](docs/worktrees.md).

## The factorycrew gateway

A separate Kiro Crew instance, so the agent, dispatcher cron and settings never touch the main one (port 5476, `~/.kiro/crew`, `~/.kiro/agents`):

```bash
./scripts/kc.sh start      # http://127.0.0.1:5477, data in ~/.kirocrew-factorio
./scripts/kc.sh install    # agent template + dispatcher script into that home
./scripts/kc.sh token      # dashboard URL with a login token
./scripts/kc.sh stop
./scripts/kc.sh cron list  # any other kirocrew subcommand runs against this instance
```

Its kiro-cli home is `~/.kirocrew-factorio/kiro` (`KIRO_HOME`), so agent specs and transcripts live there too.

## Running the bridge

```bash
python3 bridge/factorio_bridge.py        # udp :34199, game at :34200, http 127.0.0.1:34198
```

The HTTP API has no authentication: whoever reaches the port can run Lua in the game. It binds to 127.0.0.1 only.

## Tests

**The fast check, before every commit: `./scripts/check.sh`** (~2 min). It runs the Python unit tests, level 1 on 2.0.77 and 2.1.23, and level 2, every step even when one fails, and prints one line per step; logs in `.test-data/check/`. CI runs the same steps.

Python unit tests (`tests/unit`: the kc library, atlas on the committed fixture dump, circuits, the MCP tools against a fake bridge, the dispatcher, the app status page, the e2e watchdog): `./scripts/test-unit.sh`, ~20 s, no game and no Docker. Any pytest arguments go through, e.g. `./scripts/test-unit.sh tests/unit/test_kc_atlas.py -k trace`.

Then three game levels (SPEC.md, "Tests"). Only level 3 and the app e2e are end to end.

| Level | What | Command | Factorio |
|---|---|---|---|
| 1 | mod logic under FactorioTest | `./scripts/test-mod.sh`, `./scripts/test-mod-2.1.sh` | headless 2.0.77, 2.1.23 |
| 2 | integration: mod + bridge + MCP, test plays dispatcher and agent | `./scripts/test-integration.sh` | headless 2.1.23 |
| 3 | e2e: game → bridge → dispatcher → Kiro Crew chat → agent → game | `./scripts/test-e2e.sh` | headless 2.1.23 + factorycrew gateway; live game 2.0.77 by hand |

Level 2 is on 2.1 because a 2.0 headless server aborts on the first UDP packet the mod receives ([forum report](https://forums.factorio.com/viewtopic.php?t=134790), fixed in 2.1). Each image is built on first use (~1 min); level 1 takes ~12 s, level 2 ~65 s. Logs of the last level 2 run: `.test-data/integration/`.

Level 3 starts the factorycrew gateway if needed, installs the agent and dispatcher, registers the cron once, and runs a fresh game stand in Docker (bridge HTTP and RCON published on 127.0.0.1 only). It uses a real model, about 10 agent turns per run (~4 min): `tests/e2e/test_e2e.py` (chests, follow-up, unmark, a separate tab, a Russian question answered in Russian) and `tests/e2e/test_panels_e2e.py` (live panels and their library).

## Kiro Crew app (the redistributable package)

`scripts/build-app.sh` builds `dist/factorycrew/` (+ zip): the mod, the bridge run by the app backend, the MCP server, the dispatcher as the app's cron, and the Kira agent. A player installs it with `kirocrew app install <dir>`, trusts it (third-party apps need `agent.apps_trusted`), and toggles it with enable/disable.

```bash
./scripts/app-dev.sh          # build + reinstall + trust + enable on a dev gateway :5479 (~/.kirocrew-factorio-app)
./scripts/test-app-e2e.sh     # app e2e on the real 2.0.77 client, no Docker
```

### Releases and updates

`scripts/release.sh patch|minor|major|X.Y.Z|current [--push]` bumps the version in `app/app.json`, commits and tags `vX.Y.Z` on `main`, builds, and commits the build to the `release` branch as `apps/factorycrew/`. That branch is a Kiro Crew app registry (it synthesizes its index from `apps/*/app.json`). Without `--push` nothing leaves the machine.

Players (Kiro Crew 0.11 or newer): Apps → Discover → Sources (gear) → add the repository URL with branch `release`, install **Factorio Companion** and trust it in the dialog. An app installed from a local directory stays local-only: to switch it to the repository, uninstall it (data is kept) and install from the store. A newer version on `release` arrives through the app's Update action (a `git pull` of the branch); the store caches each app's `app.json` for up to 24 h, so the Update badge can lag a day behind a release.

The mod rides along: its version is the app's, and on every start the app backend replaces the `kc-companion` folder it put in Factorio's mods folder when the version differs (a folder it did not put there is left alone). Nothing comes from the Factorio mod portal. Restart Factorio after an update; the app's status page (sidebar → Factorio) shows the mod version in the mods folder and warns while the running game still has the old one, and Kira's window says so too.

After an update everything else picks up the new code by itself, with no Kiro Crew restart and no chat closed:
- the bridge and the status page run in the app backend, which Kiro Crew restarts on update;
- the dispatcher's wake exits when its file changes, and the next wake (within 30 s) runs the new code;
- Kira's MCP server: each open chat keeps its own server process, so before every tool call the server compares the installed `app.json` version with the one it started with and, on a change, re-executes itself in the same process (same pid and stdio, the chat keeps its connection) and handles that call with the new code. A tool added by the update shows in an open chat only after the chat restarts: the client reads the tool list once;
- installed in two Kiro Crews on one machine, only the first copy gets the bridge ports; the second one's status page says so in the Bridge line;
- the app always talks to the Kiro Crew it is installed in, also when another gateway runs on the same machine (a second install, another user's gateway on the default port 5476); the status page's "Panel library" line shows whether saving works;
- panels that were in the game before the update but have no artifact (up to 0.3.5 an installed app saved none) are saved once per run by the dispatcher.

Verified 2026-10-31 on a dev gateway through the real update route (`POST /api/apps/factorycrew/update`, 0.3.5 to a build marked 0.3.6) with a stand-in game on UDP: Kira's MCP server kept its pid, re-executed with the new code, and her next `factorio_saved_panels` call answered; the bridge's `pong` carried the new version.

Verified on the dev gateway with the `release` branch served over smart HTTP on loopback: added as a source, listed with `subdirectory: apps/factorycrew`, installed after the trust dialog, then `Update` took it from 0.3.2 to 0.3.3 and the status page reported 0.3.3.

The app e2e runs the real client (`tools/ui_dev.py`) with the built app's mod against the app's bridge on the worktree slot's ports (docs/worktrees.md), and asks through the window's own handlers. `tests/app_e2e/test_app_e2e.py`: 7 chests marked, follow-up from context, the trash button, a Russian question answered in Russian, the app's own MCP server, progress during a long build, the nearest oil cluster, consent before changing entities. `test_uncoded.py`: tasks no code was written for (lamp, trains, power, rates, belt). `test_atlas_planted.py`, `test_atlas_events.py`, `test_atlas.py` (the last needs the fixture save, `FC_UI_SAVE`/`FC_UI_MODS`): the atlas. The whole suite takes ~20 min and a few dozen agent turns; `-k <name>` runs one test.

## UI loop (screenshots of the real window)

A real 2.0.77 client from the copy in `/Users/Shared/factorio.app`, with its own data dir and a dev bridge on ports 34298-34300, so it never clashes with a game played on the same Mac (the app uses 34198-34200):

```bash
tools/ui_dev.py start                                   # deploy mod, start bridge + game, wait until online
tools/ui_dev.py shot window open 'reply:General=Hello'  # drive the window, then screenshot with GUI
tools/ui_dev.py restart                                 # after a mod change
tools/ui_dev.py stop
```

Screenshots land in `~/.factorycrew-dev/factorio/script-output/kc/`. The full step list is in the script's docstring.

## Playing

Players install the Kiro Crew app (see "Releases and updates" above): it runs the bridge, the dispatcher and Kira, and copies the mod into Factorio's mods folder. Start Factorio with the launch option `--enable-lua-udp 34200`. If something does not answer, the app's status page (sidebar → Factorio) says which piece is down and what to do; Factorio's own log is `~/Library/Application Support/factorio/factorio-current.log` (`grep -iE "kc-companion|udp"`).

### Live panels

Ask Kira for a panel ("show me science production with a time filter") and she writes a small window the mod keeps up to date by itself. Every working panel is saved as a Kiro Crew artifact (tag `factorio-panel`, one library per run). The **Library** button in Kira's window lists the run's saved panels and opens or deletes them directly, without a model turn; Kira can also find and restore one ("bring back my science panel").

**Not safe by design.** A panel is Lua code that runs inside the game on every refresh, and a saved panel is that code stored in an artifact. Whoever can edit the artifacts of your Kiro Crew (or reach the bridge port) can run any Lua in your game: change or delete things in the save, or hang the game with an endless loop. This is fine for a single-player game on your own machine; do not open panels from artifacts you did not make, and do not use this on a multiplayer server you care about.
