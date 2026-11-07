# Decisions

Product and design decisions a change must not undo by accident. Each entry names who decided, when, why, and where they said it on the forge. Entries are append-only: a change of mind is a new entry at the end with a `Supersedes:` line, written or quoted by a person; an existing entry is never edited or deleted, so `decisions.md:<line>` references stay valid. Code that does not match an entry is a bug, and the entry is its spec; a review finding, a failing test or an issue that collides with an entry is a request to change the decision, for the repository owner.

The reasons and measurements behind many entries are in [SPEC.md, "Decided"](../SPEC.md#decided) and [docs/sentry.md, "Decided"](sentry.md#decided). Design choices an area doc records under its own "Decisions" heading are entries only where they link here.

## When SPEC, README and the code disagree, the code wins and the docs are fixed

Decided by: the repository owner (Michael)
Date: 2026-10-29

- Why: (inferred: no reason was given with the answer)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Queries run arbitrary Lua with no sandbox; read-only is an agent rule

Decided by: the repository owner (Michael)
Date: 2026-10-29

- Why: (inferred: no reason was given with the answer). A sandbox may come later, with writes behind confirmation. [SPEC.md, "Agent tools"](../SPEC.md#agent-tools-mcp-factorio)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Kira's agent spec pins no model

Decided by: the repository owner (Michael)
Date: 2026-10-29

- Why: it stays on auto so the service stays cheap for players; Kira is made robust for weak models instead. `agent/factorio-companion.json` has no `model` field.
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Existing entities change only after the player's consent to that exact change

Decided by: the repository owner (Michael)
Date: 2026-10-29

- Why: (inferred: no reason was given with the answer). Kira first describes what, how many and where, and marks it on the map. [agent/factorio-companion.md, "Changing existing things (consent first)"](../agent/factorio-companion.md)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Players install the Kiro Crew app; the second-account setup is retired

Decided by: the repository owner (Michael)
Date: 2026-10-29

- Why: (inferred: no reason was given with the answer). The setup with a second macOS account (`gamer`) and a symlinked mod is retired; `scripts/deploy-mod.sh` and `scripts/services.sh` remain only for a hand-made setup from the main checkout.
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The game runs on the player's own machine, next to Kiro Crew, and everything talks over loopback

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: (inferred: SPEC gives none). Nothing goes over a network or a tunnel. [SPEC.md, "Decided"](../SPEC.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Queries take arbitrary Lua through load, with no fallback to primitives

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: `load` is available for text chunks in the mod runtime (Factorio Lua API docs, Libraries). [SPEC.md, "Decided"](../SPEC.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Achievements are turned off by adding the mod, not by code

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: with any mod, Steam achievements no longer count on that save; the code run through `load` disables nothing extra. [SPEC.md, "What to enable"](../SPEC.md#what-to-enable)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## One chat per run and tab, keyed by run_id and tab name, one folder per run

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: "Logistics" of two runs must never mix. [SPEC.md, "Decided"](../SPEC.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## A run is a run_id in storage, and its name comes from the player

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: the Lua API has no save name (checked in `runtime-api.json` 2.1.24). [SPEC.md, "What a run is"](../SPEC.md#what-a-run-is)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The mod declares Factorio 2.0; level 1 runs on headless 2.0.77, level 2 on headless 2.1.23

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: 2.0.77 is the player's game; a 2.0 headless server crashes on the first UDP packet, so level 2 uses 2.1.23. Level 1 runs FactorioTest headless in Docker, with no game client. [SPEC.md, "Decided"](../SPEC.md#decided), ["Tests"](../SPEC.md#tests)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## UI work is checked with screenshots from a real game client

Decided by: the repository owner (Michael)
Date: 2026-10-28

- Why: `game.take_screenshot{show_gui=true}` renders nothing on headless. [SPEC.md, "Decided"](../SPEC.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Error and bug reports go to Sentry as hand-built envelopes, with no SDK

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: all Python here is stdlib only and runs on the macOS system `python3` 3.9; the SDK sends local variables and log lines by default, which hold the player's question; most reports are not Python exceptions. [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Reports use environment prod for releases and dev-host for everything else

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: a test or dev install must never write into the players' environment; only `dev-*` events carry the host name. [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The Sentry DSN ships in the app and is refreshed from the release branch

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: a DSN only allows sending, so it may ship; a daily fetch from `release` lets a rotated DSN reach old installs, and a fetched DSN must point at `*.sentry.io`. [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Automatic error reports are on by default, with a switch on the app's status page

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: the cause of a player's stuck tab could not be seen from the developer's machine; the page states what is sent next to the switch. [docs/sentry.md](sentry.md)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## An automatic error carries versions, type, message, stack and failed Lua code, never the player's text

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: never the player's question, Kira's chat or logs; messages trimmed to 500 characters, stack without local variables, the OS user name cut out of paths. [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Chat, logs and screenshots go only from the report button and Kira's skill, after the player's yes

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: the player sees what goes before it goes. [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The same automatic error goes at most once an hour

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: a loop must not eat the free Sentry quota (Developer plan). [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Playing with no network is normal; reporting never blocks or slows anything

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: players may run local models with no internet; what could not be sent waits in a local outbox. [docs/sentry.md, "Decided"](sentry.md#decided)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## A bug report hands over the chat's raw session, not a summary

Decided by: the repository owner (Michael)
Date: 2026-11-01

- Why: long answers that stop for no visible reason and tools that fail are the bugs he meets most. [docs/sentry.md, "Raw sessions"](sentry.md#raw-sessions)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The mod ships inside the app, versioned with it, never from the mod portal

Decided by: the repository owner (Michael)
Date: 2026-11-04

- Why: (inferred: the restating comment gives none) players update one thing, and the mod always matches the bridge it talks to. [README, "Releases and updates"](../README.md#releases-and-updates)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Each worktree runs its own stack and checks only that stack

Decided by: the repository owner (Michael)
Date: 2026-11-04

- Why: (inferred: the restating comment gives none) parallel agents and checkouts must not take each other's game, bridge or gateway. [docs/worktrees.md](worktrees.md)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The atlas dump is hand-built text, structure is cached by dirty chunk, state is read live

Decided by: the repository owner (Michael)
Date: 2026-11-04

- Why: (inferred: the restating comment gives none) reading the whole base each time stalls the game. [docs/atlas.md, "Reading the base without stalling the game"](atlas.md#reading-the-base-without-stalling-the-game)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Building is ghosts only, powered in the same turn, nearest cluster by default, tried on a hidden copy before done

Decided by: the repository owner (Michael)
Date: 2026-11-04

- Why: (inferred: the restating comment gives none) Kira's builds must work when she says done, and the player keeps control of what gets built. [agent/factorio-companion.md](../agent/factorio-companion.md), "Building", "Done means it works", "Resources"
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## Private Kiro Crew _post calls for nested folders and chat slots until the public API has them

Decided by: the repository owner (Michael)
Date: 2026-11-04

- Why: (inferred: the restating comment gives none) the public API cannot create them yet. [SPEC.md, "Open questions"](../SPEC.md#open-questions)
- Evidence: [the repository owner's comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024)

## The mod runs on Factorio 2.0 and 2.1; the app writes the player's game line into its info.json

Decided by: the repository owner (Michael)
Date: 2026-11-07
Supersedes: [The mod declares Factorio 2.0; level 1 runs on headless 2.0.77, level 2 on headless 2.1.23](#the-mod-declares-factorio-20-level-1-runs-on-headless-2077-level-2-on-headless-2123)

- Quote: "нам бы поддержать мод и для 2.0.77 и для 2.1+" ("we should support the mod for both 2.0.77 and 2.1+"); on the test images: "образы переведи на 2.1.24" ("move the images to 2.1.24"); on the e2e: run it on both.
- Why: Steam's stable branch is 2.1 now, so a new player's game is 2.1, and a game that is still 2.0 can be updated at any time. Factorio loads a mod only when its `factorio_version` is the game's own major.minor, so one copy of the mod cannot serve both lines (checked 2026-11-07: with two copies side by side, 2.0.77 picks the newest one and refuses it with "Incompatible Factorio version"). Level 1 runs on headless 2.0.77 and 2.1.24, level 2 and the headless e2e on 2.1.24 (headless 2.0 still crashes on UDP), the app e2e on the 2.0.77 and the 2.1.24 client. [SPEC.md, "Decided", "Game version"](../SPEC.md#decided)
- Evidence: the repository owner's messages in the Kiro Crew chat of 2026-11-07, quoted above (the forge link is the pull request that brought this entry in).
