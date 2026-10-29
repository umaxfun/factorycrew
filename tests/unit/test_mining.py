"""Mining outpost planner (skills/mining/lib/mining.py): pure Python, no game."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "mining" / "lib"))
import mining  # noqa: E402

N, E, S, W = 0, 4, 8, 12
BURNER = {"name": "burner-mining-drill", "size": 2, "radius": 0.99, "output": (-0.5, -1.3), "mining_speed": 0.25}
BIG = {"name": "big-mining-drill", "size": 5, "radius": 6.49, "output": (0, -2.85), "mining_speed": 2.5}


def rect(x0, y0, w, h):
    return [(x + 0.5, y + 0.5) for x in range(x0, x0 + w) for y in range(y0, y0 + h)]


def disc(cx, cy, r):
    return [(x + 0.5, y + 0.5) for x in range(cx - r, cx + r + 1) for y in range(cy - r, cy + r + 1)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r]


def blob():
    return [(x + 0.5, y + 0.5) for x in range(-20, 21) for y in range(-14, 15)
            if (x / 20) ** 2 + (y / 14) ** 2 + 0.15 * math.sin(x / 3) * math.cos(y / 4) <= 1]


def tiles_of(r, drill=mining.DRILL):
    s = drill["size"]
    out = set()
    for d in r["drills"]:
        out |= {(math.floor(d["x"] - s / 2) + i, math.floor(d["y"] - s / 2) + j) for i in range(s) for j in range(s)}
    return out


def valid(r, ore, blocked=(), drill=None):
    assert r["ok"], r
    assert mining.check(r["entities"], ore, blocked, drill=drill, exit=r["exit"]) == []
    assert mining.check(r["entities"], ore, blocked, drill=drill) == []  # the exit is also found alone
    return r["stats"]


def test_rectangle_two_rows_facing_one_belt():
    ore = rect(0, 0, 30, 6)
    r = mining.plan(ore, orientation="horizontal")
    st = valid(r, ore)
    assert st["coverage"] == 100.0 and st["lanes"] == 1, st
    dirs = {d["direction"] for d in r["drills"]}
    assert dirs == {N, S}, dirs  # the classic pair: one row faces south, the other north
    ys = sorted({d["y"] for d in r["drills"]})
    belt_y = {b["y"] for b in r["belts"] if b["direction"] == E}
    assert len(ys) == 2 and belt_y == {(ys[0] + ys[1]) / 2}, (ys, belt_y)  # shared belt between the rows
    assert len(r["entities"]) == st["drills"] + st["belts"]


def test_patch_shapes_full_coverage_and_valid():
    for name, ore in [("disc8", disc(0, 0, 8)), ("disc20", disc(50, -30, 20)), ("blob", blob()),
                      ("rect", rect(0, 0, 30, 12)), ("strip", rect(0, 0, 40, 3)), ("tall", rect(0, 0, 6, 25))]:
        for mode in ("simple", "full"):
            st = valid(mining.plan(ore, mode=mode), ore)
            assert st["coverage"] == 100.0, (name, mode, st)
            assert st["drills"] * 25 >= st["ore_tiles"], (name, st)


def test_simple_uses_fewer_drills_than_full():
    ore = disc(0, 0, 8)
    simple, full = mining.plan(ore)["stats"], mining.plan(ore, mode="full")["stats"]
    assert simple["drills"] <= full["drills"], (simple, full)


def test_long_strip_picks_the_orientation_with_one_belt():
    tall = rect(0, 0, 6, 25)
    r = mining.plan(tall)
    assert r["stats"]["orientation"] == "vertical" and r["stats"]["lanes"] == 1, r["stats"]
    wide = [(y, x) for x, y in tall]
    assert mining.plan(wide)["stats"]["orientation"] == "horizontal"
    assert mining.plan(tall, orientation="horizontal")["stats"]["orientation"] == "horizontal"


def test_exit_is_at_the_corner_toward_the_target():
    ore = rect(0, 0, 20, 14)
    for toward, sx, sy in [((100, 100), 1, 1), ((-100, 100), -1, 1), ((100, -100), 1, -1), ((-100, -100), -1, -1)]:
        r = mining.plan(ore, toward=toward)
        ex = r["exit"]
        valid(r, ore)
        assert (ex["x"] - 10) * sx > 0 and (ex["y"] - 7) * sy > 0, (toward, ex)
        for d in r["drills"]:  # the exit belt is past every drill on its side
            assert (ex["x"] - d["x"]) * sx > 0 or (ex["y"] - d["y"]) * sy > 0, (toward, ex, d)


def test_blocked_tiles_are_left_free():
    ore = disc(0, 0, 10)
    blocked = [(0.5, 0.5), (1.5, 0.5), (-3.5, 4.5), (5.5, -6.5), (-8.5, -1.5)]
    r = mining.plan(ore, blocked)
    st = valid(r, ore, blocked)
    used = tiles_of(r) | {(math.floor(b["x"]), math.floor(b["y"])) for b in r["belts"]}
    assert not used & {(math.floor(x), math.floor(y)) for x, y in blocked}
    assert st["coverage"] > 95, st


def test_blocked_lane_drops_only_drills_cut_off_from_the_exit():
    ore = rect(0, 0, 30, 6)
    free = mining.plan(ore, orientation="horizontal")
    lane_y = free["belts"][0]["y"]
    wall = [(10.5, lane_y)]
    r = mining.plan(ore, wall, orientation="horizontal")
    valid(r, ore, wall)
    assert all(b["x"] > 10.5 for b in r["belts"] if b["y"] == lane_y), r["belts"]


def test_avoid_other_ore():
    iron = rect(0, 0, 12, 8)
    copper = rect(13, 0, 3, 8)
    r = mining.plan(iron, avoid=copper)
    valid(r, iron)
    for d in r["drills"]:
        assert all(x < 13 for x, _ in mining._area_tiles(d["x"], d["y"], 2.49)), d
    assert r["stats"]["coverage"] == 100.0


def test_other_drill_sizes():
    ore = rect(0, 0, 16, 10)
    r = mining.plan(ore, drill=BURNER)
    valid(r, ore, drill=BURNER)
    assert all(d["x"] == int(d["x"]) and d["y"] == int(d["y"]) for d in r["drills"])  # even size: integer centers
    big = disc(0, 0, 15)
    r = mining.plan(big, drill=BIG)
    st = valid(r, big, drill=BIG)
    assert st["coverage"] == 100.0 and r["drills"][0]["name"] == "big-mining-drill"
    assert all(d["x"] % 1 == 0.5 for d in r["drills"])


def test_output_per_direction_and_direction_words():
    ore = rect(0, 0, 20, 6)
    per_dir = {"north": (0, -1.85), "east": (1.85, 0), "south": (0, 1.85), "west": (-1.85, 0)}
    a = mining.plan(ore, drill={"output": per_dir})
    b = mining.plan(ore)
    assert a["entities"] == b["entities"]
    ents = [dict(e, direction=mining.NAMES[e["direction"]]) for e in b["entities"]]
    assert mining.check(ents, ore) == []


def test_deterministic_and_input_order_free():
    ore = blob()
    a = mining.plan(ore, toward=(-40, 3))
    shuffled = list(ore)
    random.Random(7).shuffle(shuffled)
    b = mining.plan([{"x": x, "y": y, "amount": 1000} for x, y in shuffled], toward={"position": {"x": -40, "y": 3}})
    assert a["entities"] == b["entities"]
    assert b["stats"]["ore_amount"] == 1000 * b["stats"]["covered_tiles"]


def test_pole_rows_are_free_and_next_to_every_drill_row():
    ore = disc(0, 0, 12)
    r = mining.plan(ore, orientation="horizontal")
    occ = {tuple(t) for t in r["occupied"]}
    rows = r["pole_rows"]
    pts = {tuple(p) for row in rows for p in row}
    assert pts and not pts & occ
    pole_ys = {p[1] for p in pts}
    for d in r["drills"]:
        assert d["y"] - 2 in pole_ys or d["y"] + 2 in pole_ys, d  # the row just outside the drill
    assert len(r["drill_boxes"]) == len(r["drills"])
    assert tuple(r["out"]) not in occ  # the exit feeds a free tile, where belts.route can start


def test_exit_continues_with_the_belt_router():
    sys.path.insert(0, str(ROOT / "skills" / "belts" / "lib"))
    import belts
    ore = disc(0, 0, 8)
    r = mining.plan(ore, toward=(40, 0))
    occ = [tuple(t) for t in r["occupied"]]
    home = belts.route(r["out"], (40.5, 0.5), occ)
    assert home["ok"], home
    assert mining.check(r["entities"] + home["entities"], ore, exit=home["entities"][-1]) == []


def test_belt_capacity_warning_and_rate():
    r = mining.plan(disc(0, 0, 20))
    assert r["stats"]["rate"] == r["stats"]["drills"] * 0.5 and "warning" in r
    assert "warning" not in mining.plan(rect(0, 0, 10, 6))


def test_failures():
    assert not mining.plan([])["ok"]
    ore = rect(0, 0, 5, 5)
    blocked = [(x + 0.5, y + 0.5) for x in range(-5, 11) for y in range(-5, 11)]
    r = mining.plan(ore, blocked)
    assert not r["ok"] and "reason" in r, r


def test_check_catches_broken_layouts():
    ore = rect(0, 0, 20, 6)
    r = mining.plan(ore, orientation="horizontal")
    ents = r["entities"]
    nd = len(r["drills"])
    assert any("overlaps" in p for p in mining.check(ents + [dict(ents[0], x=ents[0]["x"] + 1)], ore))
    far = dict(ents[0], x=ents[0]["x"] + 100)
    assert any("no ore" in p for p in mining.check(ents + [far], ore))
    assert any("not a belt" in p for p in mining.check(ents + [far], ore))
    lane = [i for i, e in enumerate(ents) if i >= nd and e["direction"] == E]
    gap = ents[:lane[3]] + ents[lane[3] + 1:]
    probs = mining.check(gap, ore)
    assert any("feeds nothing" in p for p in probs) or any("not a belt" in p for p in probs), probs
    head = list(ents)
    head[lane[2]] = dict(ents[lane[2]], direction=W)
    assert any("head-on" in p for p in mining.check(head, ore))
    assert any("blocked" in p for p in mining.check(ents, ore, [(ents[0]["x"], ents[0]["y"])]))
    turned = list(ents)
    turned[0] = dict(ents[0], direction=E)
    assert any("not a belt" in p for p in mining.check(turned, ore))
