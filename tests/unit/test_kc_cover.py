"""kc.cover: greedy set cover, supply squares, candidate positions. Pure Python, no game."""
from __future__ import annotations

import itertools
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import cover  # noqa: E402


# ── greedy ──

def test_greedy_textbook_instance_and_result_shape():
    sets = {"A": {1, 2, 3, 4, 5, 6}, "B": {1, 2, 3, 7}, "C": {4, 5, 6, 8}, "D": {7, 8}}
    r = cover.greedy(range(1, 9), sets)
    assert r["chosen"] == ["A", "D"] and r["uncovered"] == [] and r["covered"] == 8 and r["cost"] == 2
    assert cover.uncovered(range(1, 9), r["chosen"], sets) == []
    json.dumps(r)


def test_greedy_ties_go_to_input_order_then_to_the_tie_key():
    sets = {"x": {1}, "y": {1}, "z": {1}}
    assert cover.greedy([1], ["y", "x", "z"], sets)["chosen"] == ["y"]
    assert cover.greedy([1], ["y", "x", "z"], sets, tie=lambda c, chosen: c)["chosen"] == ["x"]
    # sets of candidates are sorted first, so the result does not depend on hashing
    assert cover.greedy([1], {"z", "y", "x"}, sets)["chosen"] == ["x"]


def test_greedy_tie_key_sees_what_was_chosen():
    # 1-D positions covering their neighbours: prefer candidates next to an already chosen one
    covers = {p: {p - 1, p, p + 1} for p in range(0, 30)}
    seen = []

    def tie(c, chosen):
        seen.append(list(chosen))
        return (0 if any(abs(c - q) <= 3 for q in chosen) else 1, c)
    r = cover.greedy(range(0, 30), covers, tie=tie)
    assert cover.uncovered(range(0, 30), r["chosen"], covers) == []
    assert [] in seen and any(s for s in seen)


def test_greedy_weights_costs_and_max_count():
    sets = {"cheap": {1, 2}, "big": {1, 2, 3, 4}, "rare": {5}}
    assert cover.greedy([1, 2, 3, 4, 5], sets)["chosen"] == ["big", "rare"]
    # big costs 3: gain/cost 4/3 < cheap 2/1
    r = cover.greedy([1, 2, 3, 4, 5], sets, cost={"big": 3})
    assert r["chosen"][0] == "cheap" and r["uncovered"] == [] and r["cost"] == 5
    # a heavy target pulls its candidate first
    assert cover.greedy([1, 2, 3, 4, 5], sets, weight={5: 10})["chosen"][0] == "rare"
    # max coverage: one pick only
    r = cover.greedy([1, 2, 3, 4, 5], sets, max_count=1)
    assert r["chosen"] == ["big"] and r["uncovered"] == [5]
    # min_gain stops early
    assert cover.greedy([1, 2, 3, 4, 5], sets, min_gain=1)["chosen"] == ["big"]


def test_greedy_conflicts_exclude_overlapping_candidates():
    # positions 0..9, each covers itself and both neighbours; picking p forbids p-1 and p+1
    covers = {p: {p - 1, p, p + 1} for p in range(10)}
    r = cover.greedy(range(10), covers, conflicts=lambda p: [p - 1, p + 1])
    assert all(abs(a - b) > 1 for a, b in itertools.combinations(r["chosen"], 2))
    assert cover.uncovered(range(10), r["chosen"], covers) == []


def test_greedy_reports_uncoverable_targets_and_ignores_foreign_ones():
    sets = {"a": {1, 99}}
    r = cover.greedy([1, 2], sets)
    assert r["chosen"] == ["a"] and r["uncovered"] == [2] and r["covered"] == 1


def test_greedy_rejects_bad_costs_and_missing_covers():
    try:
        cover.greedy([1], {"a": {1}}, cost={"a": 0})
        raise AssertionError("cost 0 accepted")
    except ValueError:
        pass
    try:
        cover.greedy([1], ["a"])
        raise AssertionError("no covers accepted")
    except TypeError:
        pass


def test_greedy_is_within_the_ln_bound_of_the_optimum_on_random_instances():
    rng = random.Random(5)
    for _ in range(40):
        n = rng.randrange(4, 10)
        sets = {i: set(rng.sample(range(n), rng.randrange(1, n))) for i in range(rng.randrange(3, 8))}
        allt = set().union(*sets.values())
        r = cover.greedy(sorted(allt), sets)
        assert r["uncovered"] == [] and cover.uncovered(sorted(allt), r["chosen"], sets) == []
        best = next(k for k in range(1, len(sets) + 1)
                    if any(set().union(*(sets[c] for c in combo)) == allt
                           for combo in itertools.combinations(sets, k)))
        harmonic = sum(1 / i for i in range(1, max(len(s) for s in sets.values()) + 1))
        assert len(r["chosen"]) <= best * harmonic + 1e-9


# ── prune / uncovered ──

def test_prune_drops_redundant_latest_first():
    sets = {"a": {1, 2}, "b": {2, 3}, "c": {1, 3}}
    assert cover.prune(["a", "b", "c"], sets) == ["a", "b"]
    assert cover.prune(["c", "a", "b"], sets) == ["c", "a"]
    assert cover.prune(["a", "b"], sets) == ["a", "b"]
    assert cover.prune(["a", "b", "c"], sets, targets=[1]) == ["a"]


