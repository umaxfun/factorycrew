"""Fluid networks for factorio_compute (`import pipes`).

join(): one connected pipe network joining every terminal around blocked tiles. A terminal
may offer several alternative tiles (a pumpjack's output tile per direction); the join picks
one per terminal. Steiner-tree heuristic ported from OilOutpostPlanner's SolveSteinerTree:
flood-fill to keep only the largest connected region holding terminal tiles, then grow a BFS
front from every terminal in parallel, level by level; the first time two fronts touch, the
shortest path between their two terminals becomes pipe, the two terminals merge into one whose
front restarts from all of its pipe, and the growth goes on until one terminal is left.

compress(): straight runs of pipe become pipe-to-ground pairs (OilOutpostPlanner's
ReplacePipes): a pipe can be buried only if no pipe or pipe-to-ground sits beside it across
the run, so side connections stay; runs longer than the reach become a chain of pairs. Added
here: a run end becomes a pipe-to-ground only if what it faces is still a pipe or the machine
port the tile serves, so a dead end never turns into an unconnected underground.

oil_field(): pumpjacks on the wells, the join over their four output tiles plus one sink tile
on the field's edge (which output tile the join picks fixes each pumpjack's direction), then
compress. check(): verifies a layout with the game's 2.0 connection rules.

Tiles are tile centers (x.5, y.5). Directions are Factorio 2.0 numbers: north 0, east 4,
south 8, west 12. A 2.0 pipe-to-ground has no input/output type: its `direction` is the side of
its one above-ground connection, and it reaches underground the opposite way. A pair is two of
them facing away from each other.

Ported from https://github.com/Coppermine-factorio/OilOutpostPlanner (MIT, (c) 2024
Coppermine) at commit 72015903b0ed492c28b6b5fa7cae575873c9c9f5, layout.lua; see SOURCE.md.
"""
from __future__ import annotations

import math

STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
NAMES = {0: "north", 4: "east", 8: "south", 12: "west"}
# vanilla 2.0 pumpjack: output tile per pumpjack direction, relative to its center
PUMPJACK_OUTPUTS = {0: (1, -2), 4: (2, 1), 8: (-1, 2), 12: (-2, -1)}  # read from 2.1.23 in game


def _xy(p):
    if isinstance(p, dict):
        if "position" in p:
            p = p["position"]
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _tile(p):
    x, y = _xy(p)
    return (math.floor(x) + 0.5, math.floor(y) + 0.5)


def _dir(d):
    if d is None:
        return None
    if isinstance(d, str):
        return {v: k for k, v in NAMES.items()}[d.lower()]
    d = int(d) % 16
    if d not in STEP:
        raise ValueError(f"direction must be a cardinal 0/4/8/12, got {d}")
    return d


def _move(p, d, k=1):
    dx, dy = STEP[d]
    return (p[0] + dx * k, p[1] + dy * k)


def _nb(p):
    return [_move(p, d) for d in (0, 4, 8, 12)]


def _between(a, b):
    """Direction from tile a to the 4-adjacent tile b."""
    for d in (0, 4, 8, 12):
        if _move(a, d) == b:
            return d
    return None


def footprint(x, y=None, w=3, h=3):
    """Tile centers covered by a w x h entity centered at (x, y)."""
    if y is None:
        x, y = _xy(x)
    x0, y0 = x - w / 2, y - h / 2
    return {(math.floor(x0 + 1e-9) + i + 0.5, math.floor(y0 + 1e-9) + j + 0.5) for i in range(w) for j in range(h)}


def _options(t):
    if isinstance(t, dict) and "options" in t:
        out = []
        for o in t["options"]:
            o = _tile(o)
            if o not in out:
                out.append(o)
        return out
    return [_tile(t)]


def _flood(origin, ok):
    seen, todo = {origin}, [origin]
    while todo:
        nxt = []
        for p in todo:
            for n in _nb(p):
                if n not in seen and ok(n):
                    seen.add(n)
                    nxt.append(n)
        todo = nxt
    return seen


