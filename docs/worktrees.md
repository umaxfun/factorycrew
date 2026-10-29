# Several worktrees on one machine

Several sessions (people or agents) can work on factorycrew on the same host at once, each in its own git worktree. The host can be a laptop or a shared server where agents spin up worktrees by themselves. The rule is the same everywhere: **each worktree brings up its own copy of the whole stack and checks only that copy.**

A stand is one such copy: a Factorio client with its own data dir and save, a bridge, and a Kiro Crew gateway with Kira (agent, MCP server) and the dispatcher cron. All of it runs this checkout's code, on ports that `scripts/stand.sh` claimed for this worktree.

## The flow

```bash
git -C <main clone> worktree add -b <branch> <path> <base>
cd <path>
./scripts/stand.sh up        # claims a free slot, starts game + bridge + gateway + Kira
./scripts/stand.sh check     # every line must be "ok" before you trust a result
# ... work: edit, ./scripts/stand.sh ui restart, ask in the window, read Kira's chat ...
./scripts/stand.sh release   # stop everything, give the slot back
git -C <main clone> worktree remove <path>
```

`up` needs no slot number: it takes the first slot whose ports are all free and that nobody has claimed. To pin one, pass it: `./scripts/stand.sh up 3`. It is idempotent: on a stand that is already up it starts only what is missing. It does copy the mod, agent and dispatcher again. A running game keeps the mod it loaded at start, so after a mod change use `ui restart`; `check` reports a game that runs older code.

## What is yours

Only what `./scripts/stand.sh env` prints:

| | |
|---|---|
| `FC_STAND_SLOT` | your slot N |
| `FC_UI_HTTP_PORT` | your bridge (HTTP; UDP is +1 for the bridge, +2 for the game) |
| `FC_KC_PORT` | your Kiro Crew gateway |
| `FC_UI_HOME` | your Factorio data dir, save, screenshots, process logs |
| `FC_KC_HOME` | your gateway's home: Kira's agent, the dispatcher, chats |

Everything else on the host belongs to someone else: other ports, other homes, other gateways, a game you did not start. Do not read it as yours, do not restart it, do not add crons or agents to it, and do not point your dispatcher or Kira at it. A port that answers is not proof it is yours. The bridge, the gateway and the game all answer the same way whoever started them. On 2026-10-29 a worktree's dispatcher pointed at another session's bridge took that session's question. In the same incident, a second `ui_dev.py start` on a shared data dir replaced another session's game.

`up` enforces part of this. It refuses a slot when any of its four ports is held by a process that is not this checkout's stand: a gateway with another `KIROCREW_HOME`, a bridge from another checkout, a game with another data dir.

## Checking only your own stand

`./scripts/stand.sh check` verifies the chain end to end and only for this stand:

- your bridge answers and its game is online;
- the bridge process runs this checkout's `bridge/factorio_bridge.py`;
- a Lua query makes the round trip and the tick advances, so the game is not paused or sitting in a menu;
- the running game loaded this checkout's mod (a hash `tools/ui_dev.py` writes when it starts the game, against the checkout now), so an edit without `ui restart` shows up;
- the gateway on your port has your `KIROCREW_HOME`;
- Kira's MCP runs this checkout's server against your bridge;
- the installed dispatcher is this checkout's, and its cron polls your bridge.

Run it after `up`, after every restart, and before reporting a result from the stand. A green test against some other bridge or gateway proves nothing about your change.

## Day-to-day

```bash
./scripts/stand.sh status
./scripts/stand.sh ui shot window open           # UI loop against your game (tools/ui_dev.py steps)
./scripts/stand.sh ui restart                    # after a mod change (data.lua / control.lua)
./scripts/stand.sh ui lua 'return game.tick'
./scripts/stand.sh kc install                    # after an agent prompt or dispatcher change
./scripts/stand.sh kc token                      # your gateway's dashboard with a login token
./scripts/stand.sh kc cron list                  # any kirocrew subcommand against your gateway
./scripts/stand.sh down                          # stop, keep the slot and the data
eval "$(./scripts/stand.sh env)"                 # the exports above, for your own scripts
```

Kira's chats from your stand live in your gateway, under `Factorio/<run>/<tab>`.

## Tests

Unit tests, level 1 (`./scripts/test-mod.sh`, `./scripts/test-mod-2.1.sh`) and integration (`./scripts/test-integration.sh`) need no stand. Each run starts its own container with no host ports and writes to this worktree's `.test-data`, so any number can run at once.

`scripts/test-e2e.sh` and `scripts/test-app-e2e.sh` are not isolated yet. They use fixed host ports and default gateway homes: the e2e publishes 34198 and 27015, stops a host bridge on 34198 for the run, and uses `~/.kirocrew-factorio`; the app e2e uses 34298 and `~/.kirocrew-factorio-app`. Run them only when nobody else on the host uses those ports and homes. Better, move them onto `stand.sh env` before relying on them in parallel.

## Slots, claims, data

- Slot N (1..20 by default): bridge HTTP `34198 + 100·N`, gateway `5500 + N`. Slot 0 would be the default ports of a plain install, so it is never handed out. Override the bases and the count with `FC_STAND_BRIDGE_BASE`, `FC_STAND_GATEWAY_BASE`, `FC_STAND_MAX`.
- Claims live in `~/.factorycrew-stands/slot-N/owner` (override with `FC_STAND_DIR`), one per slot, holding the owner checkout's path. Claiming is a `mkdir`, so two agents starting at once cannot take the same slot. A claim whose checkout path no longer exists is stale and is reused. The worktree remembers its slot in `.stand` (git-ignored).
- Homes are named after the checkout's directory: `~/.factorycrew-dev-<dir>`, `~/.kirocrew-factorio-<dir>`. `release` keeps them, because they hold the save and Kira's chats. Delete them by hand when nothing in them is needed.

## Requirements on the host

- A Factorio client the stand can start: `FACTORIO_APP` (default `/Users/Shared/factorio.app`, the macOS Steam build). The UI loop needs a real client with a window. A headless Linux server has none. There the stand's game would have to be the headless server from `docker/`, and `tools/ui_dev.py` does not do that yet.
- `kirocrew` on `PATH`, logged in once for the user the agents run as. Each stand's gateway gets its own home, but they share that login.
- `python3`, `lsof`, `curl`, `rsync`.
