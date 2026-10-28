# factorycrew

Ask Kiro Crew about the current Factorio run from an in-game window. Design: [SPEC.md](SPEC.md).

## Layout

| Path | What |
|---|---|
| `mod/kc-companion/` | Factorio 2.0 mod: in-game window (Ctrl+Shift+K), query execution, map marks, tabs, UDP to the bridge |
| `bridge/factorio_bridge.py` | Bridge: UDP to the game, HTTP API on 127.0.0.1:34198 for the dispatcher and the MCP server |
| `mcp/factorio_mcp.py` | MCP server `factorio` (stdio): `factorio_query`, `_mark`, `_unmark`, `_list_marks`, `_reply` |
| `docker/Dockerfile` | Test image: headless Factorio + FactorioTest (built from source, no portal login) + Python |
| `dispatcher/factorio_dispatch.py` | Script-cron: asks from the bridge -> chats `Factorio/<run>/<tab>` |
| `agent/` | Agent template `factorio-companion` and its rules |
| `scripts/kc.sh` | The separate Kiro Crew gateway for this project (port 5477, `~/.kirocrew-factorio`) |
| `tests/integration/` | Level 2: mod + bridge + MCP |
| `tests/e2e/` | Level 3: the full cycle through the factorycrew gateway |

All Python is stdlib only (tests use pytest).

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

Three levels (SPEC.md, "Tests"). Only level 3 is end to end.

| Level | What | Command | Factorio |
|---|---|---|---|
| 1 | mod logic under FactorioTest | `./scripts/test-mod.sh` | headless 2.0.77 |
| 2 | integration: mod + bridge + MCP, test plays dispatcher and agent | `./scripts/test-integration.sh` | headless 2.1.23 |
| 3 | e2e: game → bridge → dispatcher → Kiro Crew chat → agent → game | `./scripts/test-e2e.sh` | headless 2.1.23 + factorycrew gateway; live game 2.0.77 by hand |

Level 2 is on 2.1 because a 2.0 headless server aborts on the first UDP packet the mod receives ([forum report](https://forums.factorio.com/viewtopic.php?t=134790), fixed in 2.1). Each image is built on first use (~1 min); runs take ~10 s and ~20 s. Logs of the last level 2 run: `.test-data/integration/`.

Level 3 starts the factorycrew gateway if needed, installs the agent and dispatcher, registers the cron once, and runs a fresh game stand in Docker (bridge HTTP and RCON published on 127.0.0.1 only). It uses a real model, 5 agent turns per run, and checks that Kira answers in the player's language (one question in Russian).

## Kiro Crew app (the redistributable package)

`scripts/build-app.sh` builds `dist/factorycrew/` (+ zip): the mod, the bridge run by the app backend, the MCP server, the dispatcher as the app's cron, and the Kira agent. A player installs it with `kirocrew app install <dir>`, trusts it (third-party apps need `agent.apps_trusted`), and toggles it with enable/disable.

```bash
./scripts/app-dev.sh          # build + reinstall + trust + enable on a dev gateway :5479 (~/.kirocrew-factorio-app)
./scripts/test-app-e2e.sh     # app e2e on the real 2.0.77 client, no Docker
```

The app e2e runs the real client (`tools/ui_dev.py`) with the built app's mod against the app's bridge on the dev ports 34298-34300, and asks through the window's own handlers: 7 chests marked, follow-up from context, the trash button, a Russian question answered in Russian, and the turns used the app's own MCP server. ~50 s, 4 agent turns.

## UI loop (screenshots of the real window)

A real 2.0.77 client under `umaxfun`, from the player's copy `/Users/Shared/factorio.app`, with its own data dir and a dev bridge on ports 34298-34300, so it never clashes with the game under `gamer`:

```bash
tools/ui_dev.py start                                   # deploy mod, start bridge + game, wait until online
tools/ui_dev.py shot window open 'reply:General=Hello'  # drive the window, then screenshot with GUI
tools/ui_dev.py restart                                 # after a mod change
tools/ui_dev.py stop
```

Screenshots land in `~/.factorycrew-dev/factorio/script-output/kc/`. The full step list is in the script's docstring.

## Installing the mod for the player (account `gamer`)

As `umaxfun`, once and after every mod change:

```bash
./scripts/deploy-mod.sh        # -> /Users/Shared/kc-companion
./scripts/services.sh install  # bridge + factorycrew gateway in launchd (once)
./scripts/services.sh status
```

As `gamer`, once:

```bash
mkdir -p "$HOME/Library/Application Support/factorio/mods"
ln -s /Users/Shared/kc-companion "$HOME/Library/Application Support/factorio/mods/kc-companion"
```

Steam → Factorio → Properties → Launch Options: `--enable-lua-udp 34200`.

Diagnostics as `gamer`: `grep -iE "kc-companion|udp" "$HOME/Library/Application Support/factorio/factorio-current.log"`; copy the log to `/Users/Shared/` to share it. As `umaxfun`: `tail -f ~/.kirocrew-factorio/bridge.launchd.log` shows every question that reaches the bridge.
