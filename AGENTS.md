# factorycrew: start here

Kira, a Kiro Crew agent, answers a Factorio player's questions from a window inside the game. A Lua mod (`mod/kc-companion/`) talks UDP to a Python bridge (`bridge/`); a Kiro Crew script-cron (`dispatcher/`) moves questions into chats; Kira's MCP server (`mcp/`) gives her tools that act in the game through the bridge. Players get all of it as a Kiro Crew app (`app/`). Behaviour contract: [SPEC.md](SPEC.md). Architecture: [docs/overview.md](docs/overview.md).

## Commands

| What | Command | Time |
|---|---|---|
| Install | nothing to install: Python is stdlib only; the scripts need `uv`, Docker, `python3`, GNU `timeout` (see below the table) | |
| Fast check, before every commit (CI runs the same) | `./scripts/check.sh` | ~2 min |
| Python unit tests only | `./scripts/test-unit.sh [tests/unit/<file>.py] [-k <name>]` | ~20 s |
| Mod tests (level 1) | `./scripts/test-mod.sh`, `./scripts/test-mod-2.1.sh` | ~11 s each |
| Mod + bridge + MCP (level 2) | `./scripts/test-integration.sh [-k <name>]` | ~65 s |
| e2e through Kiro Crew, headless, real model | `./scripts/test-e2e.sh [-k <name>]` | ~4 min |
| App e2e, real game window, real model | `./scripts/test-app-e2e.sh [-k <name>]` | ~20 min |
| Start your own stack (game window, bridge, gateway, Kira) | `./scripts/stand.sh up`, then `./scripts/stand.sh check` | ~30 s |
| Build the app | `./scripts/build-app.sh` | 1 s |

Host requirements for the scripts: [docs/worktrees.md, Requirements](docs/worktrees.md#requirements-on-the-host).

Work in your own git worktree, next to the main clone (`<main clone>.worktrees/<name>`), never under `~/.kiro/crew`: the gateway's sandbox cannot read it there. Only what `./scripts/stand.sh env` prints is yours ([docs/worktrees.md](docs/worktrees.md)). The e2e scripts use a real model and open no window except `test-app-e2e.sh` and `stand.sh up`.

## Rules

- A change that alters an area's behaviour updates that area's doc in the same change.
- The code wins when SPEC, README and code disagree: fix the doc ([docs/decisions.md](docs/decisions.md)).
- Do not undo a decision in [docs/decisions.md](docs/decisions.md) without asking.
- Costly to get wrong: the Release workflow (`.github/workflows/release.yml`, `scripts/release.sh`) and the `release` branch (players update from it; rehearse with a `vX.Y.Z-test.N` tag, which goes to `release-staging`), `app/app.json` version, `mod/kc-companion/scripts/protocol.lua` together with `bridge/factorio_bridge.py` (both ends of the UDP protocol), the mod's `storage` layout (players' saves), dispatcher chat keys and cron identity (chats refuse a new cron), `agent/factorio-companion.json` (no `model` field, on purpose).

## Where things are

