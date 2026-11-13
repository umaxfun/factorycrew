# factory.yml

The factory's rules for one repository. It sits at the repository root and is read from the
default branch by everything that enforces a rule: the merger, the CI jobs `factory-tier`,
`factory-docs` and `ai-review-*`, and the crew. A merge request cannot change the rules that
judge it: the file is a gate path, so changing it needs a person's approval, and the new rules
apply from the merge on. `factory/factory.example.yml` is a filled-in example; `factory/fcore.py`
(`Config`) is the code that reads and validates it.

Globs anywhere in the file are root-relative. `**` spans any number of directories, also none
(`**/migrations/**` matches `migrations/0001.py`); `*` and `?` stay inside one path segment. A
pattern without `**` matches only at the root: `AGENTS.md` is the root file, `**/AGENTS.md` is
any.

## version

`1`. Any other value is refused.

## forge

- `kind`: `gitlab` or `github`.
- `url`: the forge's web address as people open it: `https://gitlab.com`, a self-managed
  GitLab (a path prefix such as `https://host/gitlab` is fine), `https://github.com`, a GHE.com
  tenant or a GitHub Enterprise Server host. The API address is derived from it.
- `project`: GitLab project path with its namespace (`group/sub/repo`), or GitHub `owner/name`.
- `merge_method` (GitHub, optional): `merge` (default), `squash` or `rebase`. GitLab uses the
  project's own merge method setting.

The merger's cron message repeats `kind`, `url` and `project` because it needs them to find
this file; the merger refuses to run when the two disagree.

## default_branch

The branch the product ships from. The merger checks it against the forge's default branch.

## humans

Forge usernames (GitLab) or logins (GitHub) of the people who decide. Only an account in this
list can:

- approve a merge request that touches a gate path;
- approve a merge request whose tier has `automerge: false`;
- approve a feature branch going into the default branch;
- set a lower tier on an issue (a lower tier from anyone else, the crew and the triager
  included, does not count; anyone may set a higher one), or its first tier (see `triager`);
- clear an AI review block with `/factory override ai-review <full 40-character head sha> <class>
  [model=<model>]: <reason>`, any class (`false-positive`, `not-applicable`, `pre-existing #N`,
  `infra-failure <https job URL>`, `deferred #N` with N an open issue of this project (a defect tracked elsewhere is
  `owner-decision` with its link in the reason), `owner-decision`), a
  reason of 20-500 characters that is more than one `<placeholder>`. The merge request's author, when it is the crew (an account in
  `crew`; without `crew`, any author outside `humans` except on an adopted merge request), may post only
  `false-positive`, and the merger ignores it when a blocking finding of that review is a
  security, crash, data-loss, corruption or guard-removal one, or a product-shape,
  design-one-way-door or evidence-gap one (a person owns those gates).

Bots, including the crew and the merger, must not be in it. For a feature branch the list is
always the default branch's.

## crew

Optional. The crew's forge accounts (the Kiro Crew instance's forge login). With it, only a
merge request one of these accounts opened is the crew's: its author may post the
`false-positive` override, and the review reads its disposition comments as the crew's. A person
outside `humans` who opens a merge request, or references a factory-labelled issue in one, gets
no say over a review block. Set it whenever the crew has a fixed account. Must not share an
account with `humans`. Like `humans`, it is always the default branch's.

## triager

The triager's forge accounts (the login its cron posts with, usually the crew's). Besides a
person in `humans`, only these accounts may set an issue's first tier label. A tier below the
highest from anyone else, a member who may edit labels included, does not count: the issue stays
at the highest tier, and the merger says so once on the merge request (marker `tier-ignored`).
Without it, every first tier comes from a person. Must not share an account with `humans`; may
share one with `crew`. Always the default branch's.

## checks

The repository's own CI checks, by name. Tiers refer to these names.

```yaml
checks:
  check: {job: check}                                     # GitLab CI job, or GitHub check-run name
  test:  {job: test, workflow: .github/workflows/ci.yml}  # GitHub Actions
  jenkins: {job: continuous-integration/jenkins/pr-merge, app: commit-status}
```

- `job`: the job name exactly as the forge shows it (GitLab job name; GitHub check-run name,
  which for a matrix job is `test (3.12)` and for a reusable workflow `caller / job`; a commit
  status context).
- `workflow` (GitHub): the workflow file the check-run must come from. Required for GitHub
  Actions checks: a merge request can add a workflow with a job of the same name, and that job
  does not count.
- `app` (GitHub, optional): the app slug of the check-run, `github-actions` by default. Use
  `commit-status` for a CI that reports commit statuses (Jenkins, Buildkite) rather than
  check-runs; a check from any other app does not count.

Two names are built in and need no entry:

- `ai-review`: one job per model in `review.models`, named `ai-review-<model slug>`
  (`claude-opus-5.5` gives `ai-review-claude-opus-5-5`). On GitHub they come from
  `.github/workflows/factory.yml`.
- `factory-docs`: the docs freshness job (`factory/docs_gate.py`).

A check the tier needs must have run on the merge request's current head commit and
succeeded; skipped, manual, cancelled or missing is a refusal. Any other check that failed
also blocks, unless the forge marks it allowed to fail or it is a check named here that only
another tier needs.

