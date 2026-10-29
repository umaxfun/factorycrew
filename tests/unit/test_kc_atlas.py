"""kc.atlas on the fixture dump (a player's save, docs/atlas.md "Fixture").

The dump comes from `scripts/atlas-fixture.sh dump`; without it these tests skip.
Coordinates below are places on that save.
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
from kc import atlas as A  # noqa: E402

DUMP = Path(os.environ.get("FC_ATLAS_OUT", ROOT / ".test-data" / "atlas" / "dump.txt"))
TETRA = "nullius-titanium-tetrachloride"


@pytest.fixture(scope="module")
def at():
    if not DUMP.exists():
        pytest.skip(f"no fixture dump at {DUMP}: run scripts/atlas-fixture.sh dump (docs/atlas.md, Fixture)")
    t = time.monotonic()
    a = A.Atlas(DUMP.read_text())
    a.build_seconds = time.monotonic() - t
    return a


def test_parse_counts_match_the_footer(at):
    d = A.parse(DUMP.read_text())
    assert d["footer"]["entities"] == len(d["entities"]) > 90000
    assert TETRA in d["recipes"]


def test_parse_refuses_a_cut_dump():
    text = "H|1|nauvis|1|300\nN|1|iron-chest|container|1|1\nE|1|1|1|0\nZ|2|1|1"
    with pytest.raises(ValueError, match="incomplete"):
        A.parse(text)


def test_whole_block_from_one_furnace_and_from_the_opposite_corner(at):
    one = at.blocks_in_box(-358.5, -124, -356, -121)
    other = at.blocks_in_box(-375, -136, -372, -133)
    assert one == other and len(one) == 1
    b = at.blocks[one[0]]
    assert b["recipe"] == TETRA and len(b["machines"]) == 32
    x0, y0, x1, y1 = b["bbox"]
    assert (round(x0), round(y0), round(x1), round(y1)) == (-375, -136, -348, -112)


def test_tetrachloride_inputs_come_from_two_stops_and_a_pipe(at):
    bi = at.block_at(-357.5, -122.5)
    node = ("B", bi)
    graphite = at.sources(node, "nullius-graphite")
    rutile = at.sources(node, "nullius-rutile")
    chlorine = at.sources(node, "nullius-chlorine")
    assert len(graphite) == 1 and next(iter(graphite)).startswith("S")
    assert len(rutile) == 1 and next(iter(rutile)).startswith("S")
    g_stop = at.stops[int(next(iter(graphite))[1:])]
    r_stop = at.stops[int(next(iter(rutile))[1:])]
    assert "nullius-graphite" in g_stop["s"] and "nullius-rutile" in r_stop["s"]
    assert chlorine and all(k == ["pipe"] for k in chlorine.values())
    assert all(n.startswith("B") for n in chlorine)


def test_fluids_only_by_pipe_items_never_by_pipe(at):
    for (a, b), items in at.edges.items():
        for it, kinds in items.items():
            if at.kind.get(it) == "fluid":
                assert "pipe" in kinds, (a, b, it, kinds)
            else:
                assert kinds - {"pipe"}, (a, b, it, kinds)


def test_shared_belt_does_not_join_unrelated_blocks(at):
    for (a, b), items in at.edges.items():
        if a[0] == "B" and b[0] == "B":
            assert items and set(items) <= at.blocks[a[1]]["pro"] & at.blocks[b[1]]["ing"]


def test_underground_reach_comes_from_the_prototype(at):
    assert at.ug["express-underground-belt"] == 15


def test_build_time(at):
    assert at.build_seconds < 20
