"""Belt router (skills/belts/lib/belts.py): pure Python, no game."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "belts" / "lib"))
import belts  # noqa: E402

E, S, W, N = 4, 8, 12, 0


def wall(x, ys):
    return {(x + 0.5, y + 0.5) for y in ys}


def tiles(r):
    return [(e["x"], e["y"]) for e in r["entities"]]


def test_straight_line():
    r = belts.route((0.5, 0.5), (9.5, 0.5))
    assert r["ok"] and r["belts"] == 10 and r["undergrounds"] == 0 and r["turns"] == 0, r
    assert all(e["direction"] == E for e in r["entities"]), r["entities"]
    assert belts.check(r["entities"]) == []


def test_one_turn_l_shape_and_goal_direction():
    r = belts.route((0.5, 0.5), (5.5, 5.5), goal_dir=S)
    assert r["ok"] and r["turns"] == 1 and r["belts"] == 11, r
    assert r["entities"][-1]["direction"] == S
    assert belts.check(r["entities"]) == []


def test_jumps_a_thin_wall_with_an_underground():
    blocked = wall(5, range(-20, 21))  # one-tile wall, far too long to go around
    r = belts.route((0.5, 0.5), (10.5, 0.5), blocked)
    assert r["ok"] and r["undergrounds"] == 1, r
    inp, out = [e for e in r["entities"] if e.get("type")]
    assert inp["type"] == "input" and out["type"] == "output" and inp["direction"] == out["direction"] == E
    assert inp["x"] < 5 < out["x"], (inp, out)
    assert belts.check(r["entities"], blocked) == []


def test_wall_thicker_than_underground_reach_is_walked_around():
    blocked = set()
    for x in range(3, 9):  # 6 tiles thick: basic undergrounds (reach 5) cannot cross it
        blocked |= wall(x, range(-3, 4))
    r = belts.route((0.5, 0.5), (11.5, 0.5), blocked, max_underground=5)
    assert r["ok"] and r["undergrounds"] == 0 and r["turns"] >= 2, r
    assert all(abs(y - 0.5) > 3 for x, y in tiles(r) if 3 <= x < 9), tiles(r)
    assert belts.check(r["entities"], blocked) == []
    # a longer reach (red: 7) jumps it instead
    r7 = belts.route((0.5, 0.5), (11.5, 0.5), blocked, max_underground=7, underground="fast-underground-belt")
    assert r7["ok"] and r7["undergrounds"] == 1 and r7["entities"][1]["name"] in ("transport-belt", "fast-underground-belt")
    assert belts.check(r7["entities"], blocked, max_underground=7) == []


def test_prefers_a_short_detour_over_an_underground():
    blocked = wall(5, range(0, 1))  # one tile: one row over costs 12 tiles + 2 turns = 13; under = 10 + cost
    r = belts.route((0.5, 0.5), (10.5, 0.5), blocked, underground_cost=5)
    assert r["ok"] and r["undergrounds"] == 0, r
    r2 = belts.route((0.5, 0.5), (10.5, 0.5), blocked, underground_cost=0.5)
    assert r2["ok"] and r2["undergrounds"] == 1, r2


def test_open_ground_never_uses_undergrounds():
    """Regression: an underground was charged one tile too few, so chains of them beat plain belt."""
    for uc in (0.0, 0.5, 1.5):
        r = belts.route((0.5, 0.5), (30.5, 0.5), underground_cost=uc)
        assert r["ok"] and (r["undergrounds"] == 0 or uc == 0.0), (uc, r["undergrounds"])
        assert r["cost"] == 30.0, r["cost"]


def test_no_undergrounds_when_the_tier_has_none():
    blocked = wall(5, range(-40, 41))
    r = belts.route((0.5, 0.5), (10.5, 0.5), blocked, max_underground=0, margin=8)
    assert not r["ok"] and "walled off" in r["reason"], r


def test_does_not_span_a_same_axis_underground():
    blocked = wall(5, range(-20, 21))
    foreign = [{"x": 6.5, "y": 0.5, "direction": W}]  # an existing E-W underground right after the wall
    blocked |= {(6.5, 0.5)}
    r = belts.route((0.5, 0.5), (10.5, 0.5), blocked, undergrounds=foreign)
    assert r["ok"], r
    for i, e in enumerate(r["entities"]):
        if e.get("type") == "input" and e["direction"] in (E, W) and abs(e["y"] - 0.5) < 0.1:
            out = r["entities"][i + 1]
            assert not (e["x"] < 6.5 < out["x"]), (e, out)
    assert belts.check(r["entities"], blocked, foreign) == []
    # a north-south underground on the same tile does not get in the way
    r2 = belts.route((0.5, 0.5), (10.5, 0.5), wall(5, range(-20, 21)) | {(7.5, 0.5)},
                     undergrounds=[{"x": 7.5, "y": 0.5, "direction": N}])
    assert r2["ok"] and r2["undergrounds"] == 1 and r2["turns"] == 0, r2


def test_blocked_start_goal_and_budget():
    assert belts.route((0.5, 0.5), (3.5, 0.5), {(0.5, 0.5)})["reason"].startswith("start tile")
    assert belts.route((0.5, 0.5), (3.5, 0.5), {(3.5, 0.5)})["reason"].startswith("goal tile")
    r = belts.route((0.5, 0.5), (200.5, 150.5), budget=50)
    assert not r["ok"] and "budget" in r["reason"]


def test_start_direction_and_any_point_form():
    r = belts.route({"x": 0.2, "y": 0.9}, {"position": {"x": 0, "y": 6}}, start_dir="east")
    assert r["ok"] and r["entities"][0]["direction"] == E and tiles(r)[-1] == (0.5, 6.5), r
    assert belts.check(r["entities"]) == []


def test_check_catches_broken_lines():
    good = belts.route((0.5, 0.5), (4.5, 0.5))["entities"]
    gap = good[:2] + good[3:]
    assert any("next entity" in p for p in belts.check(gap))
    head_on = [dict(good[0]), dict(good[1], direction=W)]
    assert any("face each other" in p for p in belts.check(head_on))
    dup = good + [dict(good[0])]
    assert any("already used" in p for p in belts.check(dup))


def test_maze_route_is_valid():
    """A walled maze with gaps: every route the router returns must pass check()."""
    blocked = set()
    for x in (4, 8, 12, 16):
        gap = (x * 7) % 9 - 4
        blocked |= {(x + 0.5, y + 0.5) for y in range(-6, 7) if y != gap}
    for start, goal in [((0.5, 0.5), (20.5, 0.5)), ((0.5, -5.5), (20.5, 5.5)), ((20.5, 3.5), (0.5, -3.5))]:
        for maxu in (0, 4, 5):
            r = belts.route(start, goal, blocked, max_underground=maxu, margin=4)
            assert r["ok"], (start, goal, maxu, r)
            assert belts.check(r["entities"], blocked, max_underground=max(maxu, 5)) == [], (start, goal, maxu)
