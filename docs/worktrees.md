# Several worktrees on one machine

Two sessions (people or agents) often work on factorycrew at the same time, each in its own git worktree. Each worktree brings up its OWN copy of the whole stack: a Factorio client with its own data dir and save, a bridge, and a Kiro Crew gateway with Kira and the dispatcher cron, all running this checkout's code on their own ports. Nothing is shared, so one session's questions, restarts and mod deploys never reach another's.

## What already runs on this machine

Leave these alone from a worktree:

| What | Home / data | Ports | Who |
|---|---|---|---|
| The player's game and live Kira | `~/.kirocrew-factorio` (gateway), launchd `dev.factorycrew.bridge` / `dev.factorycrew.gateway` | gateway 5477, bridge 34198-34200 | the player, from the main checkout |
| Default dev client (`tools/ui_dev.py` without env) | `~/.factorycrew-dev` | bridge 34298-34300 | the main checkout, app e2e |
| App gateway (`scripts/app-dev.sh`, `test-app-e2e.sh`) | `~/.kirocrew-factorio-app` | gateway 5479, bridge 34298-34300 | the main checkout |

A busy port is someone else's until proven otherwise. Do not add crons or agents to a gateway you did not start, and do not run `tools/ui_dev.py restart` against `~/.factorycrew-dev` from a worktree. On 2026-10-29 a worktree's dispatcher pointed at the shared dev bridge took another session's question, and a second `ui_dev.py start` replaced the other session's game.

## Start a worktree with its own stand

```bash
git -C ~/prj/factorycrew worktree add -b my-branch ~/prj/factorycrew-my-branch HEAD
cd ~/prj/factorycrew-my-branch
./scripts/stand.sh up 3        # pick a free slot 1..9; it is remembered in .stand
```

`up` deploys this checkout's mod, starts the game and the bridge, starts the gateway, installs Kira (agent + dispatcher script from this checkout) and registers the dispatcher cron. It prints the ports. The first start of a new data dir creates a fresh save, so the map is empty.

Slot N gets:

| Part | Where |
|---|---|
| Factorio data dir, save, screenshots | `~/.factorycrew-dev-<dir>/factorio` (`<dir>` = the worktree's directory name) |
| Bridge HTTP / bridge UDP / game UDP | 34198+100N / +1 / +2 (slot 2: 34398, 34399, 34400) |
| Kiro Crew gateway | `http://127.0.0.1:5500+N`, home `~/.kirocrew-factorio-<dir>` |
| Kira | agent `factorio-companion` in that home, MCP pointed at this stand's bridge |

`up` refuses to start when the slot's gateway or bridge port is held by a process that is not this stand; pick another slot then. Slots in use right now: `cat ~/prj/factorycrew*/.stand`.

## Day-to-day

```bash
./scripts/stand.sh status                       # bridge health + gateway up/down
./scripts/stand.sh ui shot window open          # the UI loop against this stand's game
./scripts/stand.sh ui restart                   # after a mod change (data.lua / control.lua)
./scripts/stand.sh ui lua 'return game.tick'
./scripts/stand.sh kc install                   # after an agent prompt or dispatcher change
./scripts/stand.sh kc token                     # dashboard of this stand's gateway, with a login token
./scripts/stand.sh kc cron list                 # any kirocrew subcommand against this gateway
./scripts/stand.sh down                         # stop game, bridge and gateway
eval "$(./scripts/stand.sh env)"                # export FC_UI_HOME, FC_UI_HTTP_PORT, FC_KC_HOME, FC_KC_PORT, FC_BRIDGE_HTTP
```

Kira's chats from this stand live in this stand's gateway (`kc token`), under the folder `Factorio/<run>/<tab>`, not in the player's gateway.

Unit tests, level 1 (`./scripts/test-mod.sh`, `./scripts/test-mod-2.1.sh`) and integration (`./scripts/test-integration.sh`) need no stand. Each run starts its own container with no host ports and uses the worktree's `.test-data`, so they can run in parallel. `scripts/test-e2e.sh` is different: it publishes host ports 34198 and 27015, stops the player's bridge for the run and uses the default gateway on 5477. Run it from one worktree at a time, and not while the player is in the game.

## Removing a worktree

```bash
./scripts/stand.sh down
git -C ~/prj/factorycrew worktree remove ~/prj/factorycrew-my-branch
```

The stand's homes (`~/.factorycrew-dev-<dir>`, `~/.kirocrew-factorio-<dir>`) stay on disk. Delete them by hand once nothing in them is needed: they hold Kira's chats and the save.
