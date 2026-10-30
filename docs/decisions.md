# Decisions

Product and design decisions a change must not undo by accident. Each says why and where it was made. Design decisions with measurements that belong to one area stay in that area's doc ("Decisions") or in [SPEC.md, "Decided"](../SPEC.md#decided).

## Confirmed by Michael on 2026-10-29

Confirmed in the Kiro Crew chat that reviewed this repository's agent readiness (answers to its questions 2 and 3).

- **The code wins.** When SPEC.md, README and the code disagree, the code is right and the docs are fixed.
- **`factorio_query` runs arbitrary Lua; read-only is an agent rule, not a sandbox.** A sandbox may come later with writes behind confirmation. [SPEC.md, "Agent tools"](../SPEC.md#agent-tools-mcp-factorio)
- **Kira's agent spec pins no model.** It stays on auto so the service stays cheap for players; Kira is made robust for weak models instead. `agent/factorio-companion.json` has no `model` field.
- **Existing entities change only after the player's consent** for that exact change, described first (what, how many, where) and marked on the map. [agent/factorio-companion.md, "Changing existing things (consent first)"](../agent/factorio-companion.md)
- **Players install the Kiro Crew app.** The setup with a second macOS account (`gamer`) and a symlinked mod is retired; `scripts/deploy-mod.sh` and `scripts/services.sh` remain only for a hand-made setup from the main checkout.

## Written in the repository with their reasons

- **The run is a `run_id` in `storage`, its name comes from the player**: the Lua API has no save name. [SPEC.md, "What a run is"](../SPEC.md#what-a-run-is)
- **One chat per run and tab, keyed by `run_id` + tab**, so "Logistics" of two runs never mix. [SPEC.md, "Decided"](../SPEC.md#decided)
- **Level 2 runs on headless 2.1.23**: a 2.0 headless server crashes on the first UDP packet. [SPEC.md, "Tests"](../SPEC.md#tests)
- **The mod ships inside the app**, versioned with it, never from the mod portal. [README, "Releases and updates"](../README.md#releases-and-updates)
- **Each worktree runs its own stack and checks only that stack.** [docs/worktrees.md](worktrees.md)
- **The atlas dump is hand-built text, structure is cached by dirty chunk, state is read live.** [docs/atlas.md, "Reading the base without stalling the game"](atlas.md#reading-the-base-without-stalling-the-game)
- **Building is ghosts only, powered in the same turn with the best researched poles, nearest cluster by default, tried on a hidden copy before "done".** [agent/factorio-companion.md](../agent/factorio-companion.md), "Building", "Done means it works", "Resources"
- **Private Kiro Crew `_post` calls for nested folders and chat slots** until the public API has them. [SPEC.md, "Open questions"](../SPEC.md#open-questions)