def join(terminals, blocked=(), *, margin=4, budget=400_000):
    """One connected network of tiles joining every terminal, around `blocked` tiles.

    terminals: points, or {"options": [points]} for a terminal that may use any one of several
        tiles (the choice is returned). At least one terminal.
    margin: how far outside the terminals' bounding box the network may go, in tiles.
    Returns {"ok": True, "tiles": [[x, y], ...] sorted, "choice": [option index per terminal],
    "pipes": n} or {"ok": False, "reason": ..., "unreachable": [terminal indices]}.
    """
    blocked = {_tile(b) for b in blocked}
    targets = [_options(t) for t in terminals]
    if not targets:
        return {"ok": False, "reason": "no terminals", "unreachable": []}
    pts = [p for opts in targets for p in opts]
    x0, x1 = min(p[0] for p in pts) - margin, max(p[0] for p in pts) + margin
    y0, y1 = min(p[1] for p in pts) - margin, max(p[1] for p in pts) + margin

    def free(t):
        return t not in blocked and x0 <= t[0] <= x1 and y0 <= t[1] <= y1

    # keep only options that are free and in the largest region holding options
    live = [[(i, o) for i, o in enumerate(opts) if free(o)] for opts in targets]
    bad = [k for k, opts in enumerate(live) if not opts]
    if bad:
        return {"ok": False, "reason": "every tile of these terminals is blocked or out of bounds", "unreachable": bad}
    remaining = sorted({o for opts in live for _, o in opts})
    best = None
    while remaining:
        region = _flood(remaining[0], free)
        inside = [o for o in remaining if o in region]
        if best is None or len(inside) > best[0]:
            best = (len(inside), region)
        remaining = [o for o in remaining if o not in region]
    reach = best[1]
    live = [[(i, o) for i, o in opts if o in reach] for opts in live]
    bad = [k for k, opts in enumerate(live) if not opts]
    if bad:
        return {"ok": False, "reason": "these terminals cannot reach the others", "unreachable": bad}

    choice = [None] * len(targets)
    if len(targets) == 1:
        choice[0] = live[0][0][0]
        return {"ok": True, "tiles": [list(live[0][0][1])], "choice": choice, "pipes": 1}

    # claim[tile] = (target, previous tile on the way back to the target or None, option or None)
    claim, hood, hidx, paths = {}, {}, {}, set()

    def clear(t):
        for pos, _ in hood.pop(t, []):
            claim.pop(pos, None)
        hidx.pop(t, None)

    for t, opts in enumerate(live):
        hood[t], hidx[t] = [], 0
        for i, o in opts:
            if o in claim:
                # two terminals share a starting tile: both take it, and they are one terminal now
                choice[t] = i
                clear(t)
                other, _, oi = claim[o]
                if oi is not None:
                    choice[other] = oi
                    clear(other)
                    claim[o] = (other, None, None)
                    hood[other], hidx[other] = [(o, 1)], 0
                    paths.add(o)
                break
            claim[o] = (t, None, i)
            hood[t].append((o, 1))

    expanded = 0

    def find_merge():
        nonlocal expanded
        level = 1
        while True:
            for t in sorted(hood):
                h, idx = hood[t], hidx[t]
                while True:
                    if idx >= len(h):
                        return None
                    pos, dist = h[idx]
                    if dist > level:
                        break
                    expanded += 1
                    if expanded > budget:
                        return "budget"
                    for c in _nb(pos):
                        if c not in reach:
                            continue
                        cl = claim.get(c)
                        if cl is None:
                            claim[c] = (t, pos, None)
                            h.append((c, level + 1))
                        elif cl[0] != t:
                            return (t, cl[0], pos, c)
                    idx += 1
                hidx[t] = idx
            level += 1

    while len(hood) > 1:
        m = find_merge()
        if m is None or m == "budget":
            why = "search budget spent" if m == "budget" else "a terminal's front ran out of room"
            return {"ok": False, "reason": why, "unreachable": []}
        t1, t2, p1, p2 = m
        path, chosen = [], {}
        for pos in (p1, p2):
            while True:
                path.append(pos)
                tgt, prev, oi = claim[pos]
                if prev is not None:
                    pos = prev
                    continue
                if oi is not None:
                    choice[tgt] = oi
                    chosen[tgt] = oi
                break
        old = []
        for t in (t1, t2):
            for pos, dist in hood[t]:
                if dist > 1:
                    break
                oi = claim[pos][2]
                if oi is None or oi == chosen.get(t):
                    old.append((pos, 1))
        clear(t1)
        clear(t2)
        hood[t1], hidx[t1] = old, 0
        for pos, _ in old:
            claim[pos] = (t1, None, None)
        for pos in path:
            paths.add(pos)
            if pos not in claim:
                old.append((pos, 1))
                claim[pos] = (t1, None, None)
    for t, opts in enumerate(live):
        if choice[t] is None:  # the last terminal standing, never merged into another
            for i, o in opts:
                if o in paths:
                    choice[t] = i
                    break
    tiles = sorted(paths)
    return {"ok": True, "tiles": [list(p) for p in tiles], "choice": choice, "pipes": len(tiles)}


