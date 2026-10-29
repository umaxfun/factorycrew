"""kc: primitive library for Kira's factorio_compute (stdlib only, Python 3.9+).

Layers (lower never imports higher):
  geo      points, tiles, footprints, directions, transforms
  grid     occupancy: what is where, can an entity fit
  search   BFS, A*, flood fill / components on any neighbour function
  steiner  join many terminals into one connected network
  cover    greedy set cover (fewest candidates covering all targets)
  wires    wire links by the shorter-reach rule, components, relay chains
  lp       exact linear programming on fractions
  bp       blueprint string codec and transforms
  checks   validators: overlaps, connectivity, coverage, wiring
  circuits circuit networks read by the mod's kc.circuits: simulate, explain, trace
  world    adapters for the mod's kc.scan / kc.proto / kc.resources / kc.best (Lua) results

Skills are built on these and live in skills/<name>/lib. The names below are the stable surface;
`import kc` gives the old kc_compute helpers too (tile, xy, footprint, astar, connect, unwired,
unreached), kept for existing code.
"""
from __future__ import annotations

from . import geo, grid  # noqa: F401
from .geo import (  # noqa: F401
    CARDINALS, E, N, S, W, bbox, dir_of, footprint, manhattan, move, opposite, rect, rotate,
    snap, spiral, tile, toward, xy,
)
from .grid import Grid  # noqa: F401

import importlib as _importlib

MODULES = ("search", "steiner", "cover", "wires", "lp", "bp", "world", "circuits", "checks")

for _m in MODULES:
    try:  # modules land one by one; a missing one must not break `import kc`
        globals()[_m] = _importlib.import_module(f".{_m}", __name__)
    except ImportError:  # pragma: no cover
        pass

from ._compat import astar, connect, unreached, unwired  # noqa: F401,E402
