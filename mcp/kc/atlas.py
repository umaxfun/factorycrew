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
CHEST = {"container", "logistic-container", "infinity-container"}
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

    def loader_side(self, e):
        """the container a loader serves: behind it when it loads a belt (output), ahead when it
        empties one (input)"""
        dx, dy = DIRV.get(e["dir"], (0, 0))
        half = (e["h"] if dy else e["w"]) / 2 + 0.5
        sign = 1 if e.get("io") == "input" else -1
        return [self.ents[i] for i in self.at.get(_tile(e["x"] + sign * dx * half, e["y"] + sign * dy * half), ())
                if self.ents[i]["id"] != e["id"]]

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
        for e in self.ents:  # loaders: a container (or machine) straight onto a belt, or off one
            if e["type"] not in ("loader", "loader-1x1"):
                continue
            for c in self.loader_side(e):
                n = self.node_of(c)
                if n is None:
                    continue
                if e.get("io") == "input":
                    belt_sinks[e["id"]].add(n)
                else:
                    belt_sources[n].add(e["id"])
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

    def known_items(self):
        if not hasattr(self, "_known"):
            k = set(self.kind)
            for b in self.blocks:
                k |= b["pro"] | b["ing"]
            for si in range(len(self.stops)):
                k |= self.stop_items(si)
            self._known = k
        return self._known

    def resolve_item(self, name):
        """the base's own item name for what a player or a model calls it: "glass" is
        "nullius-glass" in Nullius. Returns (name or None, candidates when it is ambiguous)."""
        if not name:
            return None, []
        known = self.known_items()
        if name in known:
            return name, []
        low = name.lower().strip().replace(" ", "-")
        exact = sorted(n for n in known if short(n) == low or n.split("-", 1)[-1] == low)
        if len(exact) == 1:
            return exact[0], []
        if exact:
            return None, exact
        part = sorted(n for n in known if low in n)
        if len(part) == 1:
            return part[0], []
        return None, part[:20]

    def stop_info(self, si):
        s = self.stops[si]
        return {"id": f"S{si}", "name": s.get("s", ""), "x": s["x"], "y": s["y"], "limit": s.get("lim", "")}

    def sources(self, node, item):
        return {self.name(a): sorted(ks) for a, ks in self.inbound[node].get(item, {}).items()}

    # ── belt lanes: how much of an item a belt can bring ──

    def _source_items(self, n, seen=None):
        """items a node can put on a belt; a plain chest passes on what fills it"""
        if n[0] == "C" and self.ents[n[1]].get("inf"):
            return set(self.ents[n[1]]["inf"].split(";"))
        if n[0] == "C":
            seen = seen or set()
            if n in seen:
                return set()
            seen.add(n)
            out = set()
            for (a, b) in self.raw:
                if b == n:
                    m = self._source_items(a, seen)
                    if m is None:
                        return None
                    out |= m
            return out
        if n[0] == "N":
            return None
        return self.makes(n)

    def _lanes(self):
        """item lanes on every belt piece, propagated downstream from where items enter.

        Factorio rules used: an inserter drops on the lane far from it (from along the belt: the
        right lane); a belt entering another belt from the side, where that belt also has an input
        from behind or from the other side, puts both its lanes onto the near lane only (side-load);
        otherwise lanes keep their side. state: piece -> item -> [lanes set, slowest belt speed,
        where the item was narrowed to fewer lanes]."""
        if hasattr(self, "_lane_state"):
            return self._lane_state
        state = collections.defaultdict(dict)
        work = []
        item_kind = self.kind

        def left_of(d):
            dx, dy = DIRV.get(d, (0, -1))
            return (dy, -dx)

        def side(of, at, d):
            lx, ly = left_of(d)
            v = (of[0] - at["x"]) * lx + (of[1] - at["y"]) * ly
            return "L" if v > 0.05 else "R" if v < -0.05 else None

        def add(pid, item, lanes, speed, narrowed):
            cur = state[pid].get(item)
            if cur is None:
                state[pid][item] = [set(lanes), speed, narrowed]
                work.append(pid)
                return
            changed = False
            if not set(lanes) <= cur[0]:
                cur[0] |= set(lanes); changed = True
            if speed < cur[1]:
                cur[1] = speed; changed = True
            if narrowed and not cur[2]:
                cur[2] = narrowed; changed = True
            if changed:
                work.append(pid)

        for e in self.ents:
            if e["type"] != "inserter" or "drop" not in e:
                continue
            pk, dr = self.ends(e)
            belts = [d for d in dr if d["type"] in BELT]
            if not belts:
                continue
            items = set()
            for p in pk:
                n = self.node_of(p)
                if n is None:
                    continue
                m = self._source_items(n)
                if m:
                    items |= {i for i in m if item_kind.get(i, "item") == "item"}
            for b in belts:
                sd = side((e["x"], e["y"]), b, b["dir"])
                lane = {"L": "R", "R": "L"}.get(sd, "R")  # the far lane
                for it in items:
                    add(b["id"], it, {lane}, self.belt_speed.get(b["name"], 0), None)
        for e in self.ents:
            if e["type"] in ("loader", "loader-1x1") and e.get("io") != "input":
                items = set()
                for c in self.loader_side(e):
                    n = self.node_of(c)
                    m = self._source_items(n) if n else None
                    if m:
                        items |= {i for i in m if item_kind.get(i, "item") == "item"}
                for it in items:
                    add(e["id"], it, {"L", "R"}, self.belt_speed.get(e["name"], 0), None)
        while work:
            u = work.pop()
            ue = self.ents[u]
            for v in self.down.get(u, ()):
                ve = self.ents[v]
                speed_v = self.belt_speed.get(ve["name"], 0)
                sideload_lane = None
                if ve["type"] == "transport-belt" and ue["dir"] != ve["dir"] and (ue["dir"] - ve["dir"]) % 8 == 4:
                    ups = [self.ents[w] for w in self.up.get(v, ())]
                    behind = any(w["dir"] == ve["dir"] for w in ups)
                    sides = {side((w["x"], w["y"]), ve, ve["dir"]) for w in ups if w["dir"] != ve["dir"]}
                    if behind or len(sides) > 1:
                        sideload_lane = side((ue["x"], ue["y"]), ve, ve["dir"])
                for it, (lanes, speed, narrowed) in list(state[u].items()):
                    if sideload_lane:
                        add(v, it, {sideload_lane}, min(speed, speed_v), narrowed or [ve["x"], ve["y"]])
                    else:
                        add(v, it, lanes, min(speed, speed_v), narrowed)
        self._lane_state = state
        return state

    def belt_supply(self, bi):
        """for each ingredient a block takes from belts: lanes it has there, what that can carry per
        second, which other items share those lanes, and where it was squeezed onto one lane"""
        state = self._lanes()
        ids = {m["id"] for m in self.blocks[bi]["machines"]}
        picks = collections.defaultdict(set)
        for e in self.ents:
            if e["type"] != "inserter" or "pick" not in e:
                continue
            pk, dr = self.ends(e)
            if any(d["id"] in ids for d in dr):
                for p in pk:
                    if p["type"] in BELT:
                        picks[p["id"]].add(e["id"])
        out = {}
        for it in self.blocks[bi]["ing"]:
            if self.kind.get(it) == "fluid":
                continue
            lanes, speeds, narrowed, shared = set(), [], None, set()
            belts = set()
            for pid in picks:
                st = state.get(pid, {}).get(it)
                if not st:
                    continue
                lanes |= {(pid, l) for l in st[0]}
                speeds.append(st[1])
                narrowed = narrowed or st[2]
                belts.add(self.ents[pid]["name"])
                for other, ost in state[pid].items():
                    if other != it and ost[0] & st[0]:
                        shared.add(other)
            if not speeds:
                continue
            # distinct physical belts the block picks from: lanes per belt, at the slowest speed seen
            per_belt = collections.Counter(pid for pid, _ in lanes)
            trunks = self._trunks(set(per_belt))
            lane_count = sum(max(len({l for p, l in lanes if p in t}), 0) for t in trunks)
            sp = min(speeds)
            out[it] = {"lanes": lane_count, "belts": len(trunks), "per_s": round(lane_count * sp * 480 / 2, 2),
                       "belt": sorted(belts), "shared_with": sorted(shared),
                       "one_lane_from": narrowed}
        return out

    def _trunks(self, pieces):
        """group belt pieces into lines connected through the belt graph: columns split off one trunk
        are one line, and a line carries at most two lanes of an item whatever it splits into"""
        left, groups = set(pieces), []
        while left:
            start = left.pop()
            seen, st = {start}, [start]
            while st and len(seen) < 20000:
                x = st.pop()
                for y in list(self.down.get(x, ())) + list(self.up.get(x, ())):
                    if y not in seen:
                        seen.add(y)
                        st.append(y)
            groups.append(seen & pieces)
            left -= seen
        return groups

    # ── views: what factorio_atlas returns ──

    def _src(self, a, kinds):
        """one source/sink of an item, in the words the answer needs"""
        out = {"from": self.zname(a), "by": sorted(kinds)}
        if a[0] == "B":
            b = self.blocks[a[1]]
            out.update(block=f"B{a[1]}", recipe=short(b["recipe"]), at=_center(b["bbox"]))
        elif a[0] == "S":
            s = self.stops[a[1]]
            out.update(stop=s.get("s", ""), at=[s["x"], s["y"]])
        return out

    def machines_of(self, blocks):
        return [m for bi in blocks for m in self.blocks[bi]["machines"]]

    def network_inputs(self):
        """network id -> items machines put into it explicitly"""
        if not hasattr(self, "_net_in"):
            out = collections.defaultdict(set)
            for (a, b), items in self.edges.items():
                if b[0] == "N":
                    out[b[1]].update(items)
            self._net_in = out
        return self._net_in

    # ── map: the whole base, one line per zone (or stop / train / network) ──

    def _near(self, box, near):
        if not near:
            return True
        x, y, r = near["x"], near["y"], near.get("r", 100)
        dx = max(box[0] - x, 0, x - box[2])
        dy = max(box[1] - y, 0, y - box[3])
        return math.hypot(dx, dy) <= r

    def map_view(self, kind="zone", item=None, status=None, feed=None, near=None, min_machines=1, limit=60,
                 live=None):
        """`live`: machine entity id -> live status. `status`: "stuck" (anything but working/full
        output) or a status name; rows then sort by how many machines have it."""
        rows = []
        if kind == "station":
            for si, s in enumerate(self.stops):
                items = sorted(self.stop_items(si))
                if item and item not in items:
                    continue
                if not self._near((s["x"], s["y"], s["x"], s["y"]), near):
                    continue
                trains = [t["id"] for t in self.trains_at(si)]
                rows.append(f"S{si} [{_stop_label(s.get('s', ''))}] at ({_n(s['x'])},{_n(s['y'])}) limit "
                            f"{s.get('lim', '')} trains {trains or 'none'}")
        elif kind == "train":
            for t in self.trains:
                if item and not any(item in st for st in t["stations"]):
                    continue
                rows.append(f"T{t['id']} {t['wagons']}+{t['fluid_wagons']}f wagons: "
                            + " -> ".join(_stop_label(st) for st in t["stations"]))
        elif kind == "network":
            gi = collections.defaultdict(set)
            go = collections.defaultdict(set)
            for (a, b), items in self.edges.items():
                if b[0] == "N":
                    gi[b].update(items)
                if a[0] == "N":
                    go[a].update(items)
            for n in sorted(set(gi) | set(go)):
                if item and item not in gi[n] | go[n]:
                    continue
                orphans = sorted(short(i) for i in go[n] - gi[n])
                rows.append(f"N{n[1]}: {len(gi[n])} items put in by machines, {len(go[n])} handed out"
                            + (f"; handed out but nothing puts them in: {', '.join(orphans)}" if orphans else ""))
        else:
            scored = []
            for z in self.zones:
                bl = [self.blocks[b] for b in z["blocks"]]
                ms = [m for b in bl for m in b["machines"]]
                if len(ms) < min_machines or not self._near(z["bbox"], near):
                    continue
                if item and not any(item in b["pro"] or item in b["ing"] for b in bl):
                    continue
                fm = collections.Counter(b["feed"] for b in bl)
                if feed and not fm.get(feed):
                    continue
                st = collections.Counter(live.get(m["id"], "?") for m in ms) if live is not None else None
                stuck = None
                if st is not None:
                    bad = {k: v for k, v in st.items() if k not in OK_STATUS and k not in ("?", "gone")}
                    if bad:
                        reason, _ = max(bad.items(), key=lambda kv: kv[1])
                        stuck = (sum(bad.values()), reason)
                score = 0
                if status:
                    if st is None:
                        continue
                    score = stuck[0] if status == "stuck" and stuck else st.get(status, 0)
                    if not score:
                        continue
                per = collections.Counter()
                for b in bl:
                    per[b["recipe"]] += len(b["machines"])
                tops = [r for r, _ in per.most_common()]
                if item:  # the recipes touching the item first
                    touch = {b["recipe"] for b in bl if item in b["pro"] or item in b["ing"]}
                    tops = [r for r in tops if r in touch] + [r for r in tops if r not in touch]
                recipes = ", ".join(f"{short(r)} {per[r]}" for r in tops[:3])
                box = z["bbox"]
                line = (f"Z{z['id']} ({_n(box[0])},{_n(box[1])})-({_n(box[2])},{_n(box[3])}) {len(ms)}m "
                        f"{fm.most_common(1)[0][0]}: {recipes}{' …' if len(tops) > 3 else ''}")
                if item:
                    roles = ["makes" if any(item in b["pro"] for b in bl) else "",
                             "uses" if any(item in b["ing"] for b in bl) else ""]
                    line += f" | {' & '.join(r for r in roles if r)} {short(item)}"
                if stuck:
                    line += f" | stuck {stuck[0]}: {stuck[1]}"
                scored.append((-score, z["id"], line))
            scored.sort()
            rows = [s[2] for s in scored]
        out = {"kind": kind, "rows": rows[:limit], "total": len(rows)}
        if len(rows) > limit:
            out["more"] = len(rows) - limit
        return out

    # ── trace: one item up to where it is made, or down to where it is used ──

    def _hop(self, n, item, dir):
        k, i = n
        if k == "B":
            b = self.blocks[i]
            r = self.recipes.get(b["recipe"])
            hop = {"node": f"B{i}", "zone": f"Z{self.zone_of[i]}", "recipe": b["recipe"],
                   "machines": len(b["machines"]), "at": _center(b["bbox"])}
            if r:
                for nm, amt, _ in r["pro"]:
                    if nm == item:
                        hop["make_per_s"] = round(amt * b["crafts"], 3)
                for nm, amt, _ in r["ing"]:
                    if nm == item:
                        hop["need_per_s"] = round(amt * b["crafts"], 3)
            return hop
        if k == "S":
            s = self.stops[i]
            return {"node": f"S{i}", "stop": s.get("s", ""), "at": [s["x"], s["y"]], "limit": s.get("lim", "")}
        return {"node": self.name(n)}

    def trains_at(self, si):
        name = self.stops[si].get("s", "")
        return [t for t in self.trains if name in t["stations"]]

    def stops_named(self, name):
        return [si for si, s in enumerate(self.stops) if s.get("s", "") == name]

    def trace(self, start, item, dir="up", depth=6, width=5):
        """the item's way from `start` (a node) up to where it is made or down to where it is used,
        through blocks, train stops (and the trains whose schedules join them) and logistic networks.
        Returns a tree: each hop has "next" hops; a stop's hops go through "trains"."""
        seen = set()
        flows = self.inbound if dir == "up" else self.outbound

        def walk(n, d):
            hop = self._hop(n, item, dir)
            if d >= depth or n in seen:
                if n in seen:
                    hop["seen"] = True
                return hop
            seen.add(n)
            # a producer (up) or consumer (down) of the item other than the start ends the way here
            if n != start and n[0] == "B":
                b = self.blocks[n[1]]
                if (dir == "up" and item in b["pro"]) or (dir == "down" and item in b["ing"]):
                    return hop
            nxt = []
            if n[0] == "S" and not flows[n].get(item):
                # nothing physical: the item rides trains to/from the other stops in their schedules
                trains = []
                for t in self.trains_at(n[1])[:width]:
                    th = {"train": t["id"], "wagons": t["wagons"], "fluid_wagons": t["fluid_wagons"],
                          "schedule": t["stations"], "next": []}
                    for st_name in t["stations"]:
                        if st_name == self.stops[n[1]].get("s"):
                            continue
                        for sj in self.stops_named(st_name):
                            if item in self.stop_items(sj) or not self.stop_items(sj):
                                th["next"].append(walk(("S", sj), d + 1))
                    trains.append(th)
                hop["trains"] = trains
                return hop
            for a in list(flows[n].get(item, {}))[:width]:
                h = walk(a, d + 1)
                h["by"] = sorted(flows[n][item][a])
                nxt.append(h)
            if nxt:
                hop["next"] = nxt
            return hop

        return walk(start, 0)

    def block_view(self, bi, status=None):
        """one block: machines, rates at full speed, where every ingredient comes from and where
        every product goes. `status` maps machine entity id -> live status name."""
        b = self.blocks[bi]
        node = ("B", bi)
        r = self.recipes.get(b["recipe"])
        crafts = b["crafts"]
        view = {"block": f"B{bi}", "zone": f"Z{self.zone_of[bi]}", "recipe": b["recipe"],
                "machines": len(b["machines"]), "machine": sorted({m["name"] for m in b["machines"]}),
                "bbox": _box(b["bbox"]), "feed": b["feed"]}
        if status is not None:
            view["status"] = dict(collections.Counter(status.get(m["id"], "?") for m in b["machines"]).most_common())
        if r:
            view["crafts_per_s"] = round(crafts, 3)
            view["need_per_s"] = {n: round(a * crafts, 3) for n, a, _ in r["ing"]}
            view["make_per_s"] = {n: round(a * crafts, 3) for n, a, _ in r["pro"]}
        view["inputs"] = {it: [self._src(a, ks) for a, ks in self.inbound[node].get(it, {}).items()]
                          for it in sorted(b["ing"])}
        supply = self.belt_supply(bi)
        if supply:
            need = view.get("need_per_s", {})
            for it, s in supply.items():
                if it in need:
                    s["need_per_s"] = need[it]
                    s["enough"] = s["per_s"] >= need[it] - 1e-6
            view["belt_supply"] = supply
        view["no_source"] = sorted(it for it in b["ing"] if not self.inbound[node].get(it))
        view["outputs"] = {it: [self._src(c, ks) for c, ks in self.outbound[node].get(it, {}).items()]
                           for it in sorted(b["pro"]) if self.outbound[node].get(it)}
        return view

    def zone_view(self, zi, status=None, item=None, selected=None):
        """a zone: its blocks, and for every ingredient it does not make itself, where it comes from.
        `item` keeps only blocks making or using it; `selected` (block ids) marks what a box covered."""
        z = self.zones[zi]
        blocks = z["blocks"]
        made = set().union(*(self.blocks[b]["pro"] for b in blocks))
        used = set().union(*(self.blocks[b]["ing"] for b in blocks))
        zn = f"Z{zi}"
        inputs = collections.defaultdict(dict)
        for b in blocks:
            for it, srcs in self.inbound[("B", b)].items():
                for a, ks in srcs.items():
                    if self.zname(a) != zn:
                        cur = inputs[it].setdefault(self.zname(a), self._src(a, ks))
                        cur["by"] = sorted(set(cur["by"]) | ks)
        rows = []
        for b in sorted(blocks, key=lambda b: -len(self.blocks[b]["machines"])):
            bl = self.blocks[b]
            if item and item not in bl["pro"] and item not in bl["ing"]:
                continue
            row = {"block": f"B{b}", "recipe": short(bl["recipe"]), "machines": len(bl["machines"]),
                   "at": _center(bl["bbox"]), "feed": bl["feed"]}
            if status is not None:
                st = collections.Counter(status.get(m["id"], "?") for m in bl["machines"])
                row["status"] = dict(st.most_common())
            if selected is not None and b in selected:
                row["selected"] = True
            rows.append(row)
        view = {"zone": zn, "bbox": _box(z["bbox"]), "blocks": len(blocks),
                "machines": sum(len(self.blocks[b]["machines"]) for b in blocks),
                "feed": dict(collections.Counter(self.blocks[b]["feed"] for b in blocks)),
                "makes": sorted(made - used), "block_list": rows,
                "inputs": {it: list(v.values()) for it, v in sorted(inputs.items())
                           if it in used and (item is None or it == item)},
                "no_source": sorted(it for it in used - made if it not in inputs)}
        # what the zone takes from a logistic network that no machine puts into that network: it lives
        # on what is stored there (or what the player drops in) and stops when that runs out
        put = self.network_inputs()
        gaps = sorted({it for it, srcs in view["inputs"].items() for s in srcs
                       if s["from"].startswith("N") and it not in put.get(int(s["from"][1:]), set())})
        if gaps:
            view["from_network_but_nothing_puts_it_in"] = gaps
        robots = sorted({it for it, srcs in view["inputs"].items() for s in srcs if s["from"].startswith("N")})
        if robots and view["feed"].get("topped"):
            view["robots_top_up"] = robots
        if status is not None:
            st = collections.Counter(status.get(m["id"], "?") for b in blocks for m in self.blocks[b]["machines"])
            view["status"] = dict(st.most_common())
        return view


def _n(v):
    return f"{v:.0f}"


def _stop_label(name):
    """[virtual-signal=signal-red][item=nullius-rutile] -> red rutile"""
    sig = re.findall(r"\[(?:virtual-signal|item|fluid)=([^\]]+)\]", name)
    rest = re.sub(r"\[[^\]]*\]", "", name).strip()
    return " ".join([short(x).replace("signal-", "") for x in sig] + ([rest] if rest else [])) or name


def _center(b):
    return [round((b[0] + b[2]) / 2, 1), round((b[1] + b[3]) / 2, 1)]


def _box(b):
    return [round(v, 1) for v in b]
