"""kc.lp: exact two-phase simplex on Fractions (mcp/kc/lp.py)."""
from __future__ import annotations

import itertools
import json
import random
import sys
from fractions import Fraction as Fr
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "skills" / "production" / "lib"))
import kc  # noqa: E402
from kc import lp  # noqa: E402


def ok(r, problem):
    assert r["status"] == "optimal", r
    assert lp.check(r, problem) == [], lp.check(r, problem)
    return r


def run(**problem):
    return ok(lp.solve(**problem), problem)


# ---------------------------------------------------------------- textbook optima

def test_package_exposes_lp():
    assert kc.lp is lp


def test_dantzig_example_max():
    # max 3x + 5y, x <= 4, 2y <= 12, 3x + 2y <= 18 -> (2, 6), 36
    r = run(c=[3, 5], A_ub=[[1, 0], [0, 2], [3, 2]], b_ub=[4, 12, 18], maximize=True)
    assert r["x"] == [2, 6] and r["objective"] == 36
    assert all(isinstance(v, Fr) for v in r["x"]) and isinstance(r["objective"], Fr)


def test_min_with_ge_and_equality_needs_phase_one():
    # min 2x + 3y, x/2 + y/4 <= 4, x + 3y >= 20, x + y = 10 -> (5, 5), 25
    r = run(c=[2, 3], A_ub=[["1/2", "1/4"], [-1, -3]], b_ub=[4, -20], A_eq=[[1, 1]], b_eq=[10])
    assert r["x"] == [5, 5] and r["objective"] == 25 and r["iterations"] > 0


def test_diet_min_cost():
    # min 0.6a + 0.35b, 5a + 7b >= 8, 4a + 2b >= 15, 2a + b >= 3 -> a = 15/4, b = 0
    r = run(c=[0.6, 0.35], A_ub=[[-5, -7], [-4, -2], [-2, -1]], b_ub=[-8, -15, -3])
    assert r["x"] == [Fr(15, 4), 0] and r["objective"] == Fr(9, 4)


def test_exact_thirds_stay_exact():
    # max x + y, 3x + y <= 1, x + 3y <= 1 -> (1/4, 1/4)
    r = run(c=[1, 1], A_ub=[[3, 1], [1, 3]], b_ub=[1, 1], maximize=True)
    assert r["x"] == [Fr(1, 4), Fr(1, 4)] and r["objective"] == Fr(1, 2)


def test_floats_are_read_by_their_decimal_value():
    r = run(c=[1], A_ub=[[-1]], b_ub=[-0.1])
    assert r["x"] == [Fr(1, 10)]


# ---------------------------------------------------------------- status

def test_infeasible_inequalities():
    r = lp.solve([1], A_ub=[[1], [-1]], b_ub=[1, -2])  # x <= 1 and x >= 2
    assert r["status"] == "infeasible" and r["x"] is None and r["objective"] is None
    assert lp.check(r, {}) == ["no solution: status 'infeasible'"]


def test_infeasible_equalities():
    r = lp.solve([0, 0], A_eq=[[1, 1], [1, 1]], b_eq=[1, 2])
    assert r["status"] == "infeasible"


def test_infeasible_bounds():
    assert lp.solve([1], bounds=[(3, 2)])["status"] == "infeasible"
    assert lp.solve({"a": 1}, bounds={"a": (0, 1)}, A_ub=[{"a": -1}], b_ub=[-2])["status"] == "infeasible"


def test_unbounded():
    r = lp.solve([1, 1], A_ub=[[1, -1]], b_ub=[1], maximize=True)
    assert r["status"] == "unbounded" and r["x"] is None


def test_unbounded_without_constraints_and_bounded_without_constraints():
    assert lp.solve([-1])["status"] == "unbounded"
    r = lp.solve([1, 2])
    assert r["status"] == "optimal" and r["x"] == [0, 0] and r["objective"] == 0


def test_unbounded_free_variable():
    assert lp.solve([1], bounds=[(None, None)])["status"] == "unbounded"


