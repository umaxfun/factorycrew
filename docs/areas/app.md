# Kiro Crew app and releases

Packs the mod, the bridge, the MCP server, the dispatcher and Kira into one Kiro Crew app that a player installs, and ships new versions through the `release` branch. Player-facing steps are in README, [Kiro Crew app](../../README.md#kiro-crew-app-the-redistributable-package) and [Releases and updates](../../README.md#releases-and-updates).

## Main files

- `app/app.json`: the manifest. Name `factorycrew`, `version` (the one version of app and mod), agent, MCP server `factorio` (`python3 mcp/factorio_mcp.py`), backend `backend/server.py` with `/health`, cron `factorio-dispatch` every 30 s, the sidebar page `/apps/factorycrew` ("Factorio"), API permissions `/apps/factorycrew/api/*`, skill `skills/belts`.
- `app/backend/server.py`: the backend the gateway starts. Runs the bridge in-process (`fb.serve`) on the ports from `<app>/data/config.json`, answers `/health`, `/api/status` and `GET`/`POST /api/settings` (the error-report switch `telemetry`, see [Error and bug reports](../sentry.md)) on `$PORT`, and copies the mod into Factorio's mods folder at start (`install_mod`).
- `app/backend/status.py`: the checks behind `/api/status`: Kiro Crew version, bridge, game, mod, dispatcher, MCP server, Kira's agent. Each is `{id, label, state: ok|warn|fail|idle, detail, fix?}`.
- `app/ui/index.mjs`: the status page. Plain ESM with React from the host, no build step; polls `/apps/factorycrew/api/status` every 3 s.
- `scripts/build-app.sh`: builds `dist/factorycrew/` and `dist/factorycrew-<version>.zip`, writes the app's agent file, stamps the mod's version, copies `report/` and stamps its Sentry environment (`FC_REPORT_ENV`, default `dev-<host>`; `release.sh` passes `prod`).
- `scripts/release.sh`: `patch|minor|major|X.Y.Z|current [--push]`: bumps `app/app.json`, commits and tags `vX.Y.Z` on `main`, builds, commits the build to `release` as `apps/factorycrew/`.
- `scripts/app-dev.sh`: build, reinstall, trust, enable on a dev gateway (`FC_APP_PORT`, default 5479; `FC_APP_HOME`, default `~/.kirocrew-factorio-app`). `app-dev.sh ports H U G` writes `data/config.json`.
- `scripts/test-app-e2e.sh`, `tests/app_e2e/`: the app e2e on the real 2.0.77 client.
- The `release` branch: builds only, `README.md` plus `apps/factorycrew/` (`git ls-tree --name-only release`).

## Talks to

- Uses: [Bridge](bridge.md), through `import factorio_bridge as fb`, `fb.Settings`, `fb.serve` (`app/backend/server.py:26`, `:119-126`), and its `/health` fields `game_online`, `last_seen_s`, `run_name`, `tabs`, `queued_asks`, `leased_asks`, `dispatcher_seen_s`, `agent_seen_s` (`app/backend/status.py:127-201`, `bridge/factorio_bridge.py:534`).
- Uses: [MCP server](mcp-server.md), shipped as `mcp/factorio_mcp.py`, `kc_compute.py` and `kc/` (`scripts/build-app.sh:18-19`). With no `--bridge` and no `FACTORIO_BRIDGE_URL` it takes the port from `<app>/data/config.json` (`mcp/factorio_mcp.py:21-35`). The status page starts it and asks `tools/list` (`app/backend/status.py:72-100`).
- Uses: Dispatcher, shipped as the app cron `dispatcher/factorio_dispatch.py:run` (`app/app.json`, `crons`). It reads `http_port`, `agent`, `state_file` from `data/config.json` (`dispatcher/factorio_dispatch.py:511-525`). Each reinstall gives the cron a new id, which the dispatcher handles ([SPEC, How it works](../../SPEC.md#how-it-works), step 3; `tests/app_e2e/test_app_e2e.py::test_tab_from_an_earlier_install_still_answers`).
- Uses: [Kira's agent](kira-agent.md), `agent/factorio-companion.json` + `.md` folded into one prompt with tools `@factorycrew:factorio` (`scripts/build-app.sh:25-42`).
- Uses: [Mod](mod-core.md), `mod/kc-companion/` without `tests` and dotfiles, and `skills/` (`scripts/build-app.sh:22-23`).
- Uses: the Kiro Crew gateway: `kirocrew app install|uninstall|info|enable`, `config set agent.apps_trusted` and `agent.session_control` (`scripts/app-dev.sh:23-31`). The gateway proxies `/apps/factorycrew/api/status` to the backend (`app/ui/index.mjs:2`) (inferred: gateway code is not in this repo).
- Uses: Dev stands, through `stand.sh claim`, `tools/ui_dev.py` with `FC_UI_EXTERNAL_BRIDGE=1` and `FC_UI_MOD_SRC=dist/factorycrew/mod/kc-companion`, and `tests/e2e/watch.py::wait_reply` (`scripts/test-app-e2e.sh:13`, `:46-50`; `tests/app_e2e/test_app_e2e.py:26`). See [Dev stands and test harness](dev-stand.md).
- Used by: players, who add the `release` branch as an app source (README, "Releases and updates").

## Must stay true

- One version: the build writes `app/app.json`'s version into the shipped mod's `info.json` (`scripts/build-app.sh:44-51`); the repo's `mod/kc-companion/info.json` keeps its own (0.2.0).
- The backend replaces only a `kc-companion` folder it installed (one holding `.factorycrew-managed`, not a symlink) and leaves any other alone (`app/backend/server.py:61-62`; `tests/unit/test_app_status.py::test_a_mod_folder_the_app_did_not_put_there_is_left_alone`).
- It reinstalls when the shipped version differs from the folder's `info.json` or its mark (`app/backend/server.py:63-73`; `tests/unit/test_app_status.py::test_app_update_replaces_the_mod_it_installed`).
- A failed mod copy or a failing status check does not take the bridge or the backend down (`app/backend/server.py:102-103`, `:113-118`).
- `/health` binds before the bridge starts, because the gateway reads a child that dies on its first bind as a port collision (`app/backend/server.py:122-123`).
- `<app>/data/config.json` is the one source of ports for backend, MCP server and dispatcher; defaults 34198/34199/34200 match the mod (`app/backend/server.py:10-11`, `:119-120`).
- Overall state is `fail` if any check fails, else `warn` if any warns (`app/backend/status.py:203-210`). Bridge down makes game and dispatcher `idle` (`tests/unit/test_app_status.py::test_bridge_down_marks_what_depends_on_it_unknown`). Kiro Crew below 0.11 and an agent without a Factorio MCP server are `fail` (`::test_old_kirocrew_and_an_agent_without_tools_are_named`). A dispatcher silent for more than 60 s is `fail` (`::test_offline_game_and_a_silent_dispatcher`).
- The shipped MCP server lists `factorio_query` and `factorio_reply` (`app/backend/status.py:20`; `tests/unit/test_app_status.py::test_mcp_probe_starts_the_real_server`).
- The app's turns go through the app's own MCP server, `@factorycrew:factorio/...` (`tests/app_e2e/test_app_e2e.py::test_it_was_the_apps_agent`).
- `release.sh` runs only on `main` with a clean tree, refuses a tag that exists, replaces the whole `release` tree with the new build, and pushes only with `--push` (`scripts/release.sh:24-25`, `:36`, `:63-66`, `:82-87`).
- The Mod check warns while the online game runs another mod version than the mods folder holds (a game whose mod reports none is older than 0.3.7), with "Restart Factorio to load the updated mod." (`tests/unit/test_app_status.py::test_a_game_still_running_the_old_mod_asks_for_a_restart`).
- An update needs no Kiro Crew restart: the backend (bridge, status) is restarted by Kiro Crew, the dispatcher's wake exits on a changed file, Kira's MCP servers re-execute themselves ([MCP server](mcp-server.md)), and only Factorio has to be restarted by the player (README, "Releases and updates").
- The "Panel library" check lists `factorio-panel` artifacts with the same CLI call Kira's saves use (`probe_library`, cached a minute) and fails the page when that call fails (`tests/unit/test_app_status.py::test_a_library_the_cli_cannot_reach_fails_the_page`). Before it, panels went unsaved for days with every check green.
- A bridge that cannot take its ports (the app in a second Kiro Crew on the same machine, same ports) does not kill the backend: the status page stays up, the Bridge check fails with the bind error and what to do, and the game and dispatcher checks stay "unknown" instead of reading the other copy's bridge (`run_bridge` in `app/backend/server.py`; `tests/unit/test_app_status.py::test_the_backend_survives_a_taken_bridge_port`, `::test_a_bridge_that_lost_its_port_is_named_and_the_other_one_is_not_read`; checked on two dev gateways on 2026-11-01).

## Easy to break

- `build-app.sh` copies an explicit file list (`scripts/build-app.sh:13-23`). A new module the MCP server or backend imports works from the repo and is missing from the build; the status page then shows the MCP check as `fail` "started without ..." (`app/backend/status.py:181-184`).
- `server.py` and `status.py` find the bridge and the MCP server by the build's layout, `bridge/` and `mcp/` beside `backend/` (`app/backend/server.py:23-26`, `app/backend/status.py:17`, `:77`). The unit test puts the repo's `bridge/` on `sys.path` to match (`tests/unit/test_app_status.py:115`).
- The build drops the sentence "Follow the rules in factorio-companion.md. " from the agent's prompt by exact string match (`scripts/build-app.sh:36`). If that sentence changes, the shipped prompt points at a file the app does not have.
- The agent check reads `<home>/kiro/agents/factorycrew--factorio-companion.json` (or `~/.kiro/agents` for the default home) (`app/backend/status.py:38-43`, `:103-111`). If the gateway names the file differently, the check fails with "not registered" (inferred: the file name is the gateway's convention).
- The Kiro Crew version comes only from the `Info.plist` of a `.app` around `sys.executable`; anywhere else it is "unknown" and the check passes (`app/backend/status.py:46-56`, `:120-125`).
- pytest 7 (the test image's Debian `python3-pytest`) runs a module-level function named `setup` as module setup, so a helper in `tests/unit/test_app_status.py` must not use that name (`docker/Dockerfile:21`; `git log --format=%s -- tests/unit/test_app_status.py`).
- A reinstall drops trust; `app-dev.sh` grants it again (`scripts/app-dev.sh:4`, `:30`).
- A dispatcher wake compiled the old script; the app e2e waits for a wake that started after the install (`scripts/test-app-e2e.sh:23-43`).
- `test-app-e2e.sh` puts its arguments after `tests/app_e2e`, so a file path adds to the whole directory instead of narrowing it. Narrow with `-k` (`scripts/test-app-e2e.sh:58`).
- A checkout under `~/.kiro/crew` or `~/.kirocrew` is refused: the gateway's sandbox cannot read it (`scripts/lib/checkout.sh`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_app_status.py`: status checks and mod install/update, 8 tests, ~4 s.
- `./scripts/build-app.sh`: then look at `dist/factorycrew/agents/factorio-companion.json` and `dist/factorycrew/mod/kc-companion/info.json` (version = app's).
- `./scripts/app-dev.sh`, then the dev gateway's sidebar, Factorio: every row OK while a game runs.
- `./scripts/test-app-e2e.sh [-k <name>]`: real client, real model, ~20 min for all of `tests/app_e2e`. `test_app_e2e.py` covers the app itself; `test_atlas*.py` and `test_uncoded.py` reuse its harness, and `test_atlas.py` skips unless its fixture save is loaded (`tests/app_e2e/test_atlas.py:1-8`).
- Releases: cut from `main` only; check the result with `git ls-tree --name-only release apps/factorycrew`. The update path through a branch source is checked by hand as in README, "Releases and updates".

## Decisions

- Players install the Kiro Crew app; the old `gamer`-account setup is retired: confirmed by Michael, 2026-10-29 (README, "Playing").
- The agent spec pins no model, left on auto so it is cheap for players: confirmed by Michael, 2026-10-29. `scripts/build-app.sh:33-40` writes no `model`.
- When SPEC, README and code disagree, the code wins: confirmed by Michael, 2026-10-29.

## Terms

- **`<app>`**: `<KIROCREW_HOME>/apps/factorycrew`, where the gateway installs the build (`app/backend/status.py:17`); its `data/` survives a reinstall (`scripts/app-dev.sh:3-4`).
- **managed mark**: `.factorycrew-managed` in the mods-folder copy, holding the version the app installed (`app/backend/server.py:33`, `:72`).
- **release branch**: a Kiro Crew app registry holding only the last build (`scripts/release.sh:9-10`, `:53`).
- **trust**: listing the app in `agent.apps_trusted`, which third-party apps need (README, "Kiro Crew app"; `scripts/app-dev.sh:30`).
- **dev gateway**: the gateway `app-dev.sh` installs into, never the player's (`scripts/app-dev.sh:2`).