| Paths | Area doc |
|---|---|
| `mod/kc-companion/control.lua`, `mod/kc-companion/scripts/protocol.lua`, `mod/kc-companion/info.json` | [Mod core](docs/areas/mod-core.md) |
| `mod/kc-companion/scripts/gui.lua`, `mod/kc-companion/scripts/pointers.lua`, `mod/kc-companion/data.lua`, `mod/kc-companion/graphics/`, `mod/kc-companion/locale/` | [In-game window and pointers](docs/areas/window.md) |
| `mod/kc-companion/scripts/helpers.lua`, `mod/kc-companion/scripts/trial.lua` | [Build helpers and trial runs](docs/areas/build-helpers.md) |
| `mod/kc-companion/scripts/atlas.lua`, `mcp/kc/atlas.py`, `scripts/atlas-fixture.sh`, `tests/data/atlas/`, `tools/atlas_proto/` | [Atlas](docs/areas/atlas.md) |
| `mod/kc-companion/scripts/circuits.lua`, `mcp/kc/circuits.py` | [Circuits](docs/areas/circuits.md) |
| `mod/kc-companion/scripts/panels.lua`, panel tools in `mcp/factorio_mcp.py` | [Live panels](docs/areas/panels.md) |
| `bridge/`, `app/hooks/` | [Bridge](docs/areas/bridge.md) |
| `dispatcher/` | [Dispatcher](docs/areas/dispatcher.md) |
| `mcp/factorio_mcp.py`, `mcp/kc_compute.py` | [MCP server](docs/areas/mcp-server.md) |
| `mcp/kc/` (except atlas, circuits) | [kc library](docs/areas/kc-library.md) |
| `skills/` | [Skills](docs/areas/skills.md) |
| `agent/` | [Kira, the agent](docs/areas/kira-agent.md) |
| `app/`, `scripts/build-app.sh`, `scripts/release.sh`, `scripts/app-dev.sh` | [Kiro Crew app and releases](docs/areas/app.md) |
| `scripts/` (stand, kc, test scripts, lib), `tools/ui_dev.py`, `docker/`, `tests/e2e/stand.py`, `tests/e2e/watch.py`, `.github/` | [Dev stands and test harness](docs/areas/dev-stand.md) |
| `report/` | [Error and bug reports](docs/areas/reports.md), design: [docs/sentry.md](docs/sentry.md) |
| `tests/` | the area doc of the code each test checks |

The last readiness assessment: [docs/agent-readiness.md](docs/agent-readiness.md).

<!-- factory:start -->
## Factory rules

This repository is worked by an agent crew (Kiro Crew Issue Radar). A separate merger merges
merge requests (pull requests on GitHub); people approve what it will not merge alone. The
rules themselves are in `factory.yml` on the default branch; this section says how to work
with them. Configure and upgrades replace only the text between the factory markers.

### Scope

- `scope.mode` in `factory.yml` on the default branch says which work is the crew's. `full`
  (or no `scope`): every issue. `own`: only issues labelled `scope.label` (`factory` by
  default), which a person puts on; every other issue and merge request is the team's.
  Never pick one up, comment on it, or add or remove that label. The merger and the factory's
  CI jobs ignore merge requests outside the scope the same way, on GitLab and on GitHub.
- In own mode your merge request is in scope because it fixes a labelled issue
  (`Fixes #<n>`), or because it goes into a feature branch. A person's older merge request
  that a person labels is *adopted*: the merger tells its author, waits, updates the branch
  and merges it on a new pipeline. Leave those alone too.

### Where you work

- **Every branch lives in its own git worktree.** Never switch the branch of the main clone:
  it stays on the default branch, because other chats start there and would see your branch.

### Tiers

A tier says what the change does to the product, not how big it is (`tiers` in `factory.yml`):

- **T1 fix**: the code contradicts documented behaviour (an area doc, the user docs, a test, the
  decision file) and the fix brings it back. The triage comment quotes that line
  (`` Spec: `<file>:<line>` "<quote>" ``). Do only what that line requires.
- **T2 addition**: a small new option, value or field in an existing area. Existing behaviour
  stays exactly as it is.
- **T3 behaviour change**: changes existing behaviour, however small. A short design in a
  feature branch, approved by a person, comes first.
- **T4 new concept**: something the product did not have (a new row in the area table, a new
  term in the architecture overview, a new public API family). A full design comes first, and
  you draft it only when a person asks.

**Tiers only go up.** When you find that a T1 or T2 changes existing behaviour, or that a T2 or
T3 adds a concept, add the higher tier label yourself with a comment that cites the evidence
(`file:line`, the failing test, the review finding), and continue on that tier's path. Never
remove or lower a tier label: only a person in `humans` lowers a tier, and the merger ignores a
lower tier from anyone else. An AI review that blocks with `product-shape` on a T1 or T2 is the
same evidence: move the issue up rather than argue the tier.

