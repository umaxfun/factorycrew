"""Geometry primitives: points, tiles, footprints, directions, transforms.

Conventions used across kc:
- A tile is named by its center (x.5, y.5).
- Entity positions follow the game: odd-sized entities sit on tile centers, even-sized on integers.
- Directions are Factorio 2.0 numbers: north 0, east 4, south 8, west 12 (16-way scale, so
  north-east is 2). Words ("north", "e", ...) are accepted wherever a direction is.
- Points may be (x, y), [x, y], {"x", "y"} or {"position": {"x", "y"}}.
"""
from __future__ import annotations

import math

N, E, S, W = 0, 4, 8, 12
CARDINALS = (N, E, S, W)
_WORDS = {"north": N, "n": N, "east": E, "e": E, "south": S, "s": S, "west": W, "w": W,
          "northeast": 2, "ne": 2, "southeast": 6, "se": 6, "southwest": 10, "sw": 10, "northwest": 14, "nw": 14}
_STEP = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}
NAMES = {N: "north", E: "east", S: "south", W: "west"}


# ── points and tiles ─────────────────────────────────────────────────────

def xy(p, y=None):
    """(x, y) floats from any point form, or from two numbers."""
    if y is not None:
        return float(p), float(y)
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def tile(p, y=None):
    """Center of the tile containing the point: tile((3.2, -0.1)) == (3.5, -0.5)."""
    x, y = xy(p, y)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def snap(p, w=1, h=1, direction=N):
    """Position the game uses for a w×h entity placed at p: odd sizes on tile centers, even on integers.
    East/west rotation swaps w and h."""
    x, y = xy(p)
    w, h = oriented_size(w, h, direction)

    def one(v, size):
        return math.floor(v) + 0.5 if int(size) % 2 == 1 else float(math.floor(v + 0.5))
    return (one(x, w), one(y, h))


def oriented_size(w, h, direction=N):
    d = dir_of(direction)
    return (h, w) if d in (E, W) else (w, h)


def rect(center, w, h, direction=N):
    """(x0, y0, x1, y1) box of a w×h entity centered at `center` (rotation applied)."""
    x, y = xy(center)
    w, h = oriented_size(w, h, direction)
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def footprint(*args, direction=N):
    """Tile centers covered by an entity.

    footprint(center, w, h[, direction]) or the older footprint(x, y, w, h[, direction])."""
    if isinstance(args[0], (int, float)):
        center, rest = (args[0], args[1]), args[2:]
    else:
        center, rest = args[0], args[1:]
    w, h = rest[0], rest[1]
    if len(rest) > 2:
        direction = rest[2]
    x0, y0, x1, y1 = rect(center, w, h, direction)
    out = set()
    tx = math.floor(x0 + 1e-9)
    while tx + 0.5 < x1:
        ty = math.floor(y0 + 1e-9)
        while ty + 0.5 < y1:
            out.add((tx + 0.5, ty + 0.5))
            ty += 1
        tx += 1
    return out


def tiles_in(box):
    """Tile centers inside a box (x0, y0, x1, y1), sorted row by row."""
    x0, y0, x1, y1 = box
    return [(x + 0.5, y + 0.5) for y in range(math.floor(y0), math.ceil(y1)) for x in range(math.floor(x0), math.ceil(x1))]


def bbox(points, margin=0):
    """(x0, y0, x1, y1) around points (tile centers count as their whole tile)."""
    ps = [xy(p) for p in points]
    if not ps:
        raise ValueError("bbox of no points")
    return (min(p[0] for p in ps) - 0.5 - margin, min(p[1] for p in ps) - 0.5 - margin,
            max(p[0] for p in ps) + 0.5 + margin, max(p[1] for p in ps) + 0.5 + margin)


def overlaps(a, b):
    """Whether two boxes (x0, y0, x1, y1) overlap with positive area."""
    return a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9 and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9


# ── directions ───────────────────────────────────────────────────────────

def dir_of(d):
    """Normalize a direction: number (any multiple of 1 on the 16-way scale) or word."""
    if d is None:
        return None
    if isinstance(d, str):
        k = d.strip().lower().replace("-", "").replace("_", "")
        if k not in _WORDS:
            raise ValueError(f"unknown direction {d!r}")
        return _WORDS[k]
    return int(d) % 16


def step(d):
    """Unit vector of a cardinal direction."""
    d = dir_of(d)
    if d not in _STEP:
        raise ValueError(f"step() needs a cardinal direction, got {d}")
    return _STEP[d]


def move(p, d, k=1):
    x, y = xy(p)
    dx, dy = step(d)
    return (x + dx * k, y + dy * k)


def left(d):
    return (dir_of(d) - 4) % 16


def right(d):
    return (dir_of(d) + 4) % 16


def opposite(d):
    return (dir_of(d) + 8) % 16


def axis(d):
    """0 for north-south, 4 for east-west."""
    return dir_of(d) % 8


def toward(a, b):
    """Cardinal direction that best points from a to b (ties go to the x axis)."""
    ax, ay = xy(a)
    bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    if abs(dx) >= abs(dy) and dx != 0:
        return E if dx > 0 else W
    if dy != 0:
        return S if dy > 0 else N
    return N


# ── transforms ───────────────────────────────────────────────────────────

def rotate(p, quarter_turns=1, around=(0.0, 0.0)):
    """Rotate a point clockwise (screen coordinates: y down) by 90° steps around a point."""
    x, y = xy(p)
    cx, cy = xy(around)
    dx, dy = x - cx, y - cy
    for _ in range(int(quarter_turns) % 4):
        dx, dy = -dy, dx
    return (cx + dx, cy + dy)


def rotate_dir(d, quarter_turns=1):
    return (dir_of(d) + 4 * int(quarter_turns)) % 16


def mirror(p, axis_x=None, axis_y=None):
    """Mirror a point across the vertical line x = axis_x and/or the horizontal line y = axis_y."""
    x, y = xy(p)
    if axis_x is not None:
        x = 2 * axis_x - x
    if axis_y is not None:
        y = 2 * axis_y - y
    return (x, y)


def mirror_dir(d, horizontal=True):
    """Direction after mirroring left-right (horizontal=True) or top-bottom."""
    d = dir_of(d)
    return (16 - d) % 16 if horizontal else (8 - d) % 16


# ── distances and orderings ──────────────────────────────────────────────

def manhattan(a, b):
    ax, ay = xy(a)
    bx, by = xy(b)
    return abs(ax - bx) + abs(ay - by)


def chebyshev(a, b):
    ax, ay = xy(a)
    bx, by = xy(b)
    return max(abs(ax - bx), abs(ay - by))


def euclid(a, b):
    ax, ay = xy(a)
    bx, by = xy(b)
    return math.hypot(ax - bx, ay - by)


def neighbors4(t):
    x, y = xy(t)
    return [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]


def neighbors8(t):
    x, y = xy(t)
    return [(x + dx, y + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]


def spiral(center, radius):
    """Tile centers around `center` ring by ring (Chebyshev distance 0, 1, ... radius), in a fixed order."""
    cx, cy = tile(center)
    yield (cx, cy)
    for r in range(1, int(radius) + 1):
        for i in range(-r, r + 1):
            yield (cx + i, cy - r)
        for i in range(-r + 1, r + 1):
            yield (cx + r, cy + i)
        for i in range(r - 1, -r - 1, -1):
            yield (cx + i, cy + r)
        for i in range(r - 1, -r, -1):
            yield (cx - r, cy + i)
