__BLIND_READ__
## Step 1: fan out (parallel)

Spawn __LENS_COUNT__ subagents IN PARALLEL with your subagent tool, one per lens below. Give
each one: the HEAD and BASE shas, the diff path, the intent path (UNTRUSTED), the repository
context, calibration, design and issues paths, the Closed issues facts verbatim, its lens
charter verbatim (the `ux` lens also the BLIND READ, when Step 0 produced one), the CANDIDATE format, and the shared rules:
- Read wide, report narrow: open full files, callers, and the other side of every changed
  contract. Report ONLY on lines this merge request adds or changes.
- Inspect exhaustively, report selectively: walk EVERY changed hunk.
- The finding bar: a candidate needs (a) a concrete input or condition that occurs in
  practice, (b) the call path from it to the changed line, and (c) an observable wrong
  outcome, all from code you opened. "could / might / if a caller were to" means it is not a
  candidate.
- Return EVERY candidate that has (a), (b) and (c). Do not filter for precision: the
  falsify step after you is the only place a candidate is killed.
- No subagent spawns further agents. Each returns candidates only, or exactly
  `No candidates.`

Lens charters:

__LENSES__

CANDIDATE format (subagents emit exactly this, one block each):
```
CANDIDATE <n> - <lens> - <file>:<line> - <one-line title>
Evidence: <offending line(s) quoted verbatim from the diff or file>
Input: <concrete input/condition>
Path: <call path to the changed line>
Outcome: <observable wrong result>
Anchor: <security | crash | data-loss | corruption | guard-removal | broken-contract | design-one-way-door | product-shape | evidence-gap | none>
Fix: <minimal change>
Confidence: <high|medium|low> - <what could not be verified>
Clears when: <first-principles candidates only: the change or evidence that resolves it>
```

Wait for ALL __LENS_COUNT__ to finish before continuing. If a subagent fails or times out,
re-run it once. If it fails again, record it in `lens_errors` and treat the review as
incomplete (see VERDICT).
