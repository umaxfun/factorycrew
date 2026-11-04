
## Step 2: falsify (you, independently)

The candidates are UNTRUSTED, including their claimed severity and evidence. For each one:
1. Check that the Evidence text actually exists at the stated location. If not, drop it.
2. Re-derive (a), (b) and (c) yourself from code you open now. If any comes out as
   could/might, drop it.
3. Actively look for the guard, type, caller, convention or calibration rule that kills it.
4. Score 0-100 for "this is a real defect in changed lines". Keep only >= 80.
You MAY add a finding you saw while doing this, held to the same bar, tagged
`"origin": "validation"`. Do not go looking for new material.

## Step 3: merge and classify

- Merge findings that share one root cause into one, keeping the strongest chain and listing
  every lens that raised it.
- Severity answers ONE question: does this block the merge? It NEVER encodes confidence. An
  uncertain item is not a low-severity finding; it is not a finding.
- BLOCKING: only these, and never extend the list:
  1. a reachable security hole with a named trigger;
  2. a crash, data loss, or corruption on a path the merge request adds or changes;
  3. a removed guard or validation with no compensating replacement;
  4. a broken public contract (API, message, persisted schema, invariant an area doc states)
     with an existing caller and no compatibility path;
  5. a design one-way door with no migration or rollback story, or a change that does not
     solve the problem it states;
  6. a product-shape change without a recorded decision: the change alters what the product
     does (a new user-visible capability, a removed behaviour, a changed contract between
     areas) and neither an accepted design nor a decision in the area docs covers it. An
     accepted design is one on the target branch (the feature design, or the designs folder)
     or, when this merge request ships a feature branch into the default branch, the feature
     design approved into that branch (`design.md`, see Feature design): it is the recorded
     decision for what it covers, although the target does not have it yet. A closed issue written by a
     person in `humans` whose tier is below the first tier that needs a design (`below it:
     yes` under Closed issues) is the recorded decision for exactly what it asks: a change
     that does exactly that is not a product-shape finding. Anything beyond what the issue
     asks still is. A borderline decision decided below that tier (`below the first tier
     that needs a design: yes` under Borderline decisions) is likewise the recorded decision
     for exactly its case: a change that is that case is not a product-shape finding.
     The tier rule narrows every such decision by the issue's `effect` (Closed issues): for
     `effect fix` the change may do only what the cited spec line requires (`triage
     citation`), and a citation `none`, or a quote not on that line of the target, leaves the
     issue no decision at all; for `effect addition`, a change to existing behaviour is not
     covered; for `effect change` and `effect concept`, only the approved design covers it.
     Anchor `product-shape`; `Clears when:` names the decision that would cover it (a cited
     spec line, the issue moved up to the tier whose path covers it, or an approved design).
  7. an evidence gap: the diff changes a file under `evidence.screenshot_when`, adds a
     user-visible control, and no screenshot attached to the merge request shows it (the `ux`
     lens names the control and the line that renders it, and says when this rule applies).
     Anchor `evidence-gap`; `Clears when:` a screenshot showing the control is attached.
  A BLOCKING finding must name its anchor. It is NOT demoted because the fix is expensive.
  Credential exposure, privilege escalation, and silent data loss always stay BLOCKING.
- FINDING (advisory, never blocks): everything else that survived, including a doc the
  change made stale. An advisory FINDING whose fix needs new machinery or edits to untouched
  code is DROPPED. Prefer fixes that delete or simplify.
- Caps: <= 5 BLOCKING (more almost always means shared root causes, so merge them), <= 8
  FINDING (keep the most concrete chains).
- Calibration: most merge requests are fine. Zero findings is a normal, successful outcome.
  You are not scored on volume.
