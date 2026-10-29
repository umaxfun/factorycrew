# Origin of lib/production.py

## Ported: Kirk McDonald's Factorio calculator

- Upstream: https://github.com/KirkMcDonald/kirkmcdonald.github.io
- Commit read: `349ebfb5f0fe09145169b4b47bc1771f55c1a896` (master, read 2026-10-29)
- License: Apache-2.0 (`gh api repos/KirkMcDonald/kirkmcdonald.github.io --jq .license.spdx_id` and the
  repo's LICENSE file, copied verbatim as the first part of `LICENSE-upstream`).
  Copyright 2015-2021 Kirk McDonald.
- Files ported: `simplex.js` (pivot, pivotCol, simplex), `solve.js` (traverse, recursiveSolve, the
  tableau layout and the lexicographic priority costs, cost_ratio), `cycle.js` (getCycleRecipes,
  Kosaraju), `recipe.js` (`gives`, for the walk).

What changed:
- JavaScript `Rational` (BigInteger) -> Python `fractions.Fraction`; dense list-of-lists tableau.
- Kosaraju's DFS is iterative (no recursion limit) and visits neighbours in sorted order (deterministic).
- Raw items: Kirk has a mining/pumpjack/offshore-pump recipe for every resource; here an item no recipe
  makes (or one passed in `raw`) gets an import row priced by `raw_priority` levels, the same role and
  the same default order (water level 0, other resources level 1).
- Every other LP item also gets an import row at the highest cost (Kirk only does this for items inside
  a cycle with no outside producer, `disableRecipe`), so the LP is always feasible; such imports are
  reported with a warning.
- The "tax" column/row of Kirk's tableau is folded into the cost: it adds 1 per craft/s to every row,
  which is what it does in the primal.
- Bland's rule after a run of degenerate pivots (Kirk uses Dantzig's rule only).
- `eliminateNegativeBases` is not ported: Kirk's solve() never calls it, the dual tableau starts feasible.
- Building-count targets (pseudo columns) and fuel consumers are not ported.
- Productivity uses Factorio 2.0 `ignored_by_productivity` (FactorioLab's catalyst rule) instead of
  Kirk's 1.1-era "net output" rule.

## Ported rules: FactorioLab

- Upstream: https://github.com/factoriolab/factoriolab
- Commit read: `eef44c6be28e251a22a211c2302ed43368d09ed0` (main, read 2026-10-29), file `src/state/adjustment.ts`
- License: MIT (`gh api` + LICENSE read; copied verbatim as the second part of `LICENSE-upstream`).
  Copyright 2020-2026 Doug Broad.
- Ported: the effect sum (research productivity + machine base effect + modules x count + beacons:
  module effect x count x distribution effectivity x beacon count x profile[total beacons - 1]), the
  20% floor on speed / consumption / pollution, the productivity clamp, and productivity applied to
  `amount - catalyst` only.

What changed: the clamp uses each recipe's `maximum_productivity` (2.0, default 3 = +300%) instead of a
fixed factor 4; no effect-precision truncation (exact fractions); recipes / machines whose
`allowed_effects` exclude a beneficial effect drop it (a module with such an effect is left out with a
warning); quality is summed but not fanned out into per-quality items; no Satisfactory/DSP branches.
