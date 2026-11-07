# Dev stands and test harness

Starts private copies of the whole stack (game, bridge, gateway, Kira, dispatcher) for development, and runs the four test levels locally and in CI. The contract for many worktrees on one host (ports, homes, `stand.sh check`, slots, host requirements) is [docs/worktrees.md](../worktrees.md); this doc does not repeat it.

## Main files

- `scripts/stand.sh`: one stand per worktree: `up`, `claim`, `check`, `env`, `status`, `ui`, `kc`, `down`, `release`. Picks a slot by `mkdir` claim plus port ownership.
- `scripts/kc.sh`: the factorycrew Kiro Crew gateway (default :5477, `~/.kirocrew-factorio`; `FC_KC_PORT`/`FC_KC_HOME` override). `install` writes the agent and the dispatcher; any other subcommand goes to `kirocrew`. The gateway itself starts with `--port` and no `KIROCREW_PORT` in its env (`GATEWAY`), as the desktop app starts a player's: the inherited variable once hid that crons dialed the default port.
- `tools/ui_dev.py`: a real client (`FACTORIO_APP`: the 2.0.77 one by default, `/Users/Shared/factorio-2.1.app` for 2.1.24) plus a bridge for one data dir. It writes the client's line into the deployed mod's `info.json` and keeps one dev save per line, `ui-test.zip` for 2.0 and `ui-test-<line>.zip` otherwise, since a save a 2.1 client wrote does not load in 2.0 (`tools/ui_dev.py:52-74`, `:176-180`): `start`, `stop`, `restart`, `shot`, `lua`, `steps`, `digest`. Writes `bridge.sha` and `game-mod.sha` for `stand.sh check`.
- `scripts/check.sh`: the fast check: `unit`, `level1-2.0`, `level1-2.1`, `level2`; logs in `.test-data/check/<step>.log`.
- `scripts/test-unit.sh`, `test-mod.sh`, `test-mod-2.1.sh`, `test-integration.sh`, `test-e2e.sh`: the levels. `test-app-e2e.sh` belongs to [the app](app.md).
- `scripts/lib/docker.sh`: `docker_alive`, `docker_ok` (a probe container), `docker_bounded` (a wall clock on `docker run`).
- `scripts/lib/checkout.sh`: `checkout_readable_by_gateway`.
- `scripts/lib/rcon.py`: an RCON client; its only caller is `scripts/atlas-fixture.sh:20`. There is no `rcon.sh`.
- `docker/Dockerfile`: the test image: `factoriotools/factorio` (2.0.77 by default, `FACTORIO_VERSION` build arg), FactorioTest built from source, `factorio-test-cli`, Python with Debian's pytest.
- `tests/e2e/stand.py`: the e2e container's entry: the level 2 `Stack` with the bridge on `0.0.0.0` and normal timeouts, prints `STAND_READY`.
- `tests/e2e/watch.py`: `wait_reply`, the watchdog every e2e reply wait goes through.
- `.github/workflows/check.yml`: CI on every push and PR: a `unit` job and a matrix of the three game steps; uploads `.test-data/` on failure.
- `.github/workflows/factory.yml`: the factory's jobs on every pull request: `factory-tier`, `factory-docs` and the two AI reviews; its scripts come from `factory/` on the default branch ([AGENTS.md](../../AGENTS.md), "Factory rules"; `factory.yml`).
- `.github/pull_request_template.md`: the pull request body the factory reads: `Fixes #<n>`, Repro, Fix, Proof.
- `scripts/services.sh`, `scripts/deploy-mod.sh`: the player install of the retired `gamer` setup (launchd agents `dev.factorycrew.bridge`/`.gateway`; mod copy to `/Users/Shared/kc-companion`). Both refuse to run in a linked worktree.

## Talks to

