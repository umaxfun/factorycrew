# Agent readiness: factorycrew

Remediated on branches `agent-readiness/checks-2026-10-29` and `agent-readiness/docs-2026-10-29` (stacked), from the assessment of 64468ef; base main 81e425b. Assessed by Kiro (Kiro Crew) on macOS arm64, 2026-10-29/30.
Caches used from this machine: Docker images `factorycrew-test:2.0.77` and `:2.1.23` (a cold `--no-cache` build was checked once, 55 s, with base images already pulled), uv's managed CPython 3.12.8, the `kirocrew` login.

## Summary

Start (docs):    none -> verified. AGENTS.md, 14 area docs, an overview and the decisions exist; a fresh agent given only the docs answered 15 of 15 questions from recent changes right (8 of 15 on the docs before).
Finish (checks): Not ready -> Not ready (agent items done). F1-F7 pass from a fresh worktree; "Change and verify" waits on F8: the CI workflow exists, but the repository has no remote yet, so it has never run.

Left for a person:
- Publish the repository on GitHub so `.github/workflows/check.yml` runs (F8).
- Read the claims marked `(inferred)` in `docs/areas/` (19 lines, listed in the docs merge request).
- Two app e2e tests are flaky on main (below).

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

Level: Not ready (agent items done; "Change and verify" waits on F8: publishing the repository).

| Item | Before | After | Evidence |
|---|---|---|---|
| F1 Clean install | partial | pass | nothing to install; `uv`, Docker, GNU `timeout` named in [worktrees.md, Requirements](worktrees.md#requirements-on-the-host). |
| F2 Build | pass | pass | `scripts/build-app.sh`, 0.5 s (not re-run: unchanged). |
| F3 Tests run | fail | pass | `scripts/check.sh` on a fresh worktree, first run: unit 430 passed 1 skipped, level 1 89/89 on 2.0 and 2.1, level 2 34 passed. |
| F4 Tests are stable | fail | pass | second run identical; level 1 green from an empty data dir twice per version. |
| F5 Fast check | fail | pass | `scripts/check.sh`, ~105 s. No linter (finding). |
| F6 Clean worktree | pass | pass | `git status` clean after every run. |
| F7 Worktrees do not interfere | fail | pass | two worktrees, `check.sh` one after another and at the same time: identical, all green. |
| F8 Local matches CI | fail | partial | `.github/workflows/check.yml` runs the same four scripts on push and PR; not run (no remote). |
| F9 App starts isolated | partial | pass | `stand.sh up` slot 4, `stand.sh check` 9/9 ok. |
| F10 Visual check | unknown | pass | `stand.sh ui shot`: the window with Kira's line, online. |
| F11 Services | pass | pass | headless game + bridge in Docker per slot. |
| F12 End-to-end | partial | partial | `test-e2e.sh` 9/9 (229 s); `test-app-e2e.sh` 22/24, the two failures flaky on main (below). Both refuse a checkout under `~/.kiro/crew`. |

Commands: [AGENTS.md, Commands](../AGENTS.md#commands).

Flaky on main, not caused by these branches:
- `tests/app_e2e/test_app_e2e.py::test_changing_existing_entities_needs_consent`: the wait for the answer to "yes" can return a leftover line of the previous turn (the chat text the dispatcher forwards), so the check runs before Kira acts. 1 of 3 runs.
- `tests/app_e2e/test_app_e2e.py::test_long_build_shows_progress`: depends on Kira's choices; one run powered the build with substations only (the test wants a big pole), one passed the 300 s limit.

The host: Docker Desktop twice left amd64 containers in "Created" during the night; `scripts/lib/docker.sh` failed those runs within a minute, and a Docker Desktop restart fixed it each time.

## Support (no level)

- C1 Secrets: none in the repository. The e2e runs need the user's `kirocrew` login and spend model credits. The atlas fixture dump is committed (`tests/data/atlas/dump.txt.gz`, published with Michael's consent); the save itself is not.
- C2 Agent rules: [AGENTS.md](../AGENTS.md) and [worktrees.md](worktrees.md). `agent/factorio-companion.md` and `skills/*/SKILL.md` are the product (Kira's rules), documented in [areas/kira-agent.md](areas/kira-agent.md) and [areas/skills.md](areas/skills.md).
- C3 Dangerous paths: listed in [AGENTS.md, Rules](../AGENTS.md#rules).
- C4 Source of truth: the code ([decisions.md](decisions.md)).
- C5 Designs and approval: designs in SPEC.md and docs/atlas.md; Michael decides.

## Answers from you (applied)

- The area list: as listed (Live panels added as the 14th after it merged).
- When SPEC, README and code disagree: the code wins.
- Decisions: no sandbox for `factorio_query`, no model pin, consent before changing entities confirmed; the `gamer`-account setup retired.
- CI: GitHub Actions on Linux.
- Real-client checks: allowed (they cannot run in CI: no window there).
- The atlas fixture dump: commit it.

## Left

- Michael: publish the repository on GitHub and open the two branches as PRs, checks first. Done when the `check` workflow shows four green jobs.
- Michael: read the `(inferred)` claims in `docs/areas/` and confirm or correct them.
- Michael: decide whether to add a linter (ruff, luacheck) to `check.sh` and CI.
- Open findings for whoever works on them next: Kira's rules name only the `belts` skill, though all seven are importable and `app/app.json` registers only `skills/belts` ([areas/skills.md](areas/skills.md)); the two flaky app e2e tests above.
