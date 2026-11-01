# factorycrew: start here

Kira, a Kiro Crew agent, answers a Factorio player's questions from a window inside the game. A Lua mod (`mod/kc-companion/`) talks UDP to a Python bridge (`bridge/`); a Kiro Crew script-cron (`dispatcher/`) moves questions into chats; Kira's MCP server (`mcp/`) gives her tools that act in the game through the bridge. Players get all of it as a Kiro Crew app (`app/`). Behaviour contract: [SPEC.md](SPEC.md). Architecture: [docs/overview.md](docs/overview.md).

## Commands

| What | Command | Time |
|---|---|---|
| Install | nothing to install: Python is stdlib only; the scripts need `uv`, Docker, `python3`, GNU `timeout` ([docs/worktrees.md, Requirements](docs/worktrees.md#requirements-on-the-host)) | |
| Fast check, before every commit (CI runs the same) | `./scripts/check.sh` | ~2 min |
| Python unit tests only | `./scripts/test-unit.sh [tests/unit/<file>.py] [-k <name>]` | ~20 s |
| Mod tests (level 1) | `./scripts/test-mod.sh`, `./scripts/test-mod-2.1.sh` | ~11 s each |
| Mod + bridge + MCP (level 2) | `./scripts/test-integration.sh [-k <name>]` | ~65 s |
| e2e through Kiro Crew, headless, real model | `./scripts/test-e2e.sh [-k <name>]` | ~4 min |
| App e2e, real game window, real model | `./scripts/test-app-e2e.sh [-k <name>]` | ~20 min |
| Start your own stack (game window, bridge, gateway, Kira) | `./scripts/stand.sh up`, then `./scripts/stand.sh check` | ~30 s |
| Build the app | `./scripts/build-app.sh` | 1 s |

Work in your own git worktree, next to the main clone (`<main clone>.worktrees/<name>`), never under `~/.kiro/crew`: the gateway's sandbox cannot read it there. Only what `./scripts/stand.sh env` prints is yours ([docs/worktrees.md](docs/worktrees.md)). The e2e scripts use a real model and open no window except `test-app-e2e.sh` and `stand.sh up`.

## Rules

- A change that alters an area's behaviour updates that area's doc in the same change.
- The code wins when SPEC, README and code disagree: fix the doc ([docs/decisions.md](docs/decisions.md)).
- Do not undo a decision in [docs/decisions.md](docs/decisions.md) without asking.
- Costly to get wrong: `scripts/release.sh --push` and the `release` branch (players update from it), `app/app.json` version, `mod/kc-companion/scripts/protocol.lua` together with `bridge/factorio_bridge.py` (both ends of the UDP protocol), the mod's `storage` layout (players' saves), dispatcher chat keys and cron identity (chats refuse a new cron), `agent/factorio-companion.json` (no `model` field, on purpose).

## Where things are

| Paths | Area doc |
|---|---|
| `mod/kc-companion/control.lua`, `mod/kc-companion/scripts/protocol.lua`, `mod/kc-companion/info.json` | [Mod core](docs/areas/mod-core.md) |
| `mod/kc-companion/scripts/gui.lua`, `mod/kc-companion/scripts/pointers.lua`, `mod/kc-companion/data.lua`, `mod/kc-companion/graphics/`, `mod/kc-companion/locale/` | [In-game window and pointers](docs/areas/window.md) |
| `mod/kc-companion/scripts/helpers.lua`, `mod/kc-companion/scripts/trial.lua` | [Build helpers and trial runs](docs/areas/build-helpers.md) |
| `mod/kc-companion/scripts/atlas.lua`, `mcp/kc/atlas.py`, `scripts/atlas-fixture.sh`, `tests/data/atlas/`, `tools/atlas_proto/` | [Atlas](docs/areas/atlas.md) |
| `mod/kc-companion/scripts/circuits.lua`, `mcp/kc/circuits.py` | [Circuits](docs/areas/circuits.md) |
| `mod/kc-companion/scripts/panels.lua`, panel tools in `mcp/factorio_mcp.py` | [Live panels](docs/areas/panels.md) |
| `bridge/` | [Bridge](docs/areas/bridge.md) |
| `dispatcher/` | [Dispatcher](docs/areas/dispatcher.md) |
| `mcp/factorio_mcp.py`, `mcp/kc_compute.py` | [MCP server](docs/areas/mcp-server.md) |
| `mcp/kc/` (except atlas, circuits) | [kc library](docs/areas/kc-library.md) |
| `skills/` | [Skills](docs/areas/skills.md) |
| `agent/` | [Kira, the agent](docs/areas/kira-agent.md) |
| `app/`, `scripts/build-app.sh`, `scripts/release.sh`, `scripts/app-dev.sh` | [Kiro Crew app and releases](docs/areas/app.md) |
| `scripts/` (stand, kc, test scripts, lib), `tools/ui_dev.py`, `docker/`, `tests/e2e/stand.py`, `tests/e2e/watch.py`, `.github/` | [Dev stands and test harness](docs/areas/dev-stand.md) |
| `report/` | [Error and bug reports](docs/sentry.md) |
| `tests/` | the area doc of the code each test checks |

The last readiness assessment: [docs/agent-readiness.md](docs/agent-readiness.md).