- Uses: [Bridge](bridge.md), started as `/usr/bin/python3 bridge/factorio_bridge.py --udp-port --game-port --http-port` (`tools/ui_dev.py:188-193`), over HTTP `/health`, `/query`, `/reply` (`tools/ui_dev.py:55-74`, `:247`).
- Uses: [Mod](mod-core.md), copied into the data dir with `M.BRIDGE_PORT = 34199` in `mod/kc-companion/scripts/protocol.lua` rewritten to the stand's port (`tools/ui_dev.py:141-150`), and driven by `remote.call("kc", "ui_open|ui_close|ui_click|ui_confirm|ui_inject")` ([window](window.md); `tools/ui_dev.py:232-256`).
- Uses: [Kira's agent](kira-agent.md) and [MCP server](mcp-server.md): `kc.sh install` fills `__ROOT__` and the bridge port into `agent/factorio-companion.json` (`scripts/kc.sh:44-45`), which starts the MCP server with `--bridge` (`agent/factorio-companion.json`, `mcpServers`).
- Uses: Dispatcher: `kc.sh install` copies it to `<home>/crons/` only when it changed (`scripts/kc.sh:47-54`); `stand.sh up` and `test-e2e.sh` register cron `factorio-dispatch` with `{"bridge": ...}` (`scripts/stand.sh:194-198`, `scripts/test-e2e.sh:26-30`).
- Uses: the level 2 harness `tests/integration/conftest.py` (`Stack`, `Rcon`) (`tests/e2e/stand.py:12-20`, `tests/e2e/test_e2e.py:28`).
- Uses: the `kirocrew` CLI: `gateway`, `setup --agent-only`, `stop`, `cron`, `config` (`scripts/kc.sh:61-90`).
- Used by: [the app](app.md): `test-app-e2e.sh` calls `stand.sh claim`, `app-dev.sh` (which calls `kc.sh`), `tools/ui_dev.py`, and `watch.wait_reply` (`scripts/test-app-e2e.sh`, `tests/app_e2e/test_app_e2e.py:26`).

## Must stay true

- Each worktree uses only its own stand; the rules and what enforces them are in [docs/worktrees.md, What is yours](../worktrees.md#what-is-yours). The refusals live in `scripts/kc.sh:13-19`, `tools/ui_dev.py` (`main`), `scripts/services.sh:12-18`, `scripts/deploy-mod.sh:6-12`.
- `stand.sh` counts a port holder as its own only by `KIROCREW_HOME` in the process env, this checkout's bridge path, or this checkout's data dirs (`scripts/stand.sh:49-61`). A new kind of process the stand starts must match one of these, or `up` reads the stand's own ports as taken (inferred from `ports_free`, `scripts/stand.sh:63-70`).
- The agent is installed before the gateway starts: a gateway sees only the agents present at its start (`scripts/stand.sh:191`, `scripts/test-e2e.sh:23`).
- `kc.sh` runs `kirocrew` under `env -i` with a fixed variable list, and `KIRO_HOME` is exactly `$KIROCREW_HOME/kiro` (`scripts/kc.sh:7-9`, `:31-36`).
- `check.sh` runs every step even after a failure and exits non-zero if any failed; it is plain bash 3.2 (`scripts/check.sh:7-12`, `:26-42`). CI runs the same scripts (`.github/workflows/check.yml:1-2`, `:26`, `:34-45`).
- `test-unit.sh` with only options still runs `tests/unit`, not `pytest.ini`'s `testpaths = tests`, which would collect integration and e2e (`scripts/test-unit.sh:12-14`, `pytest.ini`).
- Level 1 starts from a mod list with the DLC mods off (`scripts/test-mod.sh:20-23`, `scripts/test-mod-2.1.sh:21`). On 2.1 only copies inside the container are patched to `factorio_version` 2.1 and `helpers.create_profiler`; the repo's mod stays at 2.0 (`scripts/test-mod-2.1.sh:2-3`, `:22-25`).
- Every Docker call has a wall clock (`timeout -k`), and a stuck daemon exits 3 with a message (`scripts/lib/docker.sh`).
- The e2e container publishes the bridge and RCON on host `127.0.0.1` only: the bridge API has no auth (`scripts/test-e2e.sh:64-69`).
- The watchdog names the stuck link: game, ack, pickup, chat, failed turn, dead MCP server, silence, bridge gave up (`tests/unit/test_e2e_watch.py`, one test per link).

## Easy to break

- A running bridge, game or MCP server keeps the code it started with. `stand.sh check` compares `bridge.sha`, `game-mod.sha` and MCP start times with the checkout (`scripts/stand.sh:132-175`). Fix with `stand.sh ui restart`, or `stand.sh kc stop` then `up` for MCP.
- Copying an identical dispatcher would restart the running wake, so `kc.sh` copies only on change (`scripts/kc.sh:47-48`). The e2e scripts wait up to 90 s for a wake newer than the installed script (`scripts/test-e2e.sh:31-53`).
- `stand.sh check` uses macOS `stat -f %m` and parses `ps -o lstart` (`scripts/stand.sh:164`, `:169`); on a GNU userland that line misreads (inferred: GNU `stat -f` means file-system status).
- The scripts build `factorycrew-test:<version>` only when it is missing (`scripts/test-mod.sh:9-11`, `scripts/test-integration.sh:14-16`). After a `docker/Dockerfile` change, remove the image locally or the old one runs; CI starts from no image (`.github/workflows/check.yml:44`).
- A stuck Docker VM leaves `docker run` in "Created" and ignores SIGTERM, hence `timeout -k` (`scripts/lib/docker.sh:1-11`).
- A player's mods and settings left in a data dir by an `FC_UI_MODS` run keep the plain dev save from coming online; plain mode deletes them and never creates the dev save while a player's mods are in place (`tools/ui_dev.py:152-170`).
- A checkout under `~/.kiro/crew` or `~/.kirocrew` makes Kira's MCP server die in the gateway's sandbox. `stand.sh up` and both e2e scripts refuse it (`scripts/lib/checkout.sh`), and the watchdog names it (`tests/e2e/watch.py:141-144`).
- Starting a gateway from inside another Kiro Crew session would inherit its `TMPDIR` and session vars, and the cron sandbox then refuses the scripts (`scripts/kc.sh:28-30`).
- The e2e scripts call `uv run --no-project --with pytest==8.3.5` without `--python 3.12` (`scripts/test-e2e.sh:84`, `scripts/test-app-e2e.sh:58`), so `tests/e2e` runs on uv's default Python; level 2 runs Debian's pytest in the image (`docker/Dockerfile:21`, `scripts/test-integration.sh:20`).
- `stand.py` overrides the level 2 bridge timeouts (`--offline-after 3` etc.) with normal ones (`tests/e2e/stand.py:15`, `tests/integration/conftest.py:30`); an e2e on level 2 timeouts would mark the game offline after 3 s (inferred).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_e2e_watch.py`: the watchdog, 12 tests, ~3 s.
- `./scripts/check.sh`: ~105 s, after a change to `check.sh`, a `test-*.sh` level script, `scripts/lib/docker.sh` or `docker/Dockerfile`. CI runs the same on push (`.github/workflows/check.yml`).
- `./scripts/stand.sh up` then `./scripts/stand.sh check`: every line `ok`, after a change to `stand.sh`, `kc.sh` or `tools/ui_dev.py`.
- `./scripts/test-e2e.sh`: ~4 min, real model, after a change to `test-e2e.sh`, `tests/e2e/stand.py`, `tests/e2e/watch.py` or `kc.sh`.
- `./scripts/test-app-e2e.sh -k <name>`: after a change to `tools/ui_dev.py` options the app e2e uses.
- By hand: `tools/ui_dev.py shot <name> open` and look at the screenshot it prints.

## Decisions

- Each worktree brings up and checks only its own copy of the stack: [docs/worktrees.md](../worktrees.md), intro and "What is yours" (the incident there is the reason).
- Levels 1 and 2 run in a `linux/amd64` image: the `arm64` image runs the x64 binary through box64 and fails ([SPEC, Tests](../../SPEC.md#tests); Decided, "Headless Factorio in Docker on Apple Silicon").
- FactorioTest is built from source at `cli-v3.5.1`: the portal copy needs a Factorio login, and from 3.6.0 the mod needs 2.1 ([SPEC, Tests](../../SPEC.md#tests)).
- Level 2 runs on 2.1.24: headless 2.0 aborts on the first UDP packet ([SPEC, Decided](../../SPEC.md#decided), "Game version").
- The UI loop uses a real client: headless renders no screenshots ([SPEC, Decided](../../SPEC.md#decided), "Screenshots for UI work").
- Players install the Kiro Crew app and the `gamer` setup is retired, so `services.sh`, `deploy-mod.sh` and the `gamer` lines in `tools/ui_dev.py`'s docstring describe it: confirmed by the repository owner, [decisions.md](../decisions.md#players-install-the-kiro-crew-app-the-second-account-setup-is-retired).

## Terms

- **stand**: one private copy of game, bridge, gateway, Kira and dispatcher cron for one checkout ([docs/worktrees.md](../worktrees.md)).
- **slot / claim**: the port block a stand owns, and the `~/.factorycrew-stands/slot-N/owner` dir that reserves it (`scripts/stand.sh:16-23`).
- **level 1 / 2 / 3**: mod logic under FactorioTest; mod + bridge + MCP without Kiro Crew (integration); the full cycle through Kiro Crew (e2e). Only level 3 and the app e2e are e2e ([SPEC, Tests](../../SPEC.md#tests)).
- **fast check**: `scripts/check.sh`, everything without Kiro Crew, a model or a game window (`scripts/check.sh:2`).
- **wake**: one run of the dispatcher cron, which compiles the script once and lives up to an hour (`scripts/test-e2e.sh:31-33`).
- **UI loop**: `tools/ui_dev.py` driving and screenshotting the real window (README, "UI loop").
