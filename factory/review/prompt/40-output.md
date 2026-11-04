
## OUTPUT

Your LAST message is the review, captured verbatim. Write nothing before it. No preamble, no
restating the diff, no methodology narration, no praise, no summary.

1. One bold punchline line: `**No findings.**`, or the single main reason it blocks.
2. BLOCKING items first, each 2-4 lines:
   `BLOCKING - <file>:<line> - <title>` / quoted line(s) / `input -> path -> outcome` /
   `Anchor: ...` / `Fix: ...`
3. Then each FINDING on ONE line: `FINDING - <file>:<line> - <consequence> -> Fix: ...`, and for
   one Step 4 downgraded, ` Downgraded: <its ruling>` at the end.
4. A fenced JSON block, exactly one, in this schema:
```json
{
  "head_sha": "__HEAD_SHA__",
  "base_sha": "__BASE_SHA__",
  "verdict": "PASS|BLOCK",
  "lenses_completed": [__LENS_NAMES__],
  "lens_errors": [],
  "findings": [
    {
      "id": "F1",
      "severity": "blocking|advisory",
      "lenses": ["security"],
      "anchor": "security|crash|data-loss|corruption|guard-removal|broken-contract|design-one-way-door|product-shape|evidence-gap|none",
      "file": "path/to/file",
      "line": 42,
      "headline": "<conclusion, one sentence, < 100 chars, no path/severity word>",
      "consequence": "<input -> path -> observable outcome>",
      "fix": "<minimal change>",
      "clears_when": "<concrete evidence or change that resolves it>",
      "origin": "lens|validation",
      "downgraded": "<only on a finding Step 4 downgraded: its ruling, one line>"
    }
  ]
}
```
5. The marker line `[FACTORY-REVIEWED] __HEAD_SHA__`.
6. The LAST line, exactly one of:
   `VERDICT: PASS`  (zero blocking findings AND all __LENS_COUNT__ lenses completed)
   `VERDICT: BLOCK` (>= 1 blocking finding, OR any lens in lens_errors)
Write NOTHING after the VERDICT line.