## tiers

Tiers from low to high, keyed by a short name (`T1` .. `T4`). The order matters: the highest is
what an unsorted issue gets. A tier says what the change does to the PRODUCT, not how big it is:

| Tier | `effect` | The change | Path |
|---|---|---|---|
| T1 | `fix` | the code contradicts DOCUMENTED behaviour and the fix brings it back | automatic merge when `automerge: true` |
| T2 | `addition` | a small new option, value or field in an existing area; no existing behaviour changes | a person approves the merge request |
| T3 | `change` | changes existing behaviour, however small | a short design in a feature branch, approved by a person |
| T4 | `concept` | something the product did not have: a new area, a new term in the overview, a new public API family | a full design, drafted only when a person asks |

- Documented means a line in an area doc, the user docs, a test, or the repository's decision
  file (`contributing/decisions.md` or its own). The triager quotes that line in its comment,
  as `` Spec: `<file>:<line>` "<quote>" ``; no line to quote, no T1. Behaviour nobody
  documented is presumed intended, so changing it is T3. The review job checks that the quote
  is on that line of the target branch.
- Rule of thumb for T4: the change needs a new row in the `AGENTS.md` area map or a new term
  in the architecture overview. Then it is T4, even if it is small.
- **Tiers only go up.** The crew, a review or anyone may move an issue up (a higher tier is
  only ever stricter): when the work turns out to change behaviour (T1, T2) or to add a concept
  (T2, T3), the crew adds the higher label with a comment citing the evidence and continues on
  that tier's path. Only a person in `humans` lowers a tier; from anyone else, the crew and the
  triager included, a lower tier does not count.

Keys of each tier:

- `title`: a few words, shown in merger comments and to the triager.
- `label` (optional): the issue label for this tier; the key itself by default.
- `checks`: names from `checks` (or the built-ins) that must be green.
- `automerge`: `true` lets the merger merge into the default branch alone once every rule
  holds. `false` makes it wait for a person's approval; the merger's refusal then says that
  every other rule holds, so it would merge on that approval. Into a feature branch the steps
  of a T3 or T4 merge under the usual rules; only gate paths need a person there. Keep T1 at
  `false` until the tier test passes, or until 20 T1 merge requests in a row were approved
  without a person changing the tier or the code (`skills/kirocrew-factory/configure.md`,
  "Turning T1 automerge on"); T2-T4 stay `false` whatever the test says.
- `effect` (optional): `fix`, `addition`, `change` or `concept`. By position when absent: the
  first tier `fix`, the second `addition`, the third `change`, the fourth and higher
  `concept`. The review reads it (`first_principles`, `product-shape`).
- `triage`: the text the triager decides by. Written from the repository's own issues by the
  rule above. For a library, read "option, value or field" as a public name, parameter or
  accepted value: T2 adds one; T3 changes how names or values resolve, deprecates, changes a
  default or an error, or drops a dependency; T4 adds a new public API family.
- `examples`: three real issues (and their merge requests) of this tier, from the repository.
  A T1 example names the documented line it restored.
- `needs_start` (T3, T4): the Start level the readiness report must reach before the tier is
  turned on (`contracts`). Nothing reads the report at run time: a tier is off when it is not
  in `tiers`, so add it only once the report reaches the level. The first tier that has it is
  the first tier that needs a design: the AI review takes an issue below it, written by a
  person in `humans`, as the recorded decision for exactly what the issue asks, narrowed by
  its `effect`.
- `design` (optional, from the first `needs_start` tier up): `short` (a behaviour change: what
  changes, for whom, the steps) or `full` (a new concept: why, the alternatives, how it fits,
  the new area and its doc, the steps). Default `full` for a `concept` tier, else `short`. A
  tier that starts from a design must have `automerge: false`: the merger treats T4 like T3.

A file with three tiers still loads: its tiers are `fix`, `addition` and `change`, and its T3
covers what T4 would.

The tier of a merge request is the highest tier among the issues it fixes (`Fixes #N` lines;
on GitLab also the issues the forge links). The tier of an issue (`fcore.issue_tier`, label
events on GitLab, the timeline on GitHub):

- its first tier counts when a person in `humans` or the `triager` added it, or when it is the
  highest; from anyone else the issue stays at the highest tier (marker `tier-ignored`);
- after that, a higher tier counts from anyone, a lower one only from a person in `humans`;
- removing a label never lowers the tier; with no tier label left the issue is the highest.

A merge request that fixes no tiered issue gets the highest tier.

## triage

Rules for the triager, besides the tier texts.

- `when_unsure`: `higher` (pick the higher of two tiers that fit) or `ask` (ask the issue
  author and wait).
- `borderline`: the cases the person decided at configure time, each `{case, tier, why}`.

## overview, areas

The area map: which paths belong to which area doc. `overview` is the architecture overview.

```yaml
overview: docs/architecture/overview.md
areas:
  - {name: Game engine, paths: ["backend/app/game/**"], doc: docs/areas/game-engine.md}
```

