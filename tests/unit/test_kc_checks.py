"""kc.checks: validators over a plan in kc.place_ghosts format."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import checks  # noqa: E402

SPECS = {
    "pumpjack": {"w": 3, "h": 3, "energy": "electric"},
    "small-lamp": {"w": 1, "h": 1, "energy": "electric"},
    "substation": {"w": 2, "h": 2, "supply_area_distance": 9, "max_wire_distance": 18},
    "big-electric-pole": {"w": 2, "h": 2, "supply_area_distance": 2, "max_wire_distance": 32},
    "pipe": {"w": 1, "h": 1},
}


def test_overlaps_and_blocked():
    plan = [{"name": "pumpjack", "x": 5.5, "y": 5.5}, {"name": "pipe", "x": 6.5, "y": 6.5},
            {"name": "pipe", "x": 8.5, "y": 5.5}]
    assert len(checks.overlaps(plan, SPECS)) == 1 and "#1 pipe" in checks.overlaps(plan, SPECS)[0]
    g = kc.Grid(blocked=[(8.5, 5.5)])
    assert len(checks.blocked(plan, g, SPECS)) == 1 and checks.blocked(plan[:1], g, SPECS) == []
    assert checks.blocked(plan, {(8.5, 5.5)}, SPECS)  # a plain tile set works too


def test_unpowered_counts_supply_squares_and_existing_poles():
    lamp_near = {"name": "small-lamp", "x": 8.5, "y": 0.5}
    lamp_far = {"name": "small-lamp", "x": 40.5, "y": 0.5}
    sub = {"name": "substation", "x": 0, "y": 0}
    assert checks.unpowered([sub, lamp_near], SPECS) == []
    assert len(checks.unpowered([sub, lamp_near, lamp_far], SPECS)) == 1
    assert checks.unpowered([lamp_far], SPECS, existing_poles=[{"x": 40, "y": 2, "supply": 2.5}]) == []


def test_unwired_uses_the_shorter_reach():
    a, b = {"name": "substation", "x": 0, "y": 0}, {"name": "substation", "x": 18.03, "y": 0}
    assert len(checks.unwired([a, b], SPECS)) == 1  # 18.03 apart, reach 18: no link
    assert checks.unwired([a, dict(b, x=17.5)], SPECS) == []
    grid = [{"x": -30, "y": 0, "reach": 32}]
    assert len(checks.unwired([a], SPECS, grid)) == 1  # 30 > min(18, 32)
    relay = {"name": "big-electric-pole", "x": -14, "y": 0}
    assert checks.unwired([a, relay], SPECS, grid) == []


def test_disconnected_and_all():
    line = [(x + 0.5, 0.5) for x in range(5)]
    assert checks.disconnected(line, [(0.5, 0.5), (4.5, 0.5)]) == []
    gap = line[:2] + line[3:]
    assert len(checks.disconnected(gap, [(0.5, 0.5), (4.5, 0.5)])) == 1
    assert len(checks.disconnected(line, [(0.5, 0.5), (9.5, 0.5)])) == 1
    plan = [{"name": "substation", "x": 0, "y": 0}, {"name": "small-lamp", "x": 30.5, "y": 0.5}]
    probs = checks.all(plan, SPECS, grid=kc.Grid(), grid_poles=[], connect=(gap, [(0.5, 0.5), (4.5, 0.5)]))
    assert len(probs) == 2, probs  # unpowered lamp + cut network
