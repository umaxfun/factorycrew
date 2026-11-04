# Agent readiness: factorycrew

Remediated on branch `agent-readiness/docs-2026-11-03`, from the assessment of c1d821f (2026-11-02; code as at 769292c). The Finish fix (turn-contract e2e) merged before it. Assessed and remediated by Kiro (Kiro Crew session) on macOS arm64, for the software factory (`kirocrew-factory`). Skill: repo-readiness from kirocrew-factory-installer.
Caches used from this machine: Docker images `factorycrew-test:2.0.77` and `:2.1.23`, uv's managed CPython 3.12, the `kirocrew` login.
Tools brought in: none.

## Summary

Start (docs):    map -> contracts. `report/` has its own area doc, the overview names it with where its data goes and the Release workflow, and the repository owner's decisions are append-only entries in `docs/decisions.md`. The owner restated all of them, and five design notes besides, in [one comment on PR #4](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024), which every entry links as its evidence. A fresh agent answered 15 of 15 questions from recent changes on the new docs, and 15 of 15 on the docs before.
Finish (checks): See it run -> See it run. The flaky turn-contract e2e plants its fault itself now (bridge flag `--drop-first-reply`, test only); 6 real-model runs green.

Blocking an agent today:
- Nothing blocks T1-T4 work.
- `main` has no branch protection: CI is not a required check (the factory's go-live sets it up).

## Start: can an agent learn what it is about to change?

Level: contracts

| Area | Paths | Doc |
|---|---|---|
| Mod core | `mod/kc-companion/control.lua`, `mod/kc-companion/scripts/protocol.lua` | docs/areas/mod-core.md |
| Window and pointers | `mod/kc-companion/scripts/gui.lua`, `mod/kc-companion/scripts/pointers.lua`, `mod/kc-companion/data.lua` | docs/areas/window.md |
| Build helpers, trial | `mod/kc-companion/scripts/helpers.lua`, `mod/kc-companion/scripts/trial.lua` | docs/areas/build-helpers.md |
| Atlas | `mod/kc-companion/scripts/atlas.lua`, `mcp/kc/atlas.py` | docs/areas/atlas.md |
| Circuits | `mod/kc-companion/scripts/circuits.lua`, `mcp/kc/circuits.py` | docs/areas/circuits.md |
| Live panels | `mod/kc-companion/scripts/panels.lua`, panel tools | docs/areas/panels.md |
| Bridge (with the turn hooks) | `bridge/`, `app/hooks/` | docs/areas/bridge.md |
| Dispatcher | `dispatcher/` | docs/areas/dispatcher.md |
| MCP server | `mcp/factorio_mcp.py`, `mcp/kc_compute.py` | docs/areas/mcp-server.md |
| kc library | `mcp/kc/` | docs/areas/kc-library.md |
| Skills | `skills/` | docs/areas/skills.md |
| Kira | `agent/` | docs/areas/kira-agent.md |
| App and releases | `app/`, `scripts/build-app.sh`, `scripts/release.sh`, `.github/workflows/release.yml` | docs/areas/app.md |
| Error and bug reports | `report/` | docs/areas/reports.md (new); design: docs/sentry.md |
| Dev stands, test harness | `scripts/`, `tools/ui_dev.py`, `docker/`, `tests/e2e/{stand,watch}.py`, `.github/workflows/check.yml` | docs/areas/dev-stand.md, docs/worktrees.md |

| Item | Before | After | Evidence |
|---|---|---|---|
| S1 Entry point | pass | pass | `AGENTS.md`; `report/` row points at the area doc. |
| S2 Areas | pass | pass | 15 areas, confirmed by the owner (report/ its own area, hooks under Bridge). |
| S3 Area contracts | partial | pass | 15 area docs on the template; `docs-check.py --claims`: 0 unmarked, 20 `(inferred)`. |
| S4 Overview | partial | pass | docs/overview.md: the reports area, a diagram edge to Sentry, where report data lives, the Release workflow. |
| S5 Decisions | partial | pass | 26 entries in docs/decisions.md (Decided by, Date, Why, Evidence), each linking [the owner's comment](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024); area docs link them. |
| S6 Terms | pass | pass | per area doc. |
| S7 Docs are current | pass | pass | 14 claims of the new docs checked against the code (call sites of `configure`/`install_hooks` in all four parts, `SETTINGS_URL`, outbox path, copy steps in `build-app.sh`/`kc.sh`, UI components); one wrong claim fixed before commit (only one integration test asserts no player text). docs/sentry.md mod paths fixed. |
| S8 Question test | pass (15/15) | pass (15/15) | a new frozen set of 15 changes; fresh `kiro-cli` with fs_read only on each bundle; graded by a separate agent: before 15/15, after 15/15, no misses. |

Links and paths: `docs-check.py` resolves every link and anchor; the misses are test node ids (which exist), MCP method names (`tools/list`) and `report/environment`, which exists only in a build.

## Finish: can an agent prove its change is right?

Level: See it run. Unchanged from the assessment except F4/F12:

| Item | Before | After | Evidence |
|---|---|---|---|
| F3 Tests run | pass | pass | `scripts/check.sh` on this branch: unit 521 passed, 1 skipped; level 1 98/98 on 2.0 and 2.1; level 2 green. |
| F4 Tests are stable | pass, one flaky e2e | pass | `tests/e2e/test_turn_contract_e2e.py` plants its fault in the bridge, 6 of 6 real-model runs green. |
| F8 Local matches CI | pass | pass | `.github/workflows/check.yml`; no branch protection on `main` (go-live). |
| F12 End-to-end | pass | pass | headless e2e; the real-client app e2e was run by the owner and not re-run here. |

Other items (F1, F2, F5-F7, F9-F11) were not touched and not re-run.

## T1 automerge outlook

Unknown. The repository has three merged pull requests and no issues on GitHub; the outlook needs about 20 closed issues fixed by one change each. Until T1 automerge is on, every factory merge request waits for one approval from the owner.

## Support (no level)

- C1 Secrets: none in the repository. The Sentry DSN in `report/settings.json` is a send-only ingest key by design.
- C2 Agent rules: `AGENTS.md`, docs/worktrees.md.
- C3 Dangerous paths: AGENTS.md "Rules" plus `.github/workflows/release.yml`, `report/settings.json`, `app/hooks/turn_hook.py` with the bridge's `/hook`.
- C4 Source of truth: the code, a decision of the owner (docs/decisions.md).
- C5 Designs and approval: plans in docs/ (atlas.md, sentry.md); approval by the owner.

## Answers from you

- Decisions as entries, restated in one comment on the docs pull request: yes, [restated](https://github.com/umaxfun/factorycrew/pull/4#issuecomment-6623796024).
- SPEC.md "Decided" and docs/sentry.md "Decided": the owner's decisions, written by agents; entries too.
- `report/` its own area, turn hooks stay under Bridge: yes.
- App e2e: already run, skip.
- Factory: humans `umaxfun`; the GitHub App `factorycrew-crew` for the crew, a fine-grained token of the owner for the merger, `KIRO_API_KEY` in the environment `factory`; review models Opus and GPT Sol; branch protection on `main`: yes.

## Plan

Yours:
- Nothing for the docs. The factory's setup pull request follows (`kirocrew-factory` configure).

Agent, after that: the factory setup pull request (`kirocrew-factory` configure), with factory.yml and tier texts drafted on a local branch.