### Before you fix a bug

1. **Reproduce it first, on your own instance** of the app, from your worktree. Write the
   failing test before the fix, in the area's tests (each area doc says where under "How to
   check a change"); for a visual bug, a screenshot of the broken state. If you cannot
   reproduce it, do not guess a fix: pass on the issue as `not-reproducible` and say what you
   tried.
2. **Check that it is a bug.** Read the doc of the area the code belongs to (the area table in
   this file), its decisions, and the tests. If a doc, a decision or a test says the
   behaviour is intended, it is not a bug: comment with the reference and pass on it. If the
   docs are silent, the current behaviour is presumed intended: the fix is a behaviour change,
   so move the issue up to T3 (see Tiers) and, when two behaviours are reasonable, publish the
   question to a person with your recommendation.

### The merge request

- Title says what the user sees change. Body has three short parts: **Repro** (steps or the
  test that failed), **Fix** (what and why), **Proof** (the test that now passes, or
  before/after screenshots).
- One issue per merge request, `Fixes #<n>` on its own line. The merger reads the tier from
  the issues it fixes.
- **A change to what users see ships with a screenshot.** When the merge request changes a
  file matching `evidence.screenshot_when` in `factory.yml`, attach an *after* screenshot of
  the screen to the merge request, and a *before* one if the screen existed. The merger does
  not merge it without an image in its description or a comment.
- A change to an area's behaviour updates that area's doc in the same merge request. The
  `factory-docs` check fails on links, paths and line citations that no longer resolve, and
  when the area table here and `areas` in `factory.yml` differ.
- The repository's checks green locally before you push.

### Feature branches

- An issue can name a feature branch on its own line: `Feature branch: feature/<name>`. For
  such an issue, create your worktree from `origin/feature/<name>`, not from the default
  branch, and open the merge request **into `feature/<name>`**. The feature's design lives on
  that branch, in the designs folder (`designs.dir` in `factory.yml`). Do what your step of
  the design asks, no more.
- Write `Fixes #<n>` in the body as usual. The forge does not close issues for a merge into a
  feature branch; the merger closes them when it merges.
- Never open a merge request from a feature branch into the default branch: a person does
  that when the whole feature is ready. When the last step issue of a feature is closed,
  tell the person: comment on the T3 or T4 issue that every step is merged into `feature/<name>`
  and the feature is ready for its merge request into the default branch, and label it
  `crew: needs human`. Otherwise nobody learns that the feature waits. Give the person a
  ready description for that merge request in the same comment: the design and each step
  merge request by number, `Fixes #<T3 or T4 issue>`, and every screenshot the step merge requests
  carried (their upload links), because the reviews of the feature merge request check the
  new UI against its own description.
- Never change the design on a feature branch yourself. If a step cannot be done as
  designed, say why in the issue and label it `crew: needs human`.

### Designs (T3, T4)

- **Never start T3 or T4 code without an approved design.** Such an issue with no design on a
  feature branch waits: ask in the issue who designs it, and label it `crew: needs human`.
- A T3 design is short: what behaviour changes, for whom, what stays, the steps. A T4 design is
  full: why, the alternatives considered, how it fits the architecture overview, the new area
  and its doc, the steps. For T4 the design merges first, then the new area doc (its own merge
  request into the feature branch, with the area table row in this file and `areas` in
  `factory.yml`), then the steps.
- Draft a design only when a person in `humans` asks for it in the issue. Draft it as one
  merge request into the feature branch they name (`feature/<name>`; create the branch from
  the default branch if it does not exist) that changes only the designs folder
  (`designs.dir` in `factory.yml`). The design ends with the list of steps, each small enough
  for one merge request.
