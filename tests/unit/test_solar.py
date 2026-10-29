"""Solar planner (skills/solar/lib/solar.py): pure Python, no game."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "solar" / "lib"))
import solar  # noqa: E402

DAY_1X = dict(solar.NAUVIS, ticks_per_day=25000)  # the classic 0.84 / 42 kW numbers
MEDIUM = {"name": "medium-electric-pole", "width": 1, "height": 1, "supply": 3.5, "reach": 9}


def simulate(peak_kw, day, load_kw, steps=20000):
    """Brute force, independent of the closed form: walk one day in small steps and return the
    accumulator energy (kJ) one panel needs to hold `load_kw` (max running deficit)."""
    c = solar.cycle(day)
    dt = c["seconds"] / steps
    store, low, high = 0.0, 0.0, 0.0
    # start at noon and run two days so the window crossing midnight is seen whole
    for i in range(2 * steps):
        b = solar.brightness((i + 0.5) / steps, day["dusk"], day["evening"], day["morning"], day["dawn"])
        store += (peak_kw * c["multiplier"] * b - load_kw) * dt
        high = max(high, store)
        low = min(low, store - high)
    return -low


def test_nauvis_reference_numbers():
    r = solar.ratio(60, 5, DAY_1X, accumulator_input_kw=300, accumulator_output_kw=300, target_mw=1)
    assert r["average_kw_per_panel"] == 42.0 and r["average_fraction"] == 0.7
    assert r["accumulators_per_panel"] == 0.84 and r["limited_by"] == "energy", r
    assert r["deficit_mj_per_panel"] == 4.2
    assert (r["panels"], r["accumulators"]) == (24, 21), r  # 1000 / 42 = 23.8 -> 24; 24 * 0.84 = 20.2 -> 21


def test_formula_matches_a_brute_force_day():
    for day in (DAY_1X, dict(DAY_1X, dusk=0.2, evening=0.3, morning=0.6, dawn=0.9)):
        r = solar.ratio(60, 5, day)
        need = simulate(60, day, r["average_kw_per_panel"])
        assert abs(need / 5000 - r["accumulators_per_panel"]) < 1e-3, (day, need, r)


def test_2_0_nauvis_and_space_age_planets_match_the_wiki():
    """wiki.factorio.com/Solar_panel 'Optimal ratios' (normal quality): 2.0 days are 7 min on Nauvis."""
    cases = [  # ticks_per_day, solar-power %, accumulators per panel
        (25200, 100, 0.84672),  # Nauvis
        (5400, 400, 0.72576),   # Vulcanus 1.5 min
        (36000, 50, 0.6048),    # Gleba 10 min
        (10800, 20, 0.072576),  # Fulgora 3 min
        (72000, 1, 0.024192),   # Aquilo 20 min
    ]
    for ticks, sp, want in cases:
        r = solar.ratio(60, 5, dict(solar.NAUVIS, ticks_per_day=ticks, solar_power=sp))
        assert abs(r["accumulators_per_panel"] - want) < 1e-6, (ticks, sp, r)
        assert abs(r["average_kw_per_panel"] - 42 * sp / 100) < 1e-9


def test_day_length_and_multiplier():
    long_day = solar.ratio(60, 5, dict(DAY_1X, ticks_per_day=50000))
    assert long_day["accumulators_per_panel"] == 1.68 and long_day["average_kw_per_panel"] == 42.0
    doubled = solar.ratio(60, 5, dict(DAY_1X, solar_power_multiplier=2))
    assert doubled["average_kw_per_panel"] == 84.0 and doubled["accumulators_per_panel"] == 1.68
    assert solar.ratio(60, 5, DAY_1X, solar_multiplier=0.5)["accumulators_per_panel"] == 0.42


def test_flow_limits_bind_on_a_short_day():
    # Vulcanus: 240 kW peak, 168 kW average; a 100 kW accumulator cannot discharge that
    r = solar.ratio(60, 5, dict(solar.NAUVIS, ticks_per_day=5400, solar_power=400),
                    accumulator_input_kw=100, accumulator_output_kw=100)
    assert r["limited_by"] == "discharge" and r["accumulators_per_panel"] == 1.68, r


def test_no_night():
    r = solar.ratio(60, 5, {"always_day": True}, target_mw=6)
    assert r["accumulators_per_panel"] == 0 and r["average_kw_per_panel"] == 60 and r["limited_by"] == "none"
    assert (r["panels"], r["accumulators"]) == (100, 0)
    frozen = solar.ratio(60, 5, dict(DAY_1X, freeze_daytime=True, daytime=0.35))
    assert frozen["average_kw_per_panel"] == 30.0 and frozen["accumulators_per_panel"] == 0


def test_bad_phases_are_refused():
    with pytest.raises(ValueError):
        solar.ratio(60, 5, dict(DAY_1X, evening=0.2))


def test_sustained_power():
    assert solar.sustained_kw(25, 21, 60, 5, DAY_1X) == 25 * 42.0  # 21 >= 25 * 0.84
    short = solar.sustained_kw(24, 10, 60, 5, DAY_1X)
    assert 0 < short < 24 * 42
    # the brute-force day agrees that 10 accumulators hold exactly that load for 24 panels
    assert abs(simulate(60, DAY_1X, short / 24) * 24 / 5000 - 10) < 0.02
    assert solar.sustained_kw(10, 0, 60, 5, DAY_1X) == 0.0


def grid(c):
    """Cell as text rows: P panel, a accumulator, S pole, . empty."""
    g = [["."] * c["width"] for _ in range(c["height"])]
    for e in c["entities"]:
        w, h = c["sizes"][e["name"]]
        ch = {"pole": "S", "panel": "P", "accumulator": "a"}[next(k for k, v in c["names"].items() if v == e["name"])]
        for i in range(w):
            for j in range(h):
                y, x = int(e["y"] - h / 2) + j, int(e["x"] - w / 2) + i
                assert g[y][x] == ".", (e, "overlap")
                g[y][x] = ch
    return ["".join(r) for r in g]


def test_substation_cell():
    r = solar.ratio(60, 5, DAY_1X)
    c = solar.cell(ratio=r)
    # 18x18 = the substation's supply area and wire reach; 24 panels + 20 accumulators, the
    # common substation block (the wiki's 6:5 ratio, 0.833 vs 0.84 needed)
    assert c["ok"] and (c["width"], c["height"]) == (18, 18) and c["pole"] == [9.0, 9.0]
    assert (c["panels"], c["accumulators"]) == (24, 20), c
    assert c["sustained_kw"] > 0.99 * 24 * 42
    assert solar.check(c["entities"], ratio=r) == []
    assert sum(row.count("S") for row in grid(c)) == 4


def test_cell_coverage_every_item_inside_the_supply_area():
    for pole in (solar.POLE, MEDIUM, dict(solar.POLE, supply=7, reach=20)):
        c = solar.cell(pole=pole)
        assert c["ok"] and c["panels"] > 0, (pole, c)
        d = pole["supply"]
        px, py = c["pole"]
        for e in c["entities"][1:]:
            w, h = c["sizes"][e["name"]]
            assert px - d <= e["x"] - w / 2 and e["x"] + w / 2 <= px + d, (pole, e)
            assert py - d <= e["y"] - h / 2 and e["y"] + h / 2 <= py + d, (pole, e)
            assert 0 <= e["x"] - w / 2 and e["x"] + w / 2 <= c["width"] and e["y"] + h / 2 <= c["height"]
        assert c["width"] <= pole["reach"], pole  # neighbouring cells' poles link
        assert solar.check(c["entities"], pole=pole) == []
    assert solar.cell(pole=MEDIUM)["width"] == 7
    assert solar.cell(pole=dict(solar.POLE, supply=7, reach=20))["width"] == 14


def test_modded_sizes():
    """Nothing vanilla is assumed: a 2x2 panel, a 3x3 accumulator, a 1x1 pole."""
    pn = {"name": "x-panel", "width": 2, "height": 2, "peak_kw": 20}
    ac = {"name": "x-battery", "width": 3, "height": 3, "capacity_mj": 10}
    po = {"name": "x-pole", "width": 1, "height": 1, "supply": 5.5, "reach": 11}
    r = solar.ratio(20, 10, DAY_1X)
    c = solar.cell(pn, ac, po, ratio=r)
    assert c["ok"] and c["width"] == 11 and c["panels"] > 0 and c["accumulators"] >= 0.98 * r["accumulators_per_panel"] * c["panels"]
    assert solar.check(c["entities"], pn, ac, po, ratio=r) == []
    grid(c)
    f = solar.field(c, area=[[0, 0], [33, 22]])
    assert f["ok"] and f["poles"] == 6 and solar.check(f["entities"], pn, ac, po, ratio=r) == []


def test_field_over_an_area_skips_blocked_cells():
    c = solar.cell()
    f = solar.field(c, area=[[0, 0], [54, 36]], blocked=[(25.5, 27.5)])
    assert f["ok"] and f["poles"] == 5 and f["skipped"] == 1, f
    assert [27.0, 27.0] not in [[e["x"], e["y"]] for e in f["entities"]]
    assert (f["panels"], f["accumulators"]) == (120, 100)
    assert solar.check(f["entities"], blocked=[(25.5, 27.5)], ratio=0.84) == []


def test_field_keeps_only_cells_linked_to_the_start():
    c = solar.cell()
    wall = [(18 + 0.5 + i, y + 0.5) for y in range(36) for i in range(1)]  # blocks the middle column
    f = solar.field(c, area=[[0, 0], [54, 36]], blocked=wall)
    assert f["ok"] and f["poles"] == 2 and f["unreachable"] == 2 and f["skipped"] == 2, f
    assert solar.check(f["entities"]) == []
    # placed regardless, the far side would be a second network: check() says so
    far = solar.field(c, area=[[36, 0], [54, 36]])["entities"]
    assert any("not wire-linked" in p for p in solar.check(f["entities"] + far))


def test_field_for_a_target_near_a_point():
    r = solar.ratio(60, 5, DAY_1X)
    c = solar.cell(ratio=r)
    f = solar.field(c, (100.3, -40.7), target_mw=5, blocked=[(100.5, -40.5)])
    assert f["ok"] and f["poles"] == math.ceil(5000 / c["sustained_kw"]) and f["sustained_kw"] >= 5000, f
    assert solar.check(f["entities"], blocked=[(100.5, -40.5)], ratio=r) == []
    # cells grow around the point: none is far
    assert max(math.hypot(x + 9 - 100.3, y + 9 + 40.7) for x, y in f["cells"]) < 3 * 18
    assert solar.field(c, (0, 0), target_mw=5)["entities"] == solar.field(c, (0, 0), target_mw=5)["entities"]
    nope = solar.field(c, (0, 0), target_mw=500, search=1)
    assert not nope["ok"] and "only 9 of" in nope["reason"]


def test_partial_cells_drop_only_what_hits_a_tree():
    c = solar.cell()
    tree = [(1.5, 3.5)]  # inside the top-left panel, away from the pole
    f = solar.field(c, area=[[0, 0], [18, 18]], blocked=tree, partial=True)
    assert f["ok"] and f["poles"] == 1 and f["panels"] == c["panels"] - 1, f
    assert solar.check(f["entities"], blocked=tree) == []
    assert not solar.field(c, area=[[0, 0], [18, 18]], blocked=tree)["ok"]
    assert not solar.field(c, area=[[0, 0], [18, 18]], blocked=[(9.5, 9.5)], partial=True)["ok"]  # the pole tile


def test_check_catches_broken_fields():
    c = solar.cell()
    ents = c["entities"]
    assert solar.check(ents) == []
    moved = [dict(e) for e in ents]
    moved[-1]["x"] += 30
    assert any("outside every pole" in p for p in solar.check(moved))
    dup = ents + [dict(ents[1])]
    assert any("overlaps" in p for p in solar.check(dup))
    assert any("blocked tile" in p for p in solar.check(ents, blocked=[(ents[1]["x"], ents[1]["y"])]))
    assert any("below" in p for p in solar.check([e for e in ents if e["name"] != "accumulator"], ratio=0.84))
    assert any("unknown entity" in p for p in solar.check(ents + [{"name": "pipe", "x": 40.5, "y": 0.5}]))
    two = ents + [dict(e, x=e["x"] + 19) for e in ents]  # 19 apart: beyond the 18 reach
    assert any("not wire-linked" in p for p in solar.check(two))
