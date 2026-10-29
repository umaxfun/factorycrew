"""Exact linear programming on `fractions.Fraction` (two-phase dense-tableau simplex).

    solve(c, A_ub, b_ub, A_eq, b_eq, maximize=False, bounds=None)
        -> {"status": "optimal" | "infeasible" | "unbounded", "x", "objective", "iterations"}

minimises (or maximises) c.x subject to A_ub x <= b_ub, A_eq x == b_eq and per-variable bounds
(default 0 <= x, no upper bound). Every number is exact, so 2/3 stays 2/3 and an equality holds
exactly; inputs may be int, Fraction, "3/4" strings or floats (taken by their decimal repr,
0.1 -> 1/10).

Two input shapes:
- dense: c is a list of n numbers, each row a list of n numbers or a sparse {index: coef};
  x comes back as a list.
- named: c is a dict {var: coef}, each row a dict {var: coef} (missing = 0), bounds a dict
  {var: (lo, hi)}; variables are the union of every key, ordered by str(name); x comes back as
  a dict over all of them.

Phase 1 minimises the sum of artificial variables to find a feasible basis (infeasible if that
sum stays above 0), drives artificials out of the basis and drops redundant equality rows;
phase 2 optimises the objective. Pivoting is Dantzig's rule (most negative reduced cost, first
on ties) with a switch to Bland's rule (smallest index enters, smallest basic index leaves on a
ratio tie) once degenerate pivots repeat, so it cannot cycle. Deterministic.

`tableau_simplex` / `pivot` are the raw tableau primitives (used by skills/production).

Origin: the tableau simplex (`pivot`, `tableau_simplex`) is ported from Kirk McDonald's
Factorio calculator, https://github.com/KirkMcDonald/kirkmcdonald.github.io, `simplex.js`
(pivot, pivotCol, simplex), commit 349ebfb5f0fe09145169b4b47bc1771f55c1a896, Apache-2.0,
Copyright 2015-2021 Kirk McDonald; license text in skills/production/LICENSE-upstream.
Changes: JavaScript Rational -> Fraction, Bland's rule anti-cycling fallback, and the two-phase
driver, bounds and named variables of `solve` are new here (Kirk's tableau starts feasible).
"""
from __future__ import annotations

import math
from fractions import Fraction

ZERO = Fraction(0)
ONE = Fraction(1)


class Unbounded(ValueError):
    """The objective can grow without limit."""


class NotConverged(ValueError):
    """More than max_iter pivots."""


def F(x):
    """Exact Fraction from an int, float (by its decimal repr: 0.75 -> 3/4), "3/4" or Fraction."""
    if isinstance(x, Fraction):
        return x
    if isinstance(x, bool):
        return Fraction(int(x))
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"not a finite number: {x}")
        return Fraction(repr(x))
    if isinstance(x, str):
        return Fraction(x.strip())
    raise TypeError(f"not a number: {x!r}")


# ---------------------------------------------------------------------------- tableau (simplex.js)

def pivot(A, row, col):
    """Gauss-Jordan pivot of tableau A (list of Fraction rows) on A[row][col], in place."""
    x = A[row][col]
    A[row] = [v / x for v in A[row]]
    prow = A[row]
    for r in range(len(A)):
        if r == row:
            continue
        ratio = A[r][col]
        if ratio == 0:
            continue
        A[r] = [a - p * ratio for a, p in zip(A[r], prow)]


