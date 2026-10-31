# Agent readiness: factorycrew

Remediated from the assessment of 64468ef and merged into `main` (release 0.3.3, then 06c85c6). Assessed by Kiro (Kiro Crew) on macOS arm64, 2026-10-29 to 31; CI on GitHub Actions, Ubuntu.
Caches used from this machine: Docker images `factorycrew-test:2.0.77` and `:2.1.23` (a cold `--no-cache` build was checked once, 55 s, with base images already pulled), uv's managed CPython 3.12.8, the `kirocrew` login. CI builds the images from scratch.

## Summary

Start (docs):    none -> verified. AGENTS.md, 14 area docs, an overview and the decisions exist; a fresh agent given only the docs answered 15 of 15 questions from recent changes right (8 of 15 on the docs before).
Finish (checks): Not ready -> See it run. F1-F12 pass: `scripts/check.sh` runs locally in ~105 s and the same four steps run in CI on every push and pull request ([first green run](https://github.com/umaxfun/factorycrew/actions/runs/41195895761)).

Left for a person:
- Read the claims marked `(inferred)` in `docs/areas/` (19 lines; `grep -rn '(inferred' docs/areas`).
- Decide whether to add a linter (ruff, luacheck) to `check.sh` and CI.

## Start: can an agent learn what it is about to change?

Level: verified

| Item | Before | After | Evidence |
|---|---|---|---|
| S1 Entry point | fail | pass | [AGENTS.md](../AGENTS.md), 48 lines: what it is, commands, rules, path -> area doc table; links and paths resolve (`docs-check.py`). |
| S2 Areas | fail | pass | 14 areas (13 confirmed by Michael, plus Live panels, merged after the assessment), each in [areas/](areas/) and in the AGENTS.md table. |
| S3 Area contracts | fail | pass | every area doc has the template's sections; every bullet under Talks to, Must stay true and Easy to break has a source or `(inferred)`. |
| S4 Overview | fail | pass | [overview.md](overview.md): all 14 areas, the flow, where data lives, build and ship. |
| S5 Decisions | partial | pass | [decisions.md](decisions.md): the five confirmed on 2026-10-29 plus the ones written with reasons; SPEC no longer contradicts them. |
| S6 Terms | partial | pass | each area doc has Terms; "level" and "unit" have one meaning each in README and docs/worktrees.md. |
| S7 Docs are current | fail | pass | 6 wrong claims in the assessment and about 30 more found while writing the area docs, all fixed in SPEC, README, docs/atlas.md, docs/circuits-format.md, docs/worktrees.md and code comments; 12 claims of the new docs re-checked against the code, none wrong. No check enforces docs yet (no link lint in CI). |
| S8 Question test | fail (4/15) | pass (15/15) | a new frozen set of 15 changes, held out from the docs writers; before-docs bundle 8/15, after 15/15, graded by a separate agent. |

## Finish: can an agent prove its change is right?

Level: See it run

| Item | Before | After | Evidence |
|---|---|---|---|
| F1 Clean install | partial | pass | nothing to install; `uv`, Docker, GNU `timeout` named in [worktrees.md, Requirements](worktrees.md#requirements-on-the-host). CI starts from a clean runner and builds the images. |
| F2 Build | pass | pass | `scripts/build-app.sh`, 0.5 s; `scripts/release.sh` built and committed 0.3.3. |
| F3 Tests run | fail | pass | `scripts/check.sh` on a fresh worktree, first run: unit 436 passed 1 skipped, level 1 89/89 on 2.0 and 2.1, level 2 34 passed. |
| F4 Tests are stable | fail | pass | second run identical; level 1 green from an empty data dir twice per version. |
| F5 Fast check | fail | pass | `scripts/check.sh`, ~105 s. No linter (finding). |
| F6 Clean worktree | pass | pass | `git status` clean after every run. |
| F7 Worktrees do not interfere | fail | pass | two worktrees, `check.sh` one after another and at the same time: identical, all green. |
| F8 Local matches CI | fail | pass | `.github/workflows/check.yml` runs the same four scripts as `check.sh` on push and PR. The first run found one test that depended on the host's speed (level 2 production test: a machine ran out of input before the window ended); fixed in 06c85c6, then 4/4 green ([run](https://github.com/umaxfun/factorycrew/actions/runs/41195895761)). |
| F9 App starts isolated | partial | pass | `stand.sh up` slot 4, `stand.sh check` 9/9 ok. |
| F10 Visual check | unknown | pass | `stand.sh ui shot`: the window with Kira's line, online. |
| F11 Services | pass | pass | headless game + bridge in Docker per slot. |
| F12 End-to-end | partial | pass | `test-e2e.sh` 9/9 (229 s). `test-app-e2e.sh` 22/24 at first; the two failures were fixed (below) and both tests then passed 3 of 3 on the real client. Both scripts refuse a checkout under `~/.kiro/crew`. |

Commands: [AGENTS.md, Commands](../AGENTS.md#commands). The e2e levels need a Kiro Crew login, a real model and, for the app e2e, a game window, so they run locally only.

Fixed after the first recheck:
- `test_changing_existing_entities_needs_consent` was a product bug: the dispatcher forwarded the previous turn's closing chat text as the answer to a quick follow-up. It now counts transcript rows only after the question's own user row ([areas/dispatcher.md](areas/dispatcher.md)).
- `test_long_build_shows_progress` had a 300 s cap that healthy runs (197-352 s) could miss; it now fails on 150 s without any movement.
- Kira's rules, the `factorio_compute` description and `app/app.json` named only the `belts` skill; all seven are named now, and `tests/unit/test_skills_listed.py` keeps it that way.

The host: Docker Desktop twice left amd64 containers in "Created" during the night; `scripts/lib/docker.sh` failed those runs within a minute, and a Docker Desktop restart fixed it each time.

## Support (no level)

- C1 Secrets: none in the repository (history scanned before it went public). The e2e runs need the user's `kirocrew` login and spend model credits. The atlas fixture dump is committed (`tests/data/atlas/dump.txt.gz`, published with Michael's consent); the save itself is not.
- C2 Agent rules: [AGENTS.md](../AGENTS.md) and [worktrees.md](worktrees.md). `agent/factorio-companion.md` and `skills/*/SKILL.md` are the product (Kira's rules), documented in [areas/kira-agent.md](areas/kira-agent.md) and [areas/skills.md](areas/skills.md).
- C3 Dangerous paths: listed in [AGENTS.md, Rules](../AGENTS.md#rules).
- C4 Source of truth: the code ([decisions.md](decisions.md)).
- C5 Designs and approval: designs in SPEC.md and docs/atlas.md; Michael decides.
- Licence: MIT ([LICENSE](../LICENSE)); ported code keeps its upstream licence next to it (`skills/*/LICENSE-upstream`, `mcp/kc/LICENSE-blueprints.txt`).

## Answers from you (applied)

- The area list: as listed (Live panels added as the 14th after it merged).
- When SPEC, README and code disagree: the code wins.
- Decisions: no sandbox for `factorio_query`, no model pin, consent before changing entities confirmed; the `gamer`-account setup retired.
- CI: GitHub Actions on Linux ([umaxfun/factorycrew](https://github.com/umaxfun/factorycrew), public).
- Real-client checks: allowed (they cannot run in CI: no window there).
- The atlas fixture dump: commit it.
- Licence: MIT.

## Left

- Michael: read the `(inferred)` claims in `docs/areas/` and confirm or correct them.
- Michael: decide whether to add a linter (ruff, luacheck) to `check.sh` and CI.
- Open: whether Kira picks `power.plan` now that her rules name it; in three real-client runs she still placed poles by hand, correctly.
