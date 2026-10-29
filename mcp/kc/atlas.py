"""Atlas of a base: blocks, zones and what feeds what, from the mod's structure dump.

The dump format is documented in mod/kc-companion/scripts/atlas.lua. Words (docs/atlas.md):
  block   machines with one recipe within BLOCK_GAP tiles of each other, never across rails
  zone    blocks within ZONE_GAP tiles of each other, not across rails, fed the same way
          (a block fed only by robots never joins one fed by belts); zones are the graph's nodes
  edge    A feeds B with item X: A makes X, B uses X, and a physical path joins them (inserter,
          belt followed downstream, plain chest between, pipe segment through pumps, or a
          logistic network). Items move by belts/inserters/chests/robots, fluids only by pipes.
  network a logistic network is a node: it gets an item when an inserter puts a product into
          one of its chests and feeds a block that takes from its chests and does not already
          get that item physically
  feed    "robots" (every ingredient from a network), "belts" (none), "topped" (some)

Nodes are named B<n> (block), Z<n> (zone), S<n> (train stop), N<id> (logistic network),
W:<fluid> (offshore pump). Numbers are stable for one dump only.
Stdlib only, Python 3.9+.
"""
from __future__ import annotations

import collections
import math
import re

BLOCK_GAP = 6
ZONE_GAP = 5
STOP_RADIUS = 18
DIRV = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
BELT = {"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "linked-belt"}
MACHINE = {"assembling-machine", "furnace", "rocket-silo", "mining-drill"}
CHEST = {"container", "logistic-container"}
OK_STATUS = {"working", "full_output"}


def short(name: str) -> str:
    """Display name: the big overhaul mods' prefix off (nullius-graphite -> graphite)."""
    return re.sub(r"^(nullius|bob|angels|kr|se|pyhightech|py)-", "", name or "")


def _tile(x: float, y: float) -> tuple:
    return (math.floor(x), math.floor(y))


# ── parsing ──────────────────────────────────────────────────────────────────


def parse(text: str) -> dict:
    """Dump text -> {surface, tick, names, entities, recipes, trains, footer}."""
    names, ents, recipes, trains = {}, [], {}, []
    head, foot = {}, {}
    for line in text.split("\n"):
        if not line:
            continue
        f = line.split("|")
        k = f[0]
        if k == "E":
            nid = int(f[1])
            nm, typ, w, h, _ = names[nid]
            d = int(f[4])
            if d in (4, 12):
                w, h = h, w
            e = {"id": len(ents), "name": nm, "type": typ, "x": int(f[2]) / 2, "y": int(f[3]) / 2,
                 "dir": d, "w": w, "h": h}
            for kv in f[5:]:
                key, _, val = kv.partition("=")
                e[key] = val
            if "p" in e:
                a, b = e.pop("p").split(","); e["pick"] = (int(a) / 2, int(b) / 2)
            if "d" in e:
                a, b = e.pop("d").split(","); e["drop"] = (int(a) / 2, int(b) / 2)
            if "sp" in e:
                e["sp"] = float(e["sp"])
            if "L" in e:
                e["L"] = int(e["L"])
            if "f" in e:
                boxes = []
                for bx in e.pop("f").split(";"):
                    segs, ptype, fluid = (bx.split(":") + ["-", "-"])[:3]
                    boxes.append((sorted({int(s) for s in segs.split("+") if s}), ptype, fluid))
                e["fluidboxes"] = boxes
            for side in ("pi", "po"):
                if side in e:
                    e[side] = [int(s) for s in e[side].split("+") if s]
            ents.append(e)
        elif k == "N":
            extra = dict(kv.partition("=")[::2] for kv in f[6:])
            names[int(f[1])] = (f[2], f[3], int(f[4]), int(f[5]), extra)
        elif k == "R":
            def amounts(s):
                out = []
                for part in s.split(";") if s else []:
                    n, a, t = part.split(":")
                    out.append((n, float(a), t))
                return out
            recipes[f[1]] = {"energy": float(f[2]), "ing": amounts(f[3]), "pro": amounts(f[4])}
        elif k == "T":
            trains.append({"id": int(f[1]), "wagons": int(f[2]), "fluid_wagons": int(f[3]),
                           "stations": [s for s in f[4].split(";") if s] if len(f) > 4 else []})
        elif k == "H":
            head = {"version": int(f[1]), "surface": f[2], "tick": int(f[3]), "budget": int(f[4])}
        elif k == "Z":
            foot = {"entities": int(f[1]), "chunks": int(f[2]), "ticks": int(f[3])}
    if foot and foot["entities"] != len(ents):
        raise ValueError(f"dump incomplete: footer says {foot['entities']} entities, got {len(ents)}")
    return {"surface": head.get("surface"), "tick": head.get("tick"), "names": names, "entities": ents,
            "recipes": recipes, "trains": trains, "footer": foot}


# ── the atlas ────────────────────────────────────────────────────────────────


class Atlas:
    def __init__(self, text: str):
        d = parse(text)
        self.surface, self.tick = d["surface"], d["tick"]
        self.ents, self.recipes, self.trains = d["entities"], d["recipes"], d["trains"]
        self.ug = {nm: int(ex.get("ug", 10)) for nm, typ, _, _, ex in d["names"].values() if typ == "underground-belt"}
        self.belt_speed = {nm: float(ex["bs"]) for nm, _, _, _, ex in d["names"].values() if "bs" in ex}
        self.kind = {}
        for r in self.recipes.values():
            for n, _, t in r["ing"] + r["pro"]:
                self.kind[n] = t
        self._index()
        self._belts()
        self._blocks()
        self._stops()
        self._links()
        self._edges()
        self._zones()

    # geometry
    def fp(self, e):
        return (e["x"] - e["w"] / 2, e["y"] - e["h"] / 2, e["x"] + e["w"] / 2, e["y"] + e["h"] / 2)

    def tiles_of(self, e):
        x0, y0 = math.floor(e["x"] - e["w"] / 2 + 0.01), math.floor(e["y"] - e["h"] / 2 + 0.01)
        return [(x0 + i, y0 + j) for i in range(max(1, e["w"])) for j in range(max(1, e["h"]))]

    def _index(self):
        self.at = collections.defaultdict(list)
        self.rail = set()
        for e in self.ents:
            for t in self.tiles_of(e):
                self.at[t].append(e["id"])
            if "rail" in e["type"]:
                self.rail.update(self.tiles_of(e))

    def on(self, t, types=None):
        return [self.ents[i] for i in self.at.get(t, ()) if types is None or self.ents[i]["type"] in types]

    def in_box(self, x0, y0, x1, y1):
        seen = {}
        for tx in range(math.floor(x0), math.ceil(x1)):
            for ty in range(math.floor(y0), math.ceil(y1)):
                for i in self.at.get((tx, ty), ()):
                    seen[i] = self.ents[i]
        return list(seen.values())

    def gap_has_rail(self, a, b):
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        gx0, gx1 = (ax1, bx0) if ax1 <= bx0 else (bx1, ax0) if bx1 <= ax0 else (max(ax0, bx0), min(ax1, bx1))
        gy0, gy1 = (ay1, by0) if ay1 <= by0 else (by1, ay0) if by1 <= ay0 else (max(ay0, by0), min(ay1, by1))
        return any((tx, ty) in self.rail for tx in range(math.floor(gx0), math.ceil(gx1))
                   for ty in range(math.floor(gy0), math.ceil(gy1)))

    @staticmethod
    def gap(a, b):
        return max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3], 0)

    # belts: which piece pushes into which
    def _belt_next(self, e):
        dx, dy = DIRV.get(e["dir"], (0, 0))
        t = e["type"]
        if t == "underground-belt" and e.get("io") == "input":
            tx, ty = _tile(e["x"], e["y"])
            for k in range(1, self.ug.get(e["name"], 10) + 1):
                for o in self.on((tx + dx * k, ty + dy * k), {"underground-belt"}):
                    if o["name"] == e["name"] and o["dir"] == e["dir"]:
                        return [o["id"]] if o.get("io") == "output" else []
            return []
        if t in ("loader", "loader-1x1") and e.get("io") == "input":
            return []
        if t == "splitter":
            px, py = -dy, dx
            tiles = [_tile(e["x"] + dx + px * 0.5, e["y"] + dy + py * 0.5),
                     _tile(e["x"] + dx - px * 0.5, e["y"] + dy - py * 0.5)]
        else:
            half = (e["h"] if dy else e["w"]) / 2
            tiles = [_tile(e["x"] + dx * half + dx * 0.5, e["y"] + dy * half + dy * 0.5)]
        return [o["id"] for t2 in tiles for o in self.on(t2, BELT) if o["id"] != e["id"]]

    def _belts(self):
        self.down = collections.defaultdict(set)
        self.up = collections.defaultdict(set)
        for e in self.ents:
            if e["type"] in BELT:
                for o in self._belt_next(e):
                    self.down[e["id"]].add(o)
                    self.up[o].add(e["id"])

    # blocks
    def products(self, rname):
        if rname.startswith("mine:"):
            return {rname[5:]}
        return {p[0] for p in self.recipes.get(rname, {"pro": []})["pro"]}

    def ingredients(self, rname):
        if rname.startswith("mine:"):
            return set()
        return {p[0] for p in self.recipes.get(rname, {"ing": []})["ing"]}

    def _cluster(self, items, boxes_of, gap, same):
        """union-find over items whose footprints are within `gap` and not across rails"""
        parent = list(range(len(items)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        cell = collections.defaultdict(list)
        for i, it in enumerate(items):
            for (x0, y0, x1, y1) in boxes_of(it):
                # grown by the gap, so two footprints within `gap` always share a cell
                for cx in range(int((x0 - gap) // 32), int((x1 + gap) // 32) + 1):
                    for cy in range(int((y0 - gap) // 32), int((y1 + gap) // 32) + 1):
                        cell[(cx, cy)].append(i)
        for ids in cell.values():
            ids = sorted(set(ids))
            for n, a in enumerate(ids):
                for b in ids[n + 1:]:
                    if find(a) == find(b) or not same(items[a], items[b]):
                        continue
                    best = None
                    for A in boxes_of(items[a]):
                        for B in boxes_of(items[b]):
                            g = self.gap(A, B)
                            if best is None or g < best[0]:
                                best = (g, A, B)
                    if best and best[0] <= gap and not self.gap_has_rail(best[1], best[2]):
                        parent[find(a)] = find(b)
        groups = collections.defaultdict(list)
        for i in range(len(items)):
            groups[find(i)].append(items[i])
        return list(groups.values())

    def _blocks(self):
        by_recipe = collections.defaultdict(list)
        for e in self.ents:
            if e["type"] in MACHINE:
                r = e.get("r") or (("mine:" + e["t"]) if e.get("t") else None)
                if r:
                    e["recipe"] = r
                    by_recipe[r].append(e)
        self.blocks, self.block_of = [], {}
        for rname in sorted(by_recipe):
            for c in self._cluster(by_recipe[rname], lambda m: [self.fp(m)], BLOCK_GAP, lambda a, b: True):
                bi = len(self.blocks)
                xs = [self.fp(m) for m in c]
                r = self.recipes.get(rname)
                crafts = sum(m.get("sp") or 0 for m in c) / r["energy"] if r and r["energy"] else 0
                self.blocks.append({"id": bi, "recipe": rname, "machines": c, "crafts": crafts,
                                    "bbox": (min(b[0] for b in xs), min(b[1] for b in xs),
                                             max(b[2] for b in xs), max(b[3] for b in xs)),
                                    "pro": self.products(rname), "ing": self.ingredients(rname)})
                for m in c:
                    self.block_of[m["id"]] = bi

    # stations
    def _stops(self):
        self.stops = [e for e in self.ents if e["type"] == "train-stop"]
        self.stop_index = {e["id"]: i for i, e in enumerate(self.stops)}

    def near_stop(self, x, y):
        best = None
        for si, s in enumerate(self.stops):
            d = math.hypot(x - s["x"], y - s["y"])
            if d <= STOP_RADIUS and (best is None or d < best[0]):
                best = (d, si)
        return best[1] if best else None

    def stop_items(self, si):
        return set(re.findall(r"\[(?:item|fluid)=([^\]]+)\]", self.stops[si].get("s", "")))

    def node_of(self, e):
        t = e["type"]
        if e["id"] in self.block_of:
            return ("B", self.block_of[e["id"]])
        if t == "logistic-container" and e.get("L", -1) >= 0:
            return ("N", e["L"])
        if t in CHEST:
            return ("C", e["id"])
        if "rail" in t:
            si = self.near_stop(e["x"], e["y"])
            return ("S", si) if si is not None else None
        return None

    def ends(self, ins):
        pk = [self.ents[i] for i in self.at.get(_tile(*ins["pick"]), ())]
        dr = [self.ents[i] for i in self.at.get(_tile(*ins["drop"]), ())]
        return pk, dr

    def _links(self):
        """raw physical links (node -> node: set of kinds), before any item check"""
        raw = collections.defaultdict(set)
        belt_sources = collections.defaultdict(set)
        belt_sinks = collections.defaultdict(set)
        for e in self.ents:
            if e["type"] != "inserter" or "pick" not in e:
                continue
            pk, dr = self.ends(e)
            pn = {self.node_of(p) for p in pk} - {None}
            dn = {self.node_of(d) for d in dr} - {None}
            for a in pn:
                for b in dn:
                    if a != b:
                        raw[(a, b)].add("inserter")
                for d in dr:
                    if d["type"] in BELT:
                        belt_sources[a].add(d["id"])
            for b in dn:
                for p in pk:
                    if p["type"] in BELT:
                        belt_sinks[p["id"]].add(b)
        for a, starts in belt_sources.items():
            seen, st = set(), list(starts)
            while st:
                x = st.pop()
                if x in seen:
                    continue
                seen.add(x)
                for b in belt_sinks.get(x, ()):
                    if b != a:
                        raw[(a, b)].add("belt")
                st.extend(self.down.get(x, ()))
        # fluids: segments joined through pumps by direction; a pump side with no segment next to a
        # stop is a wagon
        seg_prod = collections.defaultdict(set)
        seg_cons = collections.defaultdict(set)
        for e in self.ents:
            bi = self.block_of.get(e["id"])
            if bi is None:
                continue
            for segs, ptype, _ in e.get("fluidboxes", ()):
                for s in segs:
                    if ptype in ("output", "input-output"):
                        seg_prod[s].add(("B", bi))
                    if ptype in ("input", "input-output"):
                        seg_cons[s].add(("B", bi))
        seg_next = collections.defaultdict(set)
        for e in self.ents:
            if e["type"] == "offshore-pump":
                for o in e.get("po", ()):
                    if o >= 0:
                        seg_prod[o].add(("W", e.get("fl", "water")))
            elif e["type"] == "pump":
                si = self.near_stop(e["x"], e["y"])
                for i in e.get("pi", ()):
                    for o in e.get("po", ()):
                        if i >= 0 and o >= 0 and i != o:
                            seg_next[i].add(o)
                        elif i < 0 <= o and si is not None:
                            seg_prod[o].add(("S", si))
                        elif o < 0 <= i and si is not None:
                            seg_cons[i].add(("S", si))
        for seg, prods in seg_prod.items():
            seen, st = set(), [seg]
            while st:
                s = st.pop()
                if s in seen:
                    continue
                seen.add(s)
                for b in seg_cons.get(s, ()):
                    for a in prods:
                        if a != b:
                            raw[(a, b)].add("pipe")
                st.extend(seg_next.get(s, ()))
        self.raw = raw

    # items on links
    def makes(self, n):
        k, i = n
        if k == "B":
            return self.blocks[i]["pro"]
        if k == "S":
            return self.stop_items(i) or None
        if k == "W":
            return {i}
        return None

    def needs(self, n):
        k, i = n
        if k == "B":
            return self.blocks[i]["ing"]
        if k == "S":
            return self.stop_items(i) or None
        return None

    def label(self, a, b, kinds):
        m, n = self.makes(a), self.needs(b)
        if m is None and n is None:
            return set()
        got = n if m is None else m if n is None else m & n
        keep = set()
        for it in got:
            kind = self.kind.get(it, "item")
            if (kind == "fluid" and "pipe" in kinds) or (kind == "item" and kinds - {"pipe"}):
                keep.add(it)
        return keep

    def _edges(self):
        raw = self.raw
        out_raw, in_raw = collections.defaultdict(list), collections.defaultdict(list)
        for (a, b), kinds in raw.items():
            out_raw[a].append((b, kinds))
            in_raw[b].append((a, kinds))
        edges = collections.defaultdict(lambda: collections.defaultdict(set))
        none = ("?", 0)
        for (a, b), kinds in raw.items():
            if a[0] == "C" or b[0] == "C" or (a[0] == "N" and b[0] == "N"):
                continue
            if b[0] == "N":
                for it in self.label(a, none, kinds | {"robots"}):
                    edges[(a, b)][it] |= kinds | {"robots"}
            elif a[0] == "N":
                for it in self.label(none, b, kinds | {"robots"}):
                    edges[(a, b)][it] |= kinds | {"robots"}
            else:
                for it in self.label(a, b, kinds):
                    edges[(a, b)][it] |= kinds
        for mid in {n for n in list(out_raw) + list(in_raw) if n[0] == "C"}:
            for a, k1 in in_raw.get(mid, []):
                for b, k2 in out_raw.get(mid, []):
                    if a == b or a[0] == "C" or b[0] == "C":
                        continue
                    ks = k1 | k2 | {"chest"}
                    for it in self.label(a, b, ks):
                        edges[(a, b)][it] |= ks
        # a block gets from a network only what it does not get physically
        phys = collections.defaultdict(set)
        for (a, b), items in edges.items():
            if b[0] == "B" and a[0] != "N":
                phys[b[1]].update(items)
        for (a, b), items in list(edges.items()):
            if a[0] == "N" and b[0] == "B":
                for it in list(items):
                    if it in phys[b[1]]:
                        del items[it]
                if not items:
                    del edges[(a, b)]
        self.edges = edges
        self.phys_in = phys
        self.inbound = collections.defaultdict(lambda: collections.defaultdict(dict))  # b -> item -> a -> kinds
        self.outbound = collections.defaultdict(lambda: collections.defaultdict(dict))
        for (a, b), items in edges.items():
            for it, ks in items.items():
                self.inbound[b][it][a] = ks
                self.outbound[a][it][b] = ks

    def feed_mode(self, bi):
        by_robot = any(a[0] == "N" for it in self.inbound[("B", bi)].values() for a in it)
        if by_robot and not self.phys_in[bi]:
            return "robots"
        return "topped" if by_robot else "belts"

    # zones
    def _zones(self):
        for b in self.blocks:
            b["feed"] = self.feed_mode(b["id"])
        cls = lambda b: "robots" if b["feed"] == "robots" else "phys"
        groups = self._cluster(self.blocks, lambda b: [self.fp(m) for m in b["machines"]], ZONE_GAP,
                               lambda a, b: cls(a) == cls(b))
        groups.sort(key=lambda g: -sum(len(b["machines"]) for b in g))
        self.zones, self.zone_of = [], {}
        for zi, g in enumerate(groups):
            bx = [b["bbox"] for b in g]
            self.zones.append({"id": zi, "blocks": [b["id"] for b in g],
                               "bbox": (min(b[0] for b in bx), min(b[1] for b in bx),
                                        max(b[2] for b in bx), max(b[3] for b in bx))})
            for b in g:
                self.zone_of[b["id"]] = zi

    # ── lookups ──

    def name(self, n):
        k, i = n
        if k == "B":
            return f"B{i}"
        if k == "S":
            return f"S{i}"
        if k == "N":
            return f"N{i}"
        if k == "W":
            return f"W:{short(i)}"
        return f"{k}{i}"

    def zname(self, n):
        return f"Z{self.zone_of[n[1]]}" if n[0] == "B" else self.name(n)

    def block_at(self, x, y):
        for i in self.at.get(_tile(x, y), ()):
            if i in self.block_of:
                return self.block_of[i]
        best = None
        for b in self.blocks:  # nearest block footprint within 3 tiles: a click next to a machine
            for m in b["machines"]:
                g = self.gap(self.fp(m), (x, y, x, y))
                if g <= 3 and (best is None or g < best[0]):
                    best = (g, b["id"])
        return best[1] if best else None

    def blocks_in_box(self, x0, y0, x1, y1):
        out = []
        for e in self.in_box(x0, y0, x1, y1):
            if e["id"] in self.block_of:
                out.append(self.block_of[e["id"]])
            elif e["type"] == "inserter" and "pick" in e:
                pk, dr = self.ends(e)
                out += [self.block_of[m["id"]] for m in pk + dr if m["id"] in self.block_of]
        return sorted(set(out))

    def zone_at(self, x, y):
        b = self.block_at(x, y)
        return None if b is None else self.zone_of[b]

    def stop_info(self, si):
        s = self.stops[si]
        return {"id": f"S{si}", "name": s.get("s", ""), "x": s["x"], "y": s["y"], "limit": s.get("lim", "")}

    def sources(self, node, item):
        return {self.name(a): sorted(ks) for a, ks in self.inbound[node].get(item, {}).items()}
