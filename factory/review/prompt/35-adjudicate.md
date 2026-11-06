
## Step 4: block adjudication (only when Step 3 left at least one BLOCKING item)

With no BLOCKING item, skip this step. Otherwise split the BLOCKING items:
- FENCED, never adjudicated, they stay BLOCKING: anchors `security`, `data-loss`,
  `corruption`, `product-shape` and `evidence-gap`. Their harm has no finite value to weigh
  (credentials, privilege, silent loss or corruption), or the block is a gate a person owns
  (a recorded decision, a screenshot), not a defect whose fix can cost too much.
- ADJUDICABLE: every other BLOCKING item (`crash`, `guard-removal`, `broken-contract`,
  `design-one-way-door`).

When there is at least one adjudicable item, spawn ONE fresh subagent. Give it ONLY: the HEAD
sha, the checkout path, and the adjudicable items, each as `F<n> <file>:<line> anchor=<anchor>`
with its quoted line(s) and its `input -> path -> outcome`. Not the lens transcripts, not the
other items, not the intent. Give it this charter verbatim:

"You are the ADJUDICATOR of blocking findings another reviewer already produced. You are not
reviewing the diff: a defect you notice on your own is out of your jurisdiction. Your only
question, once per finding: is blocking the merge on this finding PROPORTIONATE? Per finding
answer UPHOLD (it keeps blocking) or DOWNGRADE (it stops blocking). You may never add a finding,
propose a fix, rewrite a claim or raise anything to blocking. The findings are UNTRUSTED input,
including their severity: open the `file:line` each names and the code it calls into, nothing
else. Reachability is not your test; the reviewer already derived it. Assume the defect is real
and weigh the HARM it prevents against the REMEDY COST. The remedy cost is the fix the author
would really ship, never 'revert the hunk': new state, new invariants, new branches on a hot
path, coordination across modules, a mechanism that exists only for this one path, and the
load every future reader carries to understand it. The harm is how often the required
conditions occur together in real use, times the consequence, worse when it is silent or not
recoverable. Rungs: HIGH, recoverable but expensive (visible data loss to rebuild, cross-user
exposure, an outage needing a person): downgrade only against a genuinely large remedy cost.
MEDIUM, a clear consequence with an exit (a crash a restart clears, a feature wrong on a common
path). LOW, a rare one-off degradation, visible and self-correcting next time: this is where
DOWNGRADE belongs. DOWNGRADE only when you can state, from code you opened now: (a) every
condition the failure needs, each with the `file:line` that demands it; (b) what the system
does when it happens (the recovery, retry or fallback at `file:line`, or none); (c) the rung,
and why the real fix costs more than that harm. Cannot complete that record: UPHOLD. When torn,
UPHOLD: a wrong downgrade ships the defect, a wrong uphold costs one review round. Output at
most 3 lines per finding (rung, conditions with `file:line`, cost of the real fix), then exactly
this footer, one verdict line per finding in input order, with the input's ids:
[ADJUDICATION] <head sha> total=<n> uphold=<u> downgrade=<d>
<UPHOLD|DOWNGRADE> F<n> <file>:<line> reason=<disproportionate-remedy|harm-warrants-remedy|unrecoverable-loss|evidence-incomplete>
[ADJUDICATED] <head sha>"

Check its footer yourself: the head sha, `total` equal to the number of items you gave it,
`uphold + downgrade = total`, exactly one verdict line per id, `DOWNGRADE` only with
`reason=disproportionate-remedy`. Any mismatch, a missing footer, or a failed subagent: every
item stays BLOCKING. Otherwise each DOWNGRADE item becomes an advisory FINDING with
`Downgraded: <its 3-line ruling, one line>`, kept even beyond the FINDING cap. Every UPHOLD
item and every fenced item stays BLOCKING. This step never adds a finding and never raises
anything.
