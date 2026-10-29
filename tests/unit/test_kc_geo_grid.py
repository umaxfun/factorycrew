"""kc.geo and kc.grid: the base layer of the primitive library."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402
from kc import geo  # noqa: E402
from kc.grid import Grid  # noqa: E402


def test_points_tiles_and_snap():
    assert geo.xy({"position": {"x": 1, "y": 2}}) == (1.0, 2.0)
    assert geo.tile((3.2, -0.1)) == (3.5, -0.5)
    assert geo.snap((10.3, 4.2), 3, 3) == (10.5, 4.5)  # odd size: tile center
    assert geo.snap((10.3, 4.2), 2, 2) == (10.0, 4.0)  # even size: integer
    assert geo.snap((10.3, 4.2), 2, 1, "east") == (10.5, 4.0)  # rotated 2x1 is 1 wide, 2 tall


def test_footprint_both_signatures_and_rotation():
    assert geo.footprint((298.5, 309.5), 3, 3) == geo.footprint(298.5, 309.5, 3, 3)
    assert len(geo.footprint((0.5, 0.5), 3, 3)) == 9
    assert geo.footprint((10, 4), 2, 2) == {(9.5, 3.5), (10.5, 3.5), (9.5, 4.5), (10.5, 4.5)}
    assert geo.footprint((0.5, 0.0), 1, 2) == {(0.5, -0.5), (0.5, 0.5)}
    assert geo.footprint((0.0, 0.5), 1, 2, direction="east") == {(-0.5, 0.5), (0.5, 0.5)}


def test_directions():
    assert geo.dir_of("west") == 12 and geo.dir_of(20) == 4
    assert geo.left(0) == 12 and geo.right(12) == 0 and geo.opposite(4) == 12
    assert geo.move((0.5, 0.5), "south", 3) == (0.5, 3.5)
    assert geo.toward((0, 0), (5, -1)) == geo.E and geo.toward((0, 0), (1, -5)) == geo.N
    assert geo.axis(4) == geo.axis(12) == 4


def test_transforms():
    assert geo.rotate((1, 0)) == (0, 1)  # clockwise on screen (y down)
    assert geo.rotate((1, 0), 4) == (1, 0)
    assert geo.rotate_dir(0, 1) == 4 and geo.rotate_dir(12, 1) == 0
    assert geo.mirror((3, 1), axis_x=0) == (-3, 1)
    assert geo.mirror_dir(4) == 12 and geo.mirror_dir(2) == 14 and geo.mirror_dir(0, horizontal=False) == 8


def test_spiral_is_ordered_and_complete():
    ring = list(geo.spiral((0.5, 0.5), 2))
    assert ring[0] == (0.5, 0.5) and len(ring) == 25 and len(set(ring)) == 25
    assert all(geo.chebyshev(t, (0.5, 0.5)) <= 2 for t in ring)
    assert list(geo.spiral((0.5, 0.5), 2)) == ring  # deterministic


def test_grid_place_fit_and_bounds():
    g = Grid(blocked=[(2.5, 0.5)], bounds=(-10, -10, 10, 10))
    assert not g.free((2.5, 0.5)) and g.free((1.5, 0.5)) and not g.free((20.5, 0.5))
    jack = g.place({"name": "pumpjack", "x": 5.5, "y": 5.5, "w": 3, "h": 3})
    assert jack and g.occupant((6.5, 6.5)) == jack
    assert g.place({"name": "pipe", "x": 4.5, "y": 4.5}) is None  # inside the pumpjack
    assert g.can_place((0.0, 0.0), 2, 2)
    spot = g.first_fit((5.5, 5.5), 3, 3, radius=10)
    assert spot and g.can_place(spot, 3, 3) and spot != (5.5, 5.5)
    g2 = g.copy()
    assert g2.remove(jack) and g2.free((6.5, 6.5)) and not g.free((6.5, 6.5))


def test_old_kc_compute_names_still_work():
    assert kc.footprint(1.5, 1.5, 3, 3) == geo.footprint((1.5, 1.5), 3, 3)
    assert kc.unwired([(18.03, 0, 18)], (0, 0, 18)) == [[18.03, 0.0, 18.0]]
    assert kc.unreached([(0.5, 0.5), (1.5, 0.5)], (0.5, 0.5), [(1.5, 0.5), (3.5, 0.5)]) == [(3.5, 0.5)]
