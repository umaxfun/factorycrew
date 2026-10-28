"""Helpers available as `kc` inside factorio_compute (copied next to the user's code).

Tiles are tile CENTERS (x.5, y.5), the positions Factorio uses for 1x1 entities like pipes.
"""
from __future__ import annotations

import heapq
import math

DIRS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def tile(x, y=None):
    """The center of the tile containing (x, y). Also takes one point in any usual form:
    (x, y), [x, y], {"x": .., "y": ..}, {"position": {"x": .., "y": ..}}."""
    if y is None:
        x, y = xy(x)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def xy(p):
    """(x, y) from a tuple/list, a {x, y} dict or a {position: {x, y}} dict."""
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def footprint(x, y, w, h):
    """Tile centers covered by a w×h entity centered at (x, y) (e.g. a pumpjack: 3, 3)."""
    x0, y0 = x - w / 2, y - h / 2
    return {(math.floor(x0) + i + 0.5, math.floor(y0) + j + 0.5) for i in range(w) for j in range(h)}


def astar(start, goal, blocked, limit=20000):
    """Shortest 4-neighbour path of tile centers from start to goal avoiding `blocked` (a set of tiles).
    Both ends included. Returns [] if there is no path within `limit` expanded tiles."""
    start, goal = tile(start), tile(goal)
    blocked = {tile(b) for b in blocked}
    if start in blocked or goal in blocked:
        return []
    def h(p):
        return abs(p[0] - goal[0]) + abs(p[1] - goal[1])
    frontier = [(h(start), 0, start)]
    came = {start: None}
    cost = {start: 0}
    n = 0
    while frontier and n < limit:
        _, g, cur = heapq.heappop(frontier)
        if cur == goal:
            path = []
            while cur is not None:
                path.append(cur)
                cur = came[cur]
            return path[::-1]
        n += 1
        for dx, dy in DIRS4:
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in blocked or (nxt in cost and cost[nxt] <= g + 1):
                continue
            cost[nxt] = g + 1
            came[nxt] = cur
            heapq.heappush(frontier, (g + 1 + h(nxt), g + 1, nxt))
    return []


def connect(outputs, sink, blocked, margin=12):
    """ONE connected network of tiles joining every output tile to `sink` (e.g. all pumpjack output
    tiles to the field's output point), routed around `blocked` tiles. Grows the network output by
    output (nearest first), each time by the shortest path to ANY tile already in it, so branches
    share pipe. Returns {"tiles": [[x, y], ...], "failed": [outputs that could not be reached]}.
    The result is connected by construction; use it as is instead of hand-made feeder rows."""
    blocked = {tile(b) for b in blocked}
    sink = tile(sink)
    outs = [tile(o) for o in outputs]
    pts = outs + [sink]
    x0 = min(p[0] for p in pts) - margin
    x1 = max(p[0] for p in pts) + margin
    y0 = min(p[1] for p in pts) - margin
    y1 = max(p[1] for p in pts) + margin
    net = {sink}
    failed = []
    for o in sorted(outs, key=lambda p: abs(p[0] - sink[0]) + abs(p[1] - sink[1])):
        if o in net:
            continue
        if o in blocked:
            failed.append(list(o))
            continue
        came, frontier, hit = {o: None}, [o], None
        while frontier and hit is None:
            nxt = []
            for cur in frontier:
                for dx, dy in DIRS4:
                    n = (cur[0] + dx, cur[1] + dy)
                    if n in came or n in blocked or not (x0 <= n[0] <= x1 and y0 <= n[1] <= y1):
                        continue
                    came[n] = cur
                    if n in net:
                        hit = n
                        break
                    nxt.append(n)
                if hit is not None:
                    break
            frontier = nxt
        if hit is None:
            failed.append(list(o))
            continue
        cur = hit
        while cur is not None:
            net.add(cur)
            cur = came[cur]
    return {"tiles": [list(t) for t in sorted(net)], "failed": failed}


def unwired(poles, grid):
    """Poles NOT linked to `grid` by copper wire. poles: [(x, y, reach), ...], grid: (x, y, reach) of an
    existing pole. Two poles link when their distance ≤ the SHORTER of their two reaches (Factorio's
    rule; 18.03 apart with reach 18 does not link). A power layout is only done when this returns []."""
    def node(p):
        if isinstance(p, dict):
            return (*xy(p), float(p["reach"]))
        return (float(p[0]), float(p[1]), float(p[2]))
    nodes = [node(grid)] + [node(p) for p in poles]
    linked, todo = {0}, [0]
    while todo:
        i = todo.pop()
        for j, q in enumerate(nodes):
            if j in linked:
                continue
            p = nodes[i]
            if math.hypot(p[0] - q[0], p[1] - q[1]) <= min(p[2], q[2]) + 1e-9:
                linked.add(j)
                todo.append(j)
    return [list(nodes[j]) for j in range(1, len(nodes)) if j not in linked]


def unreached(pipes, start, targets):
    """Which `targets` (tiles) are NOT connected to `start` through 4-adjacent tiles of `pipes`.
    Use it before placing: a pipe network is only done when this returns []."""
    pipes = {tile(p) for p in pipes}
    seen, todo = set(), [tile(start)]
    while todo:
        p = todo.pop()
        if p in seen or p not in pipes:
            continue
        seen.add(p)
        todo.extend((p[0] + dx, p[1] + dy) for dx, dy in DIRS4)
    return [t for t in targets if tile(t) not in seen]
