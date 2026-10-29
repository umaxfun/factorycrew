"""Beacon placer (skills/beacons/lib/beacons.py): pure Python, no game."""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "beacons" / "lib"))
import beacons  # noqa: E402

PROFILE = [1 / n ** 0.5 for n in range(1, 101)]  # vanilla 2.0 beacon profile


def row(n, pitch=3, y=0.5):
    return [(pitch * i + 0.5, y) for i in range(n)]


def counts(r):
    return [m["beacons"] for m in r["machines"]]


def template_8():
    """Classic 8-beacon row: assemblers touching, a beacon row right above and below, shifted 1 tile."""
    return [(3 * k + 1.5, y) for k in range(-2, 9) for y in (-2.5, 3.5)]


def template_12():
    """Classic 12-beacon row: assemblers 6 apart, two beacons between neighbours, rows at -4 / +5."""
    b = [(6 * i + 3.5, y) for i in range(-1, 5) for y in (-0.5, 2.5)]
    return b + [(3 * k + 1.5, y) for k in range(-3, 11) for y in (-3.5, 5.5)]


def test_reach_is_exact_at_the_edge():
    m = [(0.5, 0.5)]  # 3x3 machine, collision +-1.2; beacon 3x3, supply 3: area +-4.5
    assert counts({"machines": beacons.evaluate([(5.5, 0.5)], m)["machines"]}) == [1]
    assert counts({"machines": beacons.evaluate([(6.5, 0.5)], m)["machines"]}) == [0]
    assert counts({"machines": beacons.evaluate([(5.5, -4.5)], m)["machines"]}) == [1]
    # a 5x5 refinery (collision +-2.4) is reached one tile further
    big = [{"x": 0.5, "y": 0.5, "w": 5, "h": 5}]
    assert beacons.evaluate([(6.5, 0.5)], big)["machines"][0]["beacons"] == 1
    assert beacons.evaluate([(7.5, 0.5)], big)["machines"][0]["beacons"] == 0
    # a bigger supply distance (quality beacon) reaches further
    assert beacons.evaluate([(6.5, 0.5)], m, supply_area_distance=4)["machines"][0]["beacons"] == 1


def test_profile_effect():
    assert beacons.effect_of(0, PROFILE) == 0
    assert abs(beacons.effect_of(4, PROFILE) - 2.0) < 1e-12  # 4 * 1/sqrt(4)
    assert beacons.effect_of(5, [1.0, 0.5]) == 2.5  # past the end: last entry
    assert beacons.effect_of(3) == 3  # no profile: no diminishing
    t = beacons.transmitted([{"speed": 0.2, "consumption": 0.5}] * 2, 1.5)
    assert abs(t["speed"] - 0.6) < 1e-12 and abs(t["consumption"] - 1.5) < 1e-12


def test_reference_rows_count_8_and_12():
    r8 = beacons.evaluate(template_8(), row(8))
    assert counts(r8) == [8] * 8 and beacons.check(template_8(), row(8)) == []
    r12 = beacons.evaluate(template_12(), row(5, pitch=6))
    assert counts(r12) == [12] * 5 and beacons.check(template_12(), row(5, pitch=6)) == []


def test_greedy_matches_the_reference_rows():
    # no diminishing: every machine of a touching row gets the 8-beacon count (ends get more)
    r = beacons.place(row(8))
    assert all(c >= 8 for c in counts(r)) and counts(r)[1:-1] == [8] * 6, counts(r)
    assert beacons.check(r, row(8)) == []
    # assemblers 6 apart: the greedy finds the 12-beacon layout, with as many beacons
    ms = row(5, pitch=6)
    r = beacons.place(ms)
    ref = beacons.evaluate(template_12(), ms)
    assert counts(r) == [12] * 5 and r["total_effect"] == ref["total_effect"], counts(r)
    assert r["beacons"] == 36 and beacons.check(r, ms) == []


def test_diminishing_profile_and_threshold():
    ms = row(8)
    r = beacons.place(ms, profile=PROFILE, min_utility=0.5)
    assert counts(r)[2:-2] == [8, 8, 8, 8] and r["beacons"] == 14, counts(r)
    many = beacons.place(ms, profile=PROFILE, min_utility=0.01)
    few = beacons.place(ms, profile=PROFILE, min_utility=2.0)
    assert few["beacons"] < r["beacons"] < many["beacons"]
    assert few["total_effect"] < r["total_effect"] < many["total_effect"]
    for x in (r, many, few):
        assert beacons.check(x, ms, profile=PROFILE) == []
    # effects follow the profile: 8 beacons of 0.6 speed each at 1/sqrt(8)
    r = beacons.place(ms, profile=PROFILE, beacon_effect={"speed": 0.6})
    m = r["machines"][3]
    assert m["beacons"] == 8 and abs(m["effects"]["speed"] - 0.6 * 8 / 8 ** 0.5) < 1e-6, m