The AI review reads the overview and the docs of every area the diff touches, from the target
branch. `AGENTS.md` keeps the same map as a table for agents and people; `factory-docs` fails
when the two differ.

## docs_allow

Paths the docs cite that exist only after a build or a test run (a test report folder,
generated code), so `factory-docs` does not report them as missing:

```yaml
docs_allow: ["e2e/browser/test-results/", "web/dist/"]
```

A doc path equal to an entry or under it is not a PATH finding. Plain repository paths only:
no globs, not the root. Use it for build outputs instead of the docs baseline, which is for
real debt. `factory-docs` reads it from the merge request's `factory.yml`, a gate path, so an
entry a merge request adds is approved by a person.

## designs

Where T3 and T4 designs live and which branches are feature branches.

- `dir`: the design folder, `docs/designs` by default; `contributing/designs` when `docs/` is
  a published site. Make it a gate path.
- `branches`: the feature branch pattern, `feature/*` by default.

A merge request into a feature branch is judged by the default branch's rules, made stricter
by the feature branch's own `factory.yml`: it may add required checks per tier, gate paths
and screenshot globs, and turn `automerge` off, but never relax a rule. `humans`, `forge`,
`designs` and the tier list are always the default branch's, because a feature branch can be
created by the crew without review. After merging into a feature branch the merger closes the
fixed issues itself; a feature branch reaches the default branch only with a person's approval.

The AI review of a merge request into a feature branch reads the design from that branch. The
review of the feature branch going into the default branch reads it from the source branch as
the forge holds it (`origin/<source>`, not the merge request's head): the design was approved
there and reaches the default branch with that merge request.

## review

- `models`: two models from different families. A tier that needs `ai-review` without two
  different models is refused.
- `lenses` (optional): the review lenses, from `correctness`, `security`, `first_principles`,
  `tests_spec`, `ux`, `public_api`. Default: the first four plus `ux` when
  `evidence.screenshot_when` is set, else `public_api`.
- `calibration`: the review calibration file, `factory/review-calibration.md` by default; a
  gate path, read from the target branch.
- `max_rounds`: when one lens blocks the same place this many rounds in a row, the crew stops
  fixing and labels the issue `crew: needs human`. `2`.

## evidence

- `screenshot_when`: globs of files whose change shows on screen. A merge request that changes
  one ships with an image in its description or a comment, or the merger refuses it.
- `screenshot_except` (optional): globs excluded from that rule, such as tests.

## gate_paths

Globs. A merge request that changes any of them needs a person's approval, whatever its tier:
`factory.yml`, `factory/**`, the CI entry files, `AGENTS.md`, `.kiro/**`, the designs folder,
migrations, dependency manifests and lock files, test configuration.

Into a feature branch, a gate path counts only when its content at the merge request's head
differs from the default branch's. A merge request that brings the default branch into a
feature branch (a sync) then needs no new approval for what a person already approved on the
default branch. A file missing on either side, or one the forge cannot read, still counts.

## scope

Which work the factory takes. Optional; without it, `full`.

```yaml
scope:
  mode: own              # own | full
  label: factory         # the label that puts an issue or merge request in the factory's stream
  adopt_wait_hours: 24   # an adopted merge request: how long its author has to answer first
  adopt_per_day: 5       # adopted merge requests started per UTC day
```

- `mode`: `full` (default): every issue and merge request, as before. `own`: only the
  factory's own stream, for a repository the team also works in by hand
  (onboarding, in the factory package's `kirocrew-factory` skill):
  - the triager sorts and the crew takes only issues that carry `label`;
  - a merge request into the default branch is in scope when it carries `label` or closes
    (`Fixes #N`) an issue that carries it; one into a feature branch always is. The label
    counts only when its latest add (GitLab label events, GitHub timeline) was by an account
    in `humans`: anyone who may edit labels could otherwise put a T1 into an automatic merge;
  - out of scope, the merger does not merge, rebase, comment on or report it (once, it says
    that a `label` added by someone outside `humans` was ignored), and
    `factory-tier` reports `FACTORY_IN_SCOPE=0`: `factory-docs` and the AI reviews then skip
    (GitLab: exit 3, allowed to fail, shown as a warning; GitHub: the jobs are skipped). The
    red-main watch still runs.
- `label`: `factory` by default. A person in `humans` puts it on; from anyone else, including
  the crew, a tool or an import, it does not count until a person removes it and puts it on
  again.
- `adopt_wait_hours`, `adopt_per_day`: for **adopted** merge requests, those in scope only
  through their own label (a person's merge request a person labelled; the crew's merge
  requests always close their labelled issue and are never adopted). The merger comments once
  to the author, at most `adopt_per_day` such comments per UTC day; waits `adopt_wait_hours`
  or until the author replies; then brings the branch up to date once (rebase or update when
  behind, else a new pipeline on the head, which on GitHub needs `actions: write` on the
  merger's token); and merges by the tier's rules only on checks that ran after its comment,
  so a green pipeline from before never counts. The author's latest reply containing "not
  yet" stops it; removing the label takes it out of scope.

A change of scope applies at the next tick of the merger and the next pipeline; one line takes
a repository back to `full` or to `own`.