def test_empty_problem():
    r = lp.solve([])
    assert r["status"] == "optimal" and r["x"] == [] and r["objective"] == 0


# ---------------------------------------------------------------- bounds and equalities

def test_bounds_shift_flip_and_split():
    # min x0 + 2 x1, x0 + x1 >= 3, x0 free, 1 <= x1 <= 2 -> (2, 1), 4
    r = run(c=[1, 2], A_ub=[[-1, -1]], b_ub=[-3], bounds=[(None, None), (1, 2)])
    assert r["x"] == [2, 1] and r["objective"] == 4
    r = run(c=[1], bounds=[(-3, None)])
    assert r["x"] == [-3]
    r = run(c=[1], bounds=[(None, 4)], maximize=True)
    assert r["x"] == [4]
    r = run(c=[1, 1], bounds=(-1, 1))  # one pair for every variable
    assert r["x"] == [-1, -1] and r["objective"] == -2


def test_equality_with_negative_rhs_and_free_variable():
    r = run(c=[1, 0], A_eq=[[1, -1]], b_eq=[-5], bounds=[(None, None), (0, 2)])
    assert r["x"] == [-5, 0]


def test_redundant_equality_rows_are_dropped():
    r = run(c=[1, 0], A_eq=[[1, 1], [2, 2], [3, 3]], b_eq=[2, 4, 6])
    assert r["x"] == [0, 2] and r["objective"] == 0


# ---------------------------------------------------------------- degeneracy / cycling

BEALE = dict(c=["-3/4", 20, "-1/2", 6],
             A_ub=[["1/4", -8, -1, 9], ["1/2", -12, "-1/2", 3], [0, 0, 1, 0]],
             b_ub=[0, 0, 1])


def _dantzig_cycles(problem):
    """Plain Dantzig, first row on ratio ties, no fallback: does the basis repeat?"""
    A = [[Fr(v) for v in row] + [Fr(1) if k == i else Fr(0) for k in range(3)] + [Fr(b)]
         for i, (row, b) in enumerate(zip(problem["A_ub"], problem["b_ub"]))]
    A.append([Fr(v) for v in problem["c"]] + [Fr(0)] * 4)  # minimise c -> max -c: row = c
    basis, seen = [4, 5, 6], set()
    for _ in range(50):
        if tuple(basis) in seen:
            return True
        seen.add(tuple(basis))
        obj = A[-1][:-1]
        col = min(range(len(obj)), key=lambda k: (obj[k], k))
        if obj[col] >= 0:
            return False
        rows = [r for r in range(3) if A[r][col] > 0]
        row = min(rows, key=lambda r: (A[r][-1] / A[r][col], r))
        lp.pivot(A, row, col)
        basis[row] = col
    return False


def test_beale_cycles_under_plain_dantzig_but_solve_terminates():
    assert _dantzig_cycles(BEALE)  # the case really cycles with this tie rule
    r = run(**BEALE)
    assert r["objective"] == Fr(-5, 4) and r["x"] == [1, 0, 1, 0]


def test_bland_from_the_start_also_solves_beale():
    A = [[Fr(v) for v in row] + [Fr(1) if k == i else Fr(0) for k in range(3)] + [Fr(b)]
         for i, (row, b) in enumerate(zip(BEALE["A_ub"], BEALE["b_ub"]))]
    A.append([Fr(v) for v in BEALE["c"]] + [Fr(0)] * 4)
    lp.tableau_simplex(A, [4, 5, 6], bland=True)
    assert A[-1][-1] == Fr(5, 4)  # max of -c.x


def test_degenerate_vertex():
    # three constraints through (1, 1) in 2-D
    r = run(c=[1, 1], A_ub=[[1, 0], [0, 1], [1, 1]], b_ub=[1, 1, 2], maximize=True)
    assert r["x"] == [1, 1] and r["objective"] == 2