def test_min_per_machine_and_max_beacons():
    ms = row(8)
    r = beacons.place(ms, profile=PROFILE, min_utility=99, min_per_machine=8)
    assert all(c >= 8 for c in counts(r)) and r["short"] == [], counts(r)
    assert beacons.check(r, ms, profile=PROFILE, min_per_machine=8) == []
    # 13 cannot be reached in a touching row: everyone is short, check accepts the report
    r = beacons.place(ms, min_per_machine=13, min_utility=99)
    assert r["short"] == list(range(8)) and beacons.check(r, ms, min_per_machine=13) == []
    r = beacons.place(ms, max_beacons=5)
    assert r["beacons"] == 5


def test_blocked_lanes_and_existing_beacons():
    ms = row(8)
    lanes = {(x + 0.5, y) for x in range(-10, 40) for y in (-1.5, -2.5, 2.5, 3.5)}  # inserters + belts
    r = beacons.place(ms, lanes, profile=PROFILE)
    assert r["beacons"] > 0 and beacons.check(r, ms, lanes, profile=PROFILE) == []
    tiles = set()
    for e in r["entities"]:
        tiles |= beacons.footprint(e["x"], e["y"], 3, 3)
    assert not tiles & lanes
    # beacons already standing count and block
    old = [(1.5, -2.5)]
    r2 = beacons.place(ms, existing=old, max_beacons=0)
    assert counts(r2)[:3] == [1, 1, 1] and r2["beacons"] == 0
    r3 = beacons.place(ms, existing=old)
    assert beacons.check(r3, ms, existing=old) == []
    assert all(max(abs(e["x"] - 1.5), abs(e["y"] + 2.5)) >= 3 for e in r3["entities"])


def test_pumpjack_field():
    rnd = random.Random(7)
    pj = []
    while len(pj) < 40:
        p = (rnd.randrange(0, 60) + 0.5, rnd.randrange(0, 60) + 0.5)
        if all(max(abs(p[0] - q[0]), abs(p[1] - q[1])) >= 5 for q in pj):
            pj.append(p)
    pipes = {(p[0] + 2, p[1] - 0.0) for p in pj}  # one pipe tile next to each pumpjack
    r = beacons.place(pj, pipes, profile=PROFILE)
    assert beacons.check(r, pj, pipes, profile=PROFILE) == []
    assert min(counts(r)) >= 1 and r["beacons"] > 20, counts(r)
    r2 = beacons.place(pj, pipes, profile=PROFILE, min_per_machine=3)
    assert all(c >= 3 for i, c in enumerate(counts(r2)) if i not in r2["short"])
    assert beacons.check(r2, pj, pipes, profile=PROFILE, min_per_machine=3) == []
    # deterministic
    assert beacons.place(pj, pipes, profile=PROFILE) == r
    assert beacons.place(list(reversed(pj)), sorted(pipes), profile=PROFILE)["entities"] == r["entities"]


def test_even_sized_beacon_and_point_forms():
    ms = [{"position": {"x": 0.5, "y": 0.5}, "tile_width": 3, "tile_height": 3,
           "collision_box": {"left_top": {"x": -1.2, "y": -1.2}, "right_bottom": {"x": 1.2, "y": 1.2}}}]
    r = beacons.place(ms, size=2, supply_area_distance=2, beacon="mod-beacon")
    assert r["beacons"] > 0 and r["entities"][0]["name"] == "mod-beacon"
    assert all(e["x"] == int(e["x"]) and e["y"] == int(e["y"]) for e in r["entities"]), r["entities"]
    assert beacons.check(r, ms, size=2, supply_area_distance=2) == []


def test_check_catches_bad_layouts():
    ms = row(3)
    r = beacons.place(ms)
    bad = dict(r, entities=r["entities"] + [dict(r["entities"][0])])
    assert any("overlaps beacon" in p for p in beacons.check(bad["entities"], ms))
    assert any("overlaps machine" in p for p in beacons.check([(0.5, 2.5)], ms))
    assert any("off the 3x3 grid" in p for p in beacons.check([(1.0, -5.5)], ms))
    assert any("blocked" in p for p in beacons.check([(1.5, -2.5)], ms, {(1.5, -2.5)}))
    lie = dict(r, machines=[dict(m, beacons=m["beacons"] + 1) for m in r["machines"]])
    assert any("geometry gives" in p for p in beacons.check(lie, ms))
    assert any("not listed as short" in p for p in beacons.check(dict(r, short=[]), ms, min_per_machine=50))