- A person approves the design merge request (the designs folder is in `gate_paths`). After
  it merges, open one issue per step, each with `Feature branch: feature/<name>` on its own
  line, `Part of #<n>` for the T3 or T4 issue, and the step it does. Do not open them
  earlier. A step issue needs no `scope.label` and no tier label, even in own mode: the
  crew takes it on the strength of the approved design, and the triager leaves it alone.
- With Issue Radar EE a person may write the design in a chat of their own instead. Then the
  design is on `feature/<name>` without a merge request, and the person opens the step issues
  themselves when they hand the design over, each with a tier label. A design on a feature
  branch is yours to build only through such a step issue: never start from the design alone,
  and never open step issues for a design a person wrote.
- The T3 or T4 issue stays open until the feature reaches the default branch: refer to it as
  `Part of #<n>` in the design and step merge requests, never `Fixes #<n>`, because the
  merger closes the issues a merge into a feature branch fixes.

### Merging

- **Never merge, and never turn on auto-merge.** You do not have the rights, and trying
  wastes a turn. The merger merges a green merge request on its own.
- The merger refuses, and explains in a comment, when: a check the tier needs is not green or
  did not run, a review thread is open, the merge request is labelled `crew: needs human` or
  `hold`, a screenshot is missing, the diff touches a gate path (`gate_paths` in
  `factory.yml`), or the tier is not merged alone. A gate path and such a tier need a
  person's approval; say in the body why the path had to change.
- **The default branch is red** while an issue labelled `factory: main red` is open: the
  merger merges nothing into it and says so once on each waiting merge request. Do not chase a
  red check your change did not cause; fixing the default branch is that issue's work, and it
  closes when the default branch is green again.

### Review feedback

- A merger comment, a red check, an unresolved thread and an AI review ending in
  `VERDICT: BLOCK` are all review feedback on your merge request: fix what is real, push,
  and reply in the thread. Rebut a false positive with evidence instead of complying with it.
- **Record what you did with each AI review finding** in one comment per round, so the next
  review round does not raise it again. The comment starts with the marker line and has one
  line per finding, plus quoted lines with the evidence for a rebuttal:

  ```
  <!-- factory-disposition head=<full sha of the head the review judged> -->
  - **fixed** `<file>:<line>` <the finding's headline>: fixed in <full sha>
  - **rebutted** `<file>:<line>` <the finding's headline>
  > <why it is not a defect, with the file:line that shows it>
  ```

  The review reads only the marker, the `- **...**` lines and the `> ` lines, and only from
  you (the merge request's author) and from people in `humans`. Your rebuttal never covers a
  security, crash, data-loss, corruption or removed-guard finding: those come back until the
  code changes or a person rules on them.
- An AI review block is cleared by an override comment for the current head:

  ```
  /factory override ai-review <full 40-character head sha> <class> [model=<model>]: <reason>
  ```

  The class is one of `false-positive`, `not-applicable`, `pre-existing #<issue>`,
  `infra-failure <https job URL>`, `deferred #<open issue>` and `owner-decision`; the reason is
  20-500 characters; `model=` (one of `review.models`) clears only that model's review, without
  it both. A person in `humans` may use any class.
- **You may override your own block only as `false-positive`**, only when you are sure the
  finding is wrong and the reason gives the evidence (`file:line`), and only when no blocking
  finding of that review is a security, crash, data-loss, corruption or removed-guard finding,
  or a product-shape, one-way design door or missing-screenshot (evidence-gap) one: the merger
  reads the review's anchors and ignores your override otherwise. Say in the merge
  request that you overrode, and why. Any other case is a person's call.
- After an override, propose the rule that would have prevented the false positive in
  `factory/review-calibration.md`, in its own merge request, citing the case; it lands only
  when a person approves it.
- **Two rounds, then a person.** When one review lens blocks the same place in two rounds in
  a row (`review.max_rounds` in `factory.yml`), stop fixing: label the issue
  `crew: needs human` and say what is stuck.
<!-- factory:end -->