def tableau_simplex(A, basis=None, max_iter=100000, bland=False):
    """Maximise over a tableau in place (simplex.js `simplex`). The last row is the objective
    row (negated objective coefficients, reduced against the basis), the last column the
    right-hand side; the tableau must start feasible (RHS >= 0), `basis[r]` = the column basic
    in row r (None = unknown). Returns the number of pivots; A[-1][-1] is then the optimum.
    Raises Unbounded / NotConverged.

    Dantzig's rule (most negative column, first on ties) as in Kirk's code, plus Bland's rule
    once degenerate pivots repeat (or from the start with bland=True), so it cannot cycle."""
    rows, cols = len(A) - 1, len(A[0]) - 1
    if basis is None:
        basis = [None] * rows
    streak, n = 0, 0
    while True:
        obj = A[-1]
        col = None
        for c in range(cols):
            if obj[c] < 0 and (col is None or (not bland and obj[c] < obj[col])):
                col = c
                if bland:
                    break
        if col is None:
            return n
        best, best_row = None, None
        for r in range(rows):
            x = A[r][col]
            if x <= 0:
                continue
            ratio = A[r][-1] / x
            if best is None or ratio < best or (
                    bland and ratio == best and (basis[r] if basis[r] is not None else -1)
                    < (basis[best_row] if basis[best_row] is not None else -1)):
                best, best_row = ratio, r
        if best_row is None:
            raise Unbounded("failed to pivot: the problem is unbounded")
        pivot(A, best_row, col)
        basis[best_row] = col
        n += 1
        streak = streak + 1 if best == 0 else 0
        if streak > rows:
            bland = True
        if n > max_iter:
            raise NotConverged("simplex did not converge")


# ---------------------------------------------------------------------------- input shapes

def _key(v):
    return (str(v), type(v).__name__)


def _names(c, rows, bounds):
    names = set(c)
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("named variables (c is a dict): every constraint row must be a dict")
        names.update(row)
    if bounds is not None and not _uniform(bounds):
        if not isinstance(bounds, dict):
            raise TypeError("named variables (c is a dict): bounds must be a dict or one (lo, hi)")
        names.update(bounds)
    return sorted(names, key=_key)


def _uniform(bounds):
    return (isinstance(bounds, (tuple, list)) and len(bounds) == 2
            and all(b is None or not isinstance(b, (tuple, list, dict)) for b in bounds))


def _row(row, index, n, where):
    out = [ZERO] * n
    if isinstance(row, dict):
        for k, v in row.items():
            if k not in index:
                raise ValueError(f"{where}: unknown variable {k!r}")
            out[index[k]] += F(v)
    else:
        row = list(row)
        if len(row) != n:
            raise ValueError(f"{where}: {len(row)} coefficients for {n} variables")
        out = [F(v) for v in row]
    return out


def _pair(b, where):
    if b is None:
        return ZERO, None
    lo, hi = b
    return (None if lo is None else F(lo)), (None if hi is None else F(hi))


def _bounds(bounds, names, index, n):
    if bounds is None:
        return [(ZERO, None)] * n
    if _uniform(bounds):
        return [_pair(bounds, "bounds")] * n
    if isinstance(bounds, dict):
        out = [(ZERO, None)] * n
        for k, b in bounds.items():
            if k not in index:
                raise ValueError(f"bounds: unknown variable {k!r}")
            out[index[k]] = _pair(b, f"bounds[{k!r}]")
        return out
    bounds = list(bounds)
    if len(bounds) != n:
        raise ValueError(f"bounds: {len(bounds)} pairs for {n} variables")
    return [_pair(b, f"bounds[{j}]") for j, b in enumerate(bounds)]


def _problem(c, A_ub, b_ub, A_eq, b_eq, bounds):
    """-> (names or None, cost, ub rows, ub rhs, eq rows, eq rhs, bounds), all dense Fractions."""
    A_ub, A_eq = list(A_ub or []), list(A_eq or [])
    b_ub, b_eq = [F(v) for v in (b_ub or [])], [F(v) for v in (b_eq or [])]
    if len(A_ub) != len(b_ub):
        raise ValueError(f"A_ub has {len(A_ub)} rows, b_ub {len(b_ub)} values")
    if len(A_eq) != len(b_eq):
        raise ValueError(f"A_eq has {len(A_eq)} rows, b_eq {len(b_eq)} values")
    if isinstance(c, dict):
        names = _names(c, A_ub + A_eq, bounds)
        index = {v: j for j, v in enumerate(names)}
    else:
        c = list(c)
        names, index = None, {j: j for j in range(len(c))}
    n = len(index)
    cost = _row(c, index, n, "c")
    ub = [_row(r, index, n, f"A_ub[{i}]") for i, r in enumerate(A_ub)]
    eq = [_row(r, index, n, f"A_eq[{i}]") for i, r in enumerate(A_eq)]
    return names, cost, ub, b_ub, eq, b_eq, _bounds(bounds, names, index, n)


# ---------------------------------------------------------------------------- solve

