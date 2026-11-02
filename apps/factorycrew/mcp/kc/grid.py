"""Occupancy grid: which tiles are taken, by what, and whether an entity fits.

Built from what data_lua reads (entities with sizes, water/cliff tiles), then used by every
planner: search uses `passable`, placement uses `can_place` / `place`, checks use `occupant`.
"""
from __future__ import annotations

from . import geo


class Grid:
    """Tiles -> occupant id. Tiles outside `bounds` (x0, y0, x1, y1) count as blocked, when given.

    Occupants are whatever id the caller chooses (an entity dict index, a name, True for terrain).
    Entities placed through `place` get an integer id and are kept in `entities`.
    """

    def __init__(self, blocked=(), entities=(), bounds=None):
        self.occ = {}
        self.entities = {}
        self.bounds = tuple(bounds) if bounds else None
        self._next = 1
        for t in blocked:
            self.occ[geo.tile(t)] = True
        for e in entities:
            self.place(e, force=True)

    # ── queries ──
    def inside(self, t):
        if self.bounds is None:
            return True
        x, y = geo.xy(t)
        x0, y0, x1, y1 = self.bounds
        return x0 <= x <= x1 and y0 <= y <= y1

    def free(self, t):
        t = geo.tile(t)
        return t not in self.occ and self.inside(t)

    def occupant(self, t):
        return self.occ.get(geo.tile(t))

    def passable(self, t):
        """For search: a free tile inside bounds."""
        return self.free(t)

    def can_place(self, center, w=1, h=1, direction=geo.N):
        return all(self.free(t) for t in geo.footprint(geo.snap(center, w, h, direction), w, h, direction))

    def blocked_tiles(self):
        return set(self.occ)

    # ── changes ──
    def place(self, entity, force=False):
        """Mark an entity {name, x, y, w?, h?, direction?} (w/h default 1). Returns its id, or None
        when it does not fit and force is False."""
        w, h = int(entity.get("w", 1)), int(entity.get("h", 1))
        d = entity.get("direction", geo.N)
        center = geo.snap(entity, w, h, d)
        tiles = geo.footprint(center, w, h, d)
        if not force and not all(self.free(t) for t in tiles):
            return None
        eid = self._next
        self._next += 1
        self.entities[eid] = dict(entity, x=center[0], y=center[1])
        for t in tiles:
            self.occ[t] = eid
        return eid

    def remove(self, eid):
        e = self.entities.pop(eid, None)
        if e is None:
            return False
        for t, o in list(self.occ.items()):
            if o == eid:
                del self.occ[t]
        return True

    def copy(self):
        g = Grid(bounds=self.bounds)
        g.occ = dict(self.occ)
        g.entities = {k: dict(v) for k, v in self.entities.items()}
        g._next = self._next
        return g

    def first_fit(self, center, w=1, h=1, direction=geo.N, radius=32):
        """Nearest position to `center` (spiral search) where a w×h entity fits, or None."""
        for t in geo.spiral(center, radius):
            p = geo.snap(t, w, h, direction)
            if self.can_place(p, w, h, direction):
                return p
        return None
