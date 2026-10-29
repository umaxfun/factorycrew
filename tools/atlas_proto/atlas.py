"""Offline block detection + upstream trace over a full-surface dump (dump.lua)."""
import json, math, collections, sys

DIRV = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
BELT = {"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1"}
MACHINE = {"assembling-machine", "furnace", "rocket-silo", "mining-drill"}
RAIL = {"straight-rail", "curved-rail-a", "curved-rail-b", "half-diagonal-rail", "legacy-straight-rail",
        "legacy-curved-rail", "rail-ramp", "elevated-straight-rail", "elevated-curved-rail-a",
        "elevated-curved-rail-b", "elevated-half-diagonal-rail"}


def tile(x, y):
    return (math.floor(x), math.floor(y))


class World:
    def __init__(self, path):
        d = json.load(open(path))
        self.P = d["protos"]
        self.stops = d["stops"]
        self.trains = d["trains"]
        self.ents = []
        for i, r in enumerate(d["entities"]):
            name, x, y, dr = r[:4]
            ex = r[4] if len(r) > 4 else {}
            p = self.P[name]
            w, h = p["w"], p["h"]
            if dr in (4, 12):
                w, h = h, w
            self.ents.append(dict(id=i, name=name, type=p["type"], x=x, y=y, dir=dr, w=w, h=h, **ex))
        self.at = {}
        for e in self.ents:
            for t in self.tiles_of(e):
                self.at.setdefault(t, []).append(e["id"])

    def tiles_of(self, e):
        x0 = math.floor(e["x"] - e["w"] / 2 + 0.01)
        y0 = math.floor(e["y"] - e["h"] / 2 + 0.01)
        return [(x0 + i, y0 + j) for i in range(max(1, round(e["w"]))) for j in range(max(1, round(e["h"])))]

    def on(self, t, types=None):
        return [self.ents[i] for i in self.at.get(t, []) if types is None or self.ents[i]["type"] in types]

    def in_box(self, x0, y0, x1, y1):
        seen = {}
        for tx in range(math.floor(x0), math.ceil(x1)):
            for ty in range(math.floor(y0), math.ceil(y1)):
                for i in self.at.get((tx, ty), []):
                    seen[i] = self.ents[i]
        return list(seen.values())

    # ── belts ──
    def belt_next(self, e):
        """tiles this belt piece pushes items into"""
        dx, dy = DIRV[e["dir"]]
        t = e["type"]
        if t == "underground-belt" and e.get("io") == "input":
            return []  # handled by pairing
        if t == "splitter":
            px, py = -dy, dx
            return [tile(e["x"] + dx + px * 0.5, e["y"] + dy + py * 0.5),
                    tile(e["x"] + dx - px * 0.5, e["y"] + dy - py * 0.5)]
        if t in ("loader", "loader-1x1") and e.get("io") == "input":
            return []
        return [tile(e["x"] + dx * (e["h"] if dy else e["w"]) / 2 + dx * 0.5, e["y"] + dy * (e["h"] if dy else e["w"]) / 2 + dy * 0.5)]

    def ug_pair(self, e):
        """input underground -> its output"""
        dx, dy = DIRV[e["dir"]]
        tx, ty = tile(e["x"], e["y"])
        for k in range(1, 21):  # TODO: max_underground_distance from the prototype
            for o in self.on((tx + dx * k, ty + dy * k), {"underground-belt"}):
                if o["name"] == e["name"] and o["dir"] == e["dir"]:
                    if o.get("io") == "output":
                        return o
                    return None
        return None

    def downstream(self, e):
        out = []
        if e["type"] == "underground-belt" and e.get("io") == "input":
            o = self.ug_pair(e)
            return [o] if o else []
        for t in self.belt_next(e):
            for o in self.on(t, BELT):
                if o["id"] != e["id"]:
                    out.append(o)
        return out

    def build_belt_graph(self):
        self.down = collections.defaultdict(set)
        self.up = collections.defaultdict(set)
        for e in self.ents:
            if e["type"] in BELT:
                for o in self.downstream(e):
                    self.down[e["id"]].add(o["id"])
                    self.up[o["id"]].add(e["id"])

    # ── inserters ──
    def inserter_ends(self, e):
        pk = [self.ents[i] for i in self.at.get(tile(*e["pick"]), [])]
        dr = [self.ents[i] for i in self.at.get(tile(*e["drop"]), [])]
        return pk, dr


def machine_clusters(w, recipe, gap):
    """same-recipe machines whose footprints are within `gap` tiles of each other"""
    ms = [e for e in w.ents if e.get("recipe") == recipe and e["type"] in MACHINE]
    parent = list(range(len(ms)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i, a in enumerate(ms):
        for j in range(i + 1, len(ms)):
            b = ms[j]
            gx = abs(a["x"] - b["x"]) - (a["w"] + b["w"]) / 2
            gy = abs(a["y"] - b["y"]) - (a["h"] + b["h"]) / 2
            if max(gx, gy) <= gap:
                parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i, m in enumerate(ms):
        groups[find(i)].append(m)
    return sorted(groups.values(), key=len, reverse=True)


def block(w, box, gap=6):
    """seed box -> {selected machines, block machines, inserters, belts touching it, bbox}"""
    seed = [e for e in w.in_box(*box) if e["type"] in MACHINE and e.get("recipe")]
    if not seed:
        # seed only touched inserters/belts: take machines those inserters serve
        for e in w.in_box(*box):
            if e["type"] == "inserter":
                pk, dr = w.inserter_ends(e)
                seed += [m for m in pk + dr if m["type"] in MACHINE and m.get("recipe")]
    sel_ids = {m["id"] for m in seed}
    machines = {}
    for r in {m["recipe"] for m in seed}:
        for c in machine_clusters(w, r, gap):
            if sel_ids & {m["id"] for m in c}:
                for m in c:
                    machines[m["id"]] = m
    mid = set(machines)
    feed, out = [], []
    x0 = min(m["x"] - m["w"] / 2 for m in machines.values()); x1 = max(m["x"] + m["w"] / 2 for m in machines.values())
    y0 = min(m["y"] - m["h"] / 2 for m in machines.values()); y1 = max(m["y"] + m["h"] / 2 for m in machines.values())
    ins = [e for e in w.in_box(x0 - 3, y0 - 3, x1 + 3, y1 + 3) if e["type"] == "inserter"]
    for e in ins:
        pk, dr = w.inserter_ends(e)
        if any(m["id"] in mid for m in dr):
            feed.append((e, [p for p in pk if p["type"] in BELT | {"container", "logistic-container"}]))
        if any(m["id"] in mid for m in pk):
            out.append((e, [p for p in dr if p["type"] in BELT | {"container", "logistic-container"}]))
    return dict(selected=seed, machines=list(machines.values()), feed=feed, out=out, bbox=(x0, y0, x1, y1))


def trace_up(w, start_ids, limit=5000):
    """walk belts upstream from start belts; return sources: inserters/loaders putting items on them"""
    seen, stack = set(), list(start_ids)
    while stack and len(seen) < limit:
        b = stack.pop()
        if b in seen:
            continue
        seen.add(b)
        stack.extend(w.up.get(b, ()))
    return seen