def test_uncovered_validator():
    sets = {"a": {1}, "b": {2}}
    assert cover.uncovered([1, 2, 3], ["a"], sets) == [2, 3]
    assert cover.uncovered([1, 2], ["a", "b"], lambda c: sets[c]) == []


# ── squares ──

def test_square_reach_and_edge_contact():
    assert cover.square_reach((0.5, 0.5), 3.5) == (-3.0, -3.0, 4.0, 4.0)
    # medium pole at 0.5 (supply 3.5): a lamp 3 tiles away is powered, 4 away touches the edge only
    assert cover.area_overlaps((3, 0, 4, 1), (0.5, 0.5), 3.5)
    assert not cover.area_overlaps((4, 0, 5, 1), (0.5, 0.5), 3.5)
    # entity dicts work, size and rotation from the dict
    assert cover.area_overlaps({"x": 4.5, "y": 0.5, "w": 3, "h": 3}, (0.5, 0.5), 3.5)
    assert not cover.area_overlaps({"x": 5.5, "y": 0.5, "w": 3, "h": 3}, (0.5, 0.5), 3.5)
    # beacon 3x3 at 0.5, supply_area_distance 3: square half-size 1.5 + 3
    # (square -4..5; a machine at 5.5 spans 4..7 and is reached, at 6.5 it spans 5..8: edge only)
    assert cover.area_overlaps({"x": 5.5, "y": 0.5, "w": 3, "h": 3}, (0.5, 0.5), 1.5 + 3)
    assert not cover.area_overlaps({"x": 6.5, "y": 0.5, "w": 3, "h": 3}, (0.5, 0.5), 1.5 + 3)


def test_box_forms():
    assert cover.box((0, 0, 2, 3)) == (0.0, 0.0, 2.0, 3.0)
    assert cover.box({"x": 1.0, "y": 1.5, "w": 2, "h": 3}) == (0.0, 0.0, 2.0, 3.0)
    assert cover.box({"position": {"x": 1.5, "y": 1.0}, "w": 2, "h": 3, "direction": 4}) == (0.0, 0.0, 3.0, 2.0)
    assert cover.box({"x0": 0, "y0": 1, "x1": 2, "y1": 3}) == (0.0, 1.0, 2.0, 3.0)


# ── candidates ──

def test_candidates_around_grid_offsets_and_reach():
    lamp = [(0, 0, 1, 1)]
    odd = cover.candidates_around(lamp, 2.5)
    assert all(x % 1 == 0.5 and y % 1 == 0.5 for x, y in odd)
    even = cover.candidates_around(lamp, 9, size=2)
    assert all(x == int(x) and y == int(y) for x, y in even)
    # every candidate reaches the box, and every grid position that reaches it is a candidate
    for cands, r, o in ((odd, 2.5, 0.5), (even, 9, 0.0)):
        for p in cands:
            assert cover.area_overlaps(lamp[0], p, r)
        brute = {(i + o, j + o) for i in range(-15, 16) for j in range(-15, 16)
                 if cover.area_overlaps(lamp[0], (i + o, j + o), r)}
        assert brute == set(cands)
    assert len(odd) == 5 * 5  # a 1x1 lamp, half-size 2.5: centers -1.5..2.5 per axis


def test_candidates_around_respects_the_grid_and_maps_boxes():
    lamps = [(0, 0, 1, 1), (4, 0, 5, 1)]
    g = kc.Grid(blocked=[(0.5, 0.5), (4.5, 0.5)])
    cands = cover.candidates_around(lamps, 2.5, g, 1)
    assert (0.5, 0.5) not in cands and (4.5, 0.5) not in cands
    assert cands[(2.5, 0.5)] == [0, 1]  # between them: reaches both
    assert cands[(-1.5, 0.5)] == [0]
    # a 2x2 thing must fit whole: tile (1.5, 0.5) blocked kills every 2x2 position covering it
    g2 = kc.Grid(blocked=[(1.5, 0.5)])
    c2 = cover.candidates_around([(0, 0, 1, 1)], 3, g2, 2)
    assert all(not g2.occupant(t) for p in c2 for t in kc.footprint(p, 2, 2))
    assert (1.0, 0.0) not in c2 and (2.0, 1.0) not in c2


def test_candidates_feed_greedy_into_a_valid_cover():
    rng = random.Random(9)
    boxes = []
    for _ in range(30):
        x, y = rng.randrange(0, 60), rng.randrange(0, 60)
        boxes.append((x, y, x + 3, y + 3))
    occupied = [t for b in boxes for t in kc.geo.tiles_in(b)]
    g = kc.Grid(blocked=occupied)
    cands = cover.candidates_around(boxes, 3.5, g, 1)
    r = cover.greedy(range(len(boxes)), cands)
    assert cover.uncovered(range(len(boxes)), r["chosen"], cands) == r["uncovered"]
    for p in r["chosen"]:
        assert g.can_place(p, 1, 1)
    for i in set(range(len(boxes))) - set(r["uncovered"]):
        assert any(cover.area_overlaps(boxes[i], p, 3.5) for p in r["chosen"])
    kept = cover.prune(r["chosen"], cands)
    assert len(kept) <= len(r["chosen"])
    assert set(cover.uncovered(range(len(boxes)), kept, cands)) == set(r["uncovered"])


def test_kc_exposes_the_module():
    assert kc.cover is cover