def _ports(ports):
    out = {}
    for p in ports or ():
        if isinstance(p, dict):
            t, d = _tile(p), _dir(p["direction"])
        else:
            t, d = _tile((p[0], p[1])), _dir(p[2])
        out.setdefault(t, set()).add(d)
    return out


def compress(pipes, *, pipe="pipe", pipe_to_ground="pipe-to-ground", pipe_to_ground_reach=10,
             min_run=3, ports=(), keep=()):
    """Bury straight runs of `pipes` (tiles) as pipe-to-ground pairs.

    pipe_to_ground_reach: the most tiles from one half of a pair to the other (2.0 vanilla:
        max_underground_distance 10). Must be >= 2. 0 or None: return the pipes as they are.
    min_run: shortest run worth burying (2 pipe-to-grounds replace it).
    ports: tiles where a machine connects, as {x, y, direction} or (x, y, direction), direction
        = the side of the tile the machine is on (pumpjack output tile -> toward the pumpjack).
    keep: tiles that must stay plain pipe (the sink you connect to later).
    Returns {"entities": [{name, x, y, direction?}], "pipes": n, "pipe_to_ground": n, "pairs": n}.
    """
    P = {_tile(p) for p in pipes}
    ports = _ports(ports)
    keep = {_tile(k) for k in keep}
    U = {}
    reach = int(pipe_to_ground_reach or 0)
    if reach >= 2:
        for lo, hi in ((0, 8), (12, 4)):  # north-south runs first, then west-east, as upstream
            side = (4, 12) if lo == 0 else (0, 8)

            def ok(t):
                if t not in P or t in keep:
                    return False
                if any(d not in (lo, hi) for d in ports.get(t, ())):
                    return False
                return not any(_move(t, s) in P or _move(t, s) in U for s in side)

            cand = sorted((t for t in P if ok(t)), key=(lambda t: (t[0], t[1])) if lo == 0 else (lambda t: (t[1], t[0])))
            runs, cur = [], []
            for t in cand:
                if cur and _move(cur[-1], hi) == t:
                    cur.append(t)
                else:
                    if cur:
                        runs.append(cur)
                    cur = [t]
            if cur:
                runs.append(cur)
            for run in runs:
                if not (_move(run[0], lo) in P or lo in ports.get(run[0], ())):
                    run = run[1:]
                if run and not (_move(run[-1], hi) in P or hi in ports.get(run[-1], ())):
                    run = run[:-1]
                n_tiles = len(run)
                if n_tiles < max(min_run, 2):
                    continue
                for t in run:
                    P.discard(t)
                n = -(-n_tiles // (reach + 1))
                for i in range(n):
                    a = int(math.floor(n_tiles * i / n + 0.5))
                    b = int(math.floor(n_tiles * (i + 1) / n + 0.5)) - 1
                    U[run[a]] = lo
                    U[run[b]] = hi
    ents = [{"name": pipe, "x": t[0], "y": t[1]} for t in sorted(P)]
    ents += [{"name": pipe_to_ground, "x": t[0], "y": t[1], "direction": U[t]} for t in sorted(U)]
    return {"entities": ents, "pipes": len(P), "pipe_to_ground": len(U), "pairs": len(U) // 2}


def _outputs(output_offsets):
    oo = output_offsets or PUMPJACK_OUTPUTS
    return {_dir(k): (float(_xy(v)[0]), float(_xy(v)[1])) for k, v in oo.items()}


def _port_of(center, d, outputs, w, h):
    """(output tile, body tile it connects to) of a pumpjack at `center` facing `d`."""
    off = outputs[d]
    o = _tile((center[0] + off[0], center[1] + off[1]))
    body = footprint(center[0], center[1], w, h)
    inside = [n for n in _nb(o) if n in body]
    return o, (inside[0] if inside else None)


def _sink_on_edge(occupied, toward, bad):
    xs = [t[0] for t in occupied]
    ys = [t[1] for t in occupied]
    x0, x1, y0, y1 = min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1
    ring = set()
    x = x0
    while x <= x1:
        ring.add((x, y0))
        ring.add((x, y1))
        x += 1
    y = y0
    while y <= y1:
        ring.add((x0, y))
        ring.add((x1, y))
        y += 1
    tx, ty = _xy(toward)
    ring = sorted(t for t in ring if t not in bad)
    if not ring:
        return None
    return min(ring, key=lambda t: ((t[0] - tx) ** 2 + (t[1] - ty) ** 2, t))


def oil_field(wells, blocked=(), *, pumpjack_size=3, output_offsets=None, sink=None, toward=None,
              foreign_pipes=(), foreign_ports=(), pumpjack="pumpjack", pipe="pipe",
              pipe_to_ground="pipe-to-ground", pipe_to_ground_reach=10, min_run=3, margin=4,
              budget=400_000):
    """Pumpjacks on `wells` plus one pipe network joining their outputs to a sink tile.

    wells: pumpjack centers (the crude-oil resource positions).
    blocked: tiles nothing may be placed on (entities, ghosts, water, cliffs); not the wells.
    pumpjack_size: tile_width of the pumpjack prototype (square assumed; a w x h pair also works).
    output_offsets: {direction: (dx, dy)} = the tile a pumpjack facing that direction outputs
        into, relative to its center; read from the prototype (see SKILL.md). Default vanilla.
    sink: the tile where the field's network ends (where you connect the outside to).
        None: the free tile on the ring just outside the field nearest `toward`.
        Both None: no sink, the pumpjacks are only joined to each other.
    foreign_pipes: tiles of existing fluid entities that connect on all sides (pipes, tanks...);
        no new pipe may touch them except the sink. foreign_ports: tiles some existing machine
        connects into; no new pipe may sit there.
    Returns {"ok": True, "entities": pumpjacks + pipes + pipe-to-grounds for kc.place_ghosts,
    "pumpjacks", "pipes", "pipe_to_ground", "pipe_tiles" (before burying), "sink", "problems"}
    or {"ok": False, "reason": ...}.
    """
    w, h = (pumpjack_size, pumpjack_size) if isinstance(pumpjack_size, (int, float)) else tuple(pumpjack_size)
    w, h = int(w), int(h)
    outputs = _outputs(output_offsets)
    blocked = {_tile(b) for b in blocked}
    centers = []
    for p in wells:
        x, y = _xy(p)
        c = (math.floor(x) + 0.5 if w % 2 else float(round(x)), math.floor(y) + 0.5 if h % 2 else float(round(y)))
        if c not in centers:
            centers.append(c)
    if not centers:
        return {"ok": False, "reason": "no wells"}
    occupied, owner = set(), {}
    for k, c in enumerate(centers):
        for t in footprint(c[0], c[1], w, h):
            if t in blocked:
                return {"ok": False, "reason": f"pumpjack at {list(c)} overlaps a blocked tile {list(t)}"}
            if t in owner:
                return {"ok": False, "reason": f"pumpjacks at {list(c)} and {list(centers[owner[t]])} overlap"}
            owner[t] = k
            occupied.add(t)
    fp = {_tile(t) for t in foreign_pipes}
    avoid = {_tile(t) for t in foreign_ports} | {n for t in fp for n in _nb(t)}
    bad = blocked | occupied | avoid | fp
    if sink is not None:
        sink_t = _tile(sink)
        if sink_t in blocked or sink_t in occupied or sink_t in fp:
            return {"ok": False, "reason": f"sink tile {list(sink_t)} is blocked"}
    elif toward is not None:
        sink_t = _sink_on_edge(occupied, toward, bad)
        if sink_t is None:
            return {"ok": False, "reason": "no free tile on the field's edge for the sink"}
    else:
        sink_t = None
    dirs = sorted(outputs)
    terms = [{"options": [_port_of(c, d, outputs, w, h)[0] for d in dirs]} for c in centers]
    if sink_t is not None:
        terms.append(sink_t)
    j = join(terms, bad - ({sink_t} if sink_t else set()), margin=margin, budget=budget)
    if not j["ok"]:
        who = [list(centers[k]) if k < len(centers) else "sink" for k in j.get("unreachable", [])]
        return {"ok": False, "reason": f"pipes: {j['reason']}" + (f" ({who})" if who else "")}
    jacks, ports = [], []
    for k, c in enumerate(centers):
        opts = terms[k]["options"]
        d = None
        # options were deduplicated in join; map the chosen tile back to a direction
        uniq = []
        for o in opts:
            if o not in uniq:
                uniq.append(o)
        tile = uniq[j["choice"][k]]
        for dd in dirs:
            if _port_of(c, dd, outputs, w, h)[0] == tile:
                d = dd
                break
        o, body = _port_of(c, d, outputs, w, h)
        jacks.append({"name": pumpjack, "x": c[0], "y": c[1], "direction": d})
        ports.append((o[0], o[1], _between(o, body)))
    cp = compress(j["tiles"], pipe=pipe, pipe_to_ground=pipe_to_ground, pipe_to_ground_reach=pipe_to_ground_reach,
                  min_run=min_run, ports=ports, keep=[sink_t] if sink_t else [])
    ents = jacks + cp["entities"]
    problems = check(ents, blocked, pumpjack=pumpjack, pumpjack_size=(w, h), output_offsets=outputs,
                     pipe=pipe, pipe_to_ground=pipe_to_ground, pipe_to_ground_reach=pipe_to_ground_reach,
                     sink=sink_t, foreign_pipes=fp, foreign_ports=foreign_ports)
    return {"ok": True, "entities": ents, "pumpjacks": len(jacks), "pipes": cp["pipes"],
            "pipe_to_ground": cp["pipe_to_ground"], "pipe_tiles": j["pipes"],
            "sink": list(sink_t) if sink_t else None, "problems": problems}


def check(entities, blocked=(), *, pumpjack="pumpjack", pumpjack_size=3, output_offsets=None, pipe="pipe",
          pipe_to_ground="pipe-to-ground", pipe_to_ground_reach=10, sink=None, foreign_pipes=(), foreign_ports=()):
    """Problems with a fluid layout as a list of strings ([] = valid).

    entities: pumpjacks (with direction), pipes and pipe-to-grounds (with direction), by name.
    Verifies, with the 2.0 connection rules (a pipe connects on all four sides, a pipe-to-ground
    only on the side it faces plus underground to the nearest same-axis one facing back within
    reach, a pumpjack only through its output tile): no tile used twice, nothing on a blocked
    tile, every pumpjack's output tile holds a pipe that connects to it, everything is one
    network that contains `sink` (if given), and no pipe touches `foreign_pipes` (except at the
    sink) or sits on `foreign_ports` (fluids would mix)."""
    w, h = (pumpjack_size, pumpjack_size) if isinstance(pumpjack_size, (int, float)) else tuple(pumpjack_size)
    w, h = int(w), int(h)
    outputs = _outputs(output_offsets)
    blocked = {_tile(b) for b in blocked}
    reach = int(pipe_to_ground_reach or 0)
    problems, at = [], {}
    kinds = []
    for i, e in enumerate(entities):
        if e["name"] == pumpjack:
            kind = "jack"
            c = _xy(e)
            tiles = footprint(c[0], c[1], w, h)
        elif e["name"] == pipe_to_ground:
            kind = "ptg"
            tiles = {_tile(e)}
        elif e["name"] == pipe:
            kind = "pipe"
            tiles = {_tile(e)}
        else:
            problems.append(f"#{i} {e['name']}: not a pumpjack, pipe or pipe-to-ground of this layout")
            kinds.append(None)
            continue
        kinds.append(kind)
        for t in sorted(tiles):
            if t in at:
                problems.append(f"#{i} {e['name']} at {list(t)}: tile already used by #{at[t]}")
            else:
                at[t] = i
            if t in blocked:
                problems.append(f"#{i} {e['name']} at {list(t)} is on a blocked tile")

    def face(i):
        return _dir(entities[i].get("direction", 0))

    def links(i, t):
        """Entities connected to entity i (a pipe or ptg on tile t) above ground and underground."""
        out = []
        sides = (0, 4, 8, 12) if kinds[i] == "pipe" else (face(i),)
        for d in sides:
            n = _move(t, d)
            j = at.get(n)
            if j is None:
                continue
            if kinds[j] == "pipe" or (kinds[j] == "ptg" and face(j) == (d + 8) % 16):
                out.append(j)
            elif kinds[j] == "jack" and jport.get(j) == (t, n):
                out.append(j)
        if kinds[i] == "ptg" and reach:
            d = (face(i) + 8) % 16
            for k in range(1, reach + 1):
                j = at.get(_move(t, d, k))
                if j is not None and kinds[j] == "ptg" and face(j) % 8 == d % 8:
                    if face(j) == d:
                        out.append(j)
                    break
        return out

    jport = {}
    for i, e in enumerate(entities):
        if kinds[i] == "jack":
            c = _xy(e)
            d = _dir(e.get("direction", 0))
            if d not in outputs:
                problems.append(f"#{i} pumpjack at {list(c)}: no output offset for direction {d}")
                continue
            jport[i] = _port_of(c, d, outputs, w, h)
    adj = {i: set() for i in range(len(entities)) if kinds[i]}
    for i, e in enumerate(entities):
        if kinds[i] in ("pipe", "ptg"):
            for j in links(i, _tile(e)):
                adj[i].add(j)
                adj[j].add(i)
    for i, (o, body) in sorted(jport.items()):
        if not adj[i]:
            c = list(_xy(entities[i]))
            what = "is empty" if o not in at else "holds something that does not connect to it"
            problems.append(f"#{i} pumpjack at {c}: its output tile {list(o)} {what}")
    fluid = [i for i in adj if kinds[i]]
    if fluid:
        sink_t = _tile(sink) if sink is not None else None
        if sink_t is not None and (sink_t not in at or kinds[at[sink_t]] == "jack"):
            problems.append(f"sink tile {list(sink_t)} holds no pipe")
        start = at[sink_t] if sink_t is not None and sink_t in at else fluid[0]
        seen, todo = {start}, [start]
        while todo:
            i = todo.pop()
            for j in adj[i]:
                if j not in seen:
                    seen.add(j)
                    todo.append(j)
        for i in fluid:
            if i not in seen:
                problems.append(f"#{i} {entities[i]['name']} at {list(_xy(entities[i]))} is not connected to the network")
    fp = {_tile(t) for t in foreign_pipes}
    fports = {_tile(t) for t in foreign_ports}
    sink_t = _tile(sink) if sink is not None else None
    for i, e in enumerate(entities):
        if kinds[i] not in ("pipe", "ptg"):
            continue
        t = _tile(e)
        if t in fports:
            problems.append(f"#{i} {e['name']} at {list(t)} sits on a foreign machine's connection: fluids would mix")
        if t == sink_t:
            continue
        sides = (0, 4, 8, 12) if kinds[i] == "pipe" else (face(i),)
        for d in sides:
            if _move(t, d) in fp:
                problems.append(f"#{i} {e['name']} at {list(t)} touches a foreign pipe at {list(_move(t, d))}: fluids would mix")
    for i, (o, body) in sorted(jport.items()):
        if o in fp:
            problems.append(f"#{i} pumpjack outputs into a foreign pipe at {list(o)}")
    return problems