def test_tableau_simplex_raises_on_unbounded_and_max_iter():
    A = [[Fr(1), Fr(-1), Fr(1), Fr(0), Fr(1)], [Fr(-1), Fr(-1), Fr(0), Fr(0), Fr(0)]]
    with pytest.raises(lp.Unbounded) as e:
        lp.tableau_simplex(A, [2])
    assert isinstance(e.value, ValueError)
    A = [[Fr(1), Fr(1), Fr(4)], [Fr(-1), Fr(0), Fr(0)]]
    with pytest.raises(lp.NotConverged):
        lp.tableau_simplex(A, [1], max_iter=0)


# ---------------------------------------------------------------- named / sparse

def test_named_variables_return_a_dict():
    problem = dict(c={"a": 3, "b": 2},
                   A_ub=[{"a": 1, "b": 1}, {"a": 1, "b": 3}], b_ub=[4, 6], maximize=True)
    r = run(**problem)
    assert r["x"] == {"a": 4, "b": 0} and r["objective"] == 12


def test_named_variables_only_in_rows_or_bounds_are_reported():
    r = run(c={"a": 1}, A_ub=[{"a": -1, "b": 1}], b_ub=[-1], bounds={"z": (2, 5)})
    assert r["x"] == {"a": 1, "b": 0, "z": 2}


def test_sparse_dense_rows():
    r = run(c=[0, 0, -1], A_ub=[{2: 1}, {0: 1, 2: -1}], b_ub=[7, 0])
    assert r["x"] == [0, 0, 7]


def test_bad_shapes_raise():
    with pytest.raises(ValueError):
        lp.solve([1, 2], A_ub=[[1]], b_ub=[1])
    with pytest.raises(ValueError):
        lp.solve([1], A_ub=[[1]], b_ub=[])
    with pytest.raises(ValueError):
        lp.solve([1], A_ub=[{3: 1}], b_ub=[1])
    with pytest.raises(TypeError):
        lp.solve({"a": 1}, A_ub=[[1]], b_ub=[1])


# ---------------------------------------------------------------- production-like flow

OIL = dict(  # rates per craft/s: aop, heavy cracking, light cracking; minimise crude
    c={"aop": 100, "heavy": 0, "light": 0},
    A_ub=[{"aop": -25, "heavy": 40},                # heavy oil: produced >= cracked
          {"aop": -45, "heavy": -30, "light": 30},  # light oil
          {"aop": -55, "light": -20}],              # petroleum >= 97.5
    b_ub=[0, 0, "-97.5"])


def test_oil_cracking_flow():
    r = run(**OIL)
    assert r["x"] == {"aop": 1, "heavy": Fr(5, 8), "light": Fr(17, 8)} and r["objective"] == 100


def test_oil_flow_with_exact_balance_equalities():
    eq = dict(c=OIL["c"], A_eq=OIL["A_ub"], b_eq=OIL["b_ub"])
    r = run(**eq)
    assert r["x"] == {"aop": 1, "heavy": Fr(5, 8), "light": Fr(17, 8)}


# ---------------------------------------------------------------- determinism / output / check

def test_deterministic_and_independent_of_dict_order():
    p1 = dict(c={"x": 1, "y": 1}, A_ub=[{"x": 1, "y": 1}], b_ub=[1], maximize=True)
    p2 = dict(c={"y": 1, "x": 1}, A_ub=[{"y": 1, "x": 1}], b_ub=[1], maximize=True)
    a, b, c = lp.solve(**p1), lp.solve(**p1), lp.solve(**p2)
    assert a == b == c


def test_as_str_and_as_float_are_json_and_check_reads_strings():
    problem = dict(c=[1, 1], A_ub=[[3, 1], [1, 3]], b_ub=[1, 1], maximize=True)
    r = lp.solve(**problem)
    s, f = lp.as_str(r), lp.as_float(r)
    json.dumps(s), json.dumps(f)
    assert s["x"] == ["1/4", "1/4"] and s["objective"] == "1/2" and f["x"] == [0.25, 0.25]
    assert r["x"] == [Fr(1, 4), Fr(1, 4)]  # the original is untouched
    assert lp.check(s, problem) == []
    named = lp.as_str(lp.solve(**OIL))
    assert named["x"]["heavy"] == "5/8" and lp.check(named, OIL) == []