def _result(status, x=None, objective=None, iterations=0):
    return {"status": status, "x": x, "objective": objective, "iterations": iterations}


def solve(c, A_ub=None, b_ub=None, A_eq=None, b_eq=None, maximize=False, bounds=None,
          max_iter=100000):
    """Optimise c.x subject to A_ub x <= b_ub, A_eq x == b_eq, bounds.

    bounds: None (every x >= 0), one (lo, hi) for all variables, a list of (lo, hi) per variable
    (dense) or {var: (lo, hi)} (named; unnamed variables keep 0 <= x). None in a pair = no limit
    on that side, so (None, None) is a free variable.
    Returns {"status", "x", "objective", "iterations"}: x (list, or dict for named variables)
    and objective are exact Fractions when status is "optimal", else None. iterations counts
    simplex pivots over both phases. Bad shapes raise ValueError / TypeError."""
    names, cost, ub, b_ub, eq, b_eq, bnd = _problem(c, A_ub, b_ub, A_eq, b_eq, bounds)
    n = len(cost)

    # x_j -> nonnegative columns y: x = lo + y, x = hi - y, or x = y+ - y-
    cols, shift, extra_ub = [], [], []  # cols[j] = [(y column, sign)]
    ny = 0
    for j, (lo, hi) in enumerate(bnd):
        if lo is not None and hi is not None and hi < lo:
            return _result("infeasible")
        if lo is not None:
            cols.append([(ny, ONE)])
            shift.append(lo)
            if hi is not None:
                extra_ub.append((ny, hi - lo))
            ny += 1
        elif hi is not None:
            cols.append([(ny, -ONE)])
            shift.append(hi)
            ny += 1
        else:
            cols.append([(ny, ONE), (ny + 1, -ONE)])
            shift.append(ZERO)
            ny += 2

    def transform(row, rhs):
        out = [ZERO] * ny
        for j, a in enumerate(row):
            if a:
                rhs -= a * shift[j]
                for y, s in cols[j]:
                    out[y] += a * s
        return out, rhs

    rows, kinds = [], []  # kinds: "ub" (gets a slack) or "eq"
    for row, rhs in zip(ub, b_ub):
        rows.append(transform(row, rhs))
        kinds.append("ub")
    for y, cap in extra_ub:
        line = [ZERO] * ny
        line[y] = ONE
        rows.append((line, cap))
        kinds.append("ub")
    for row, rhs in zip(eq, b_eq):
        rows.append(transform(row, rhs))
        kinds.append("eq")

    m = len(rows)
    nslack = kinds.count("ub")
    need_art = [kinds[i] == "eq" or rows[i][1] < 0 for i in range(m)]
    nart = sum(need_art)
    ncols = ny + nslack + nart
    A, basis = [], []
    s, a = ny, ny + nslack
    for i, (line, rhs) in enumerate(rows):
        t = line + [ZERO] * (nslack + nart) + [rhs]
        if kinds[i] == "ub":
            t[s] = ONE
            slack, s = s, s + 1
        else:
            slack = None
        if rhs < 0:
            t = [-v for v in t]
        if need_art[i]:
            t[a] = ONE
            basis.append(a)
            a += 1
        else:
            basis.append(slack)
        A.append(t)

    iterations = 0
    if nart:  # phase 1: maximise -(sum of artificials)
        obj = [ZERO] * ncols + [ZERO]
        for k in range(ny + nslack, ncols):
            obj[k] = ONE
        for r in range(m):
            if basis[r] >= ny + nslack:
                obj = [o - v for o, v in zip(obj, A[r])]
        A.append(obj)
        iterations += tableau_simplex(A, basis, max_iter)
        if A[-1][-1] != 0:
            return _result("infeasible", iterations=iterations)
        A.pop()
        keep = []
        for r in range(m):
            if basis[r] >= ny + nslack:  # artificial at 0: swap in any real column, else redundant
                k = next((k for k in range(ny + nslack) if A[r][k] != 0), None)
                if k is None:
                    continue
                pivot(A, r, k)
                basis[r] = k
            keep.append(r)
        A = [A[r][:ny + nslack] + [A[r][-1]] for r in keep]
        basis = [basis[r] for r in keep]
        ncols = ny + nslack

    gain = [ZERO] * ny  # phase 2 maximises gain.y
    for j, cj in enumerate(cost):
        for y, sgn in cols[j]:
            gain[y] += (cj if maximize else -cj) * sgn
    obj = [-g for g in gain] + [ZERO] * (ncols - ny) + [ZERO]
    for r, b in enumerate(basis):
        if obj[b] != 0:
            f = obj[b]
            obj = [o - f * v for o, v in zip(obj, A[r])]
    A.append(obj)
    try:
        iterations += tableau_simplex(A, basis, max_iter)
    except Unbounded:
        return _result("unbounded", iterations=iterations)

    y = [ZERO] * ny
    for r, b in enumerate(basis):
        if b < ny:
            y[b] = A[r][-1]
    x = [shift[j] + sum((y[k] * sgn for k, sgn in cols[j]), ZERO) for j in range(n)]
    objective = sum((cj * xj for cj, xj in zip(cost, x)), ZERO)
    if names is not None:
        x = {v: x[j] for j, v in enumerate(names)}
    return _result("optimal", x, objective, iterations)


