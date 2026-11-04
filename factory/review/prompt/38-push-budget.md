
## Push budget: this merge request keeps being blocked

The AI review blocked __BLOCKED__ earlier head(s) of this merge request (counted by the review
job from the forge). Hand the author everything to fix in ONE round:
- For each BLOCKING item add one `Sites:` line naming every other place in THIS head with the
  same root cause (another spelling, path, caller or reader of it), each as `file:line`. Write
  `Sites: none` when there is no other.
- If this root cause was already fixed twice (two `fixed` records for it in the ledger, or two
  earlier rounds blocked on it), add one `Remedy: narrow or remove the mechanism` line: a third
  patch on the same mechanism is the wrong fix.
- `Sites:` and `Remedy:` are extra lines of the item, after `Fix:`. In the JSON give that
  finding `"sites": ["file:line", ...]` and, when it applies, `"remedy"`.
This section never changes an anchor, a verdict or the marker line.