def test_check_reports_violations():
    problem = dict(c=[1, 1], A_ub=[[1, 1]], b_ub=[1], A_eq=[[1, -1]], b_eq=[0],
                   bounds=[(0, None), (0, "1/4")], maximize=True)
    r = lp.solve(**problem)
    assert r["x"] == [Fr(1, 4), Fr(1, 4)]
    bad = dict(r, x=[Fr(1), Fr(1, 2)])
    probs = lp.check(bad, problem)
    assert any(p.startswith("A_ub[0]") for p in probs)
    assert any(p.startswith("A_eq[0]") for p in probs)
    assert any("upper bound" in p for p in probs)
    assert any(p.startswith("objective") for p in probs)
    assert lp.check(dict(r, objective=Fr(3)), problem) == ["objective 3 != c.x = 1/2"]
    assert lp.check(dict(r, x=[Fr(1, 4)]), problem)
    assert lp.check({"status": "optimal", "x": {"a": 1}}, OIL)[0].startswith("x has no value")


# ---------------------------------------------------------------- brute-force cross-check

def _brute(c, A, b, maximize):
    """Best vertex of {A x <= b, x >= 0} (n <= 3) by solving every n-subset as equalities."""
    n = len(c)
    rows = [list(r) for r in A] + [[-Fr(int(i == j)) for j in range(n)] for i in range(n)]
    rhs = list(b) + [Fr(0)] * n
    best = None
    for sub in itertools.combinations(range(len(rows)), n):
        M = [rows[i][:] + [rhs[i]] for i in sub]
        try:
            for k in range(n):
                p = next(r for r in range(k, n) if M[r][k] != 0)
                M[k], M[p] = M[p], M[k]
                M[k] = [v / M[k][k] for v in M[k]]
                for r in range(n):
                    if r != k and M[r][k] != 0:
                        M[r] = [a - M[r][k] * q for a, q in zip(M[r], M[k])]
        except StopIteration:
            continue
        x = [M[k][-1] for k in range(n)]
        if all(sum(a * v for a, v in zip(row, x)) <= r for row, r in zip(rows, rhs)):
            val = sum(ci * xi for ci, xi in zip(c, x))
            if best is None or (val > best if maximize else val < best):
                best = val
    return best


def test_random_lps_match_vertex_enumeration():
    rng = random.Random(7)
    seen = {"optimal": 0, "infeasible": 0}
    for _ in range(150):
        n, m = rng.randint(1, 3), rng.randint(1, 4)
        c = [Fr(rng.randint(-5, 5)) for _ in range(n)]
        A = [[Fr(rng.randint(-4, 4), rng.randint(1, 3)) for _ in range(n)] for _ in range(m)]
        b = [Fr(rng.randint(-6, 8)) for _ in range(m)]
        A += [[Fr(int(i == j)) for j in range(n)] for i in range(n)]  # box x <= 6: bounded
        b += [Fr(6)] * n
        maximize = rng.random() < 0.5
        r = lp.solve(c, A, b, maximize=maximize)
        want = _brute(c, A, b, maximize)
        seen[r["status"]] += 1
        if want is None:
            assert r["status"] == "infeasible", (c, A, b)
        else:
            assert r["status"] == "optimal" and r["objective"] == want, (c, A, b, r, want)
            assert lp.check(r, {"c": c, "A_ub": A, "b_ub": b}) == []
    assert seen["optimal"] > 50 and seen["infeasible"] > 5, seen


# ---------------------------------------------------------------- production uses this simplex

def test_production_uses_kc_lp():
    import production
    assert production.simplex is lp.tableau_simplex and production._pivot is lp.pivot