# ---------------------------------------------------------------------------- output / checks

def _conv(v, fn):
    if isinstance(v, Fraction):
        return fn(v)
    if isinstance(v, dict):
        return {k: _conv(x, fn) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_conv(x, fn) for x in v]
    return v


def as_float(result):
    """Copy of a solve() result with every Fraction as float (for display)."""
    return _conv(result, float)


def as_str(result):
    """Copy of a solve() result with every Fraction as an exact string ("2/3"), JSON-safe."""
    return _conv(result, str)


def check(result, constraints):
    """Problems with a solve() result against the problem it should satisfy; [] = all good.

    constraints: the solve() keyword arguments as a dict, {"c"?, "A_ub"?, "b_ub"?, "A_eq"?,
    "b_eq"?, "bounds"?, "maximize"?}. Every constraint and bound must hold exactly, and with "c"
    the reported objective must equal c.x. x may be Fractions or as_str() strings; as_float()
    floats are taken at their decimal value, so they rarely pass."""
    status = result.get("status")
    if status != "optimal":
        return [f"no solution: status {status!r}"]
    x = result.get("x")
    c = constraints.get("c")
    if c is None:
        c = {k: 0 for k in x} if isinstance(x, dict) else [0] * len(x)
    names, cost, ub, b_ub, eq, b_eq, bnd = _problem(
        c, constraints.get("A_ub"), constraints.get("b_ub"), constraints.get("A_eq"),
        constraints.get("b_eq"), constraints.get("bounds"))
    problems = []
    if names is None:
        if isinstance(x, dict) or len(x) != len(cost):
            return [f"x has {len(x)} values, the problem {len(cost)} variables"]
        xv = [F(v) for v in x]
        label = [f"x[{j}]" for j in range(len(xv))]
    else:
        if not isinstance(x, dict):
            return ["x must be a dict for named variables"]
        missing = [v for v in names if v not in x]
        if missing:
            return [f"x has no value for {v!r}" for v in missing]
        xv = [F(x[v]) for v in names]
        label = [f"x[{v!r}]" for v in names]
    for i, (row, rhs) in enumerate(zip(ub, b_ub)):
        lhs = sum((a * v for a, v in zip(row, xv)), ZERO)
        if lhs > rhs:
            problems.append(f"A_ub[{i}]: {lhs} > {rhs}")
    for i, (row, rhs) in enumerate(zip(eq, b_eq)):
        lhs = sum((a * v for a, v in zip(row, xv)), ZERO)
        if lhs != rhs:
            problems.append(f"A_eq[{i}]: {lhs} != {rhs}")
    for j, (lo, hi) in enumerate(bnd):
        if lo is not None and xv[j] < lo:
            problems.append(f"{label[j]} = {xv[j]} < lower bound {lo}")
        if hi is not None and xv[j] > hi:
            problems.append(f"{label[j]} = {xv[j]} > upper bound {hi}")
    if constraints.get("c") is not None:
        want = sum((a * v for a, v in zip(cost, xv)), ZERO)
        got = result.get("objective")
        if got is None or F(got) != want:
            problems.append(f"objective {got} != c.x = {want}")
    return problems
