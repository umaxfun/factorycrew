"""Production graph of a base.

Nodes: blocks (same-recipe machines within GAP tiles, not across rails), train stations, logistic networks.
Edge A -> B carries item X when A makes X, B uses X, and there is a physical path: an inserter, a belt
(followed downstream), a plain chest in between, a pipe segment (pumps and valves followed by direction),
or robots in one logistic network. Chains = connected parts of the block graph without stations.
"""
import sys, json, math, re, time, collections
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlas import World, BELT, MACHINE, tile, DIRV

# FC_ATLAS_DIR: the headless game's write-data dir; the dump Lua writes into script-output/atlas/ there
OUT = os.path.join(os.environ["FC_ATLAS_DIR"], "script-output", "atlas") + "/"
GAP = 6
t0 = time.time()
w = World(OUT + "nauvis.json")
w.build_belt_graph()
meta = json.load(open(OUT + "meta.json"))
FL = json.load(open(OUT + "fluids.json"))
R = meta["recipes"]
WAGON = {"cargo-wagon", "fluid-wagon"}
CHEST = {"container", "logistic-container"}


def short(n):
    return re.sub(r"^nullius-", "", n)


# ── rail tiles, for cutting blocks at rails ──
rail = set()
for e in w.ents:
    if "rail" in e["type"]:
        rail.update(w.tiles_of(e))

# ── blocks ──
drill_target = {(d[0], d[1]): d[2] for d in meta["drills"]}
for e in w.ents:
    if e["type"] == "mining-drill":
        e["recipe"] = "mine:" + drill_target.get((e["x"], e["y"]), "-")


def products(rname):
    if rname.startswith("mine:"):
        return {rname[5:]}
    return {p[0] for p in R.get(rname, {"pro": []})["pro"]}


def ingredients(rname):
    if rname.startswith("mine:"):
        return set()
    return {p[0] for p in R.get(rname, {"ing": []})["ing"]}


def gap_has_rail(a, b):
    ax0, ay0, ax1, ay1 = a; bx0, by0, bx1, by1 = b
    gx0, gx1 = (ax1, bx0) if ax1 <= bx0 else (bx1, ax0) if bx1 <= ax0 else (max(ax0, bx0), min(ax1, bx1))
    gy0, gy1 = (ay1, by0) if ay1 <= by0 else (by1, ay0) if by1 <= ay0 else (max(ay0, by0), min(ay1, by1))
    return any((tx, ty) in rail for tx in range(math.floor(gx0), math.ceil(gx1)) for ty in range(math.floor(gy0), math.ceil(gy1)))


def fp(m):
    return (m["x"] - m["w"] / 2, m["y"] - m["h"] / 2, m["x"] + m["w"] / 2, m["y"] + m["h"] / 2)


by_recipe = collections.defaultdict(list)
for e in w.ents:
    if e["type"] in MACHINE and e.get("recipe"):
        by_recipe[e["recipe"]].append(e)
blocks, block_of = [], {}
for rname, ms in by_recipe.items():
    parent = {m["id"]: m["id"] for m in ms}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    cell = collections.defaultdict(list)
    for m in ms:
        cell[(int(m["x"] // 16), int(m["y"] // 16))].append(m)
    for (cx, cy), lst in cell.items():
        near = [o for dx in (-1, 0, 1) for dy in (-1, 0, 1) for o in cell.get((cx + dx, cy + dy), [])]
        for a in lst:
            A = fp(a)
            for b in near:
                if b["id"] <= a["id"] or find(a["id"]) == find(b["id"]):
                    continue
                B = fp(b)
                if max(B[0] - A[2], A[0] - B[2], B[1] - A[3], A[1] - B[3], 0) <= GAP and not gap_has_rail(A, B):
                    parent[find(a["id"])] = find(b["id"])
    g = collections.defaultdict(list)
    for m in ms:
        g[find(m["id"])].append(m)
    for c in g.values():
        bi = len(blocks)
        x0 = min(fp(m)[0] for m in c); y0 = min(fp(m)[1] for m in c)
        x1 = max(fp(m)[2] for m in c); y1 = max(fp(m)[3] for m in c)
        crafts = 0
        if rname in R:
            crafts = sum(m.get("speed") or 0 for m in c) / R[rname]["energy"]
        blocks.append(dict(id=bi, recipe=rname, machines=c, bbox=(x0, y0, x1, y1), crafts=crafts,
                           pro=products(rname), ing=ingredients(rname),
                           status=collections.Counter(m.get("status") or "?" for m in c)))
        for m in c:
            block_of[m["id"]] = bi
pos_block = {(m["x"], m["y"]): block_of[m["id"]] for b in blocks for m in b["machines"]}

# ── stations ──
stops = w.stops


def stop_items(s):
    return {"nullius-" + x if not x.startswith("nullius-") and False else x
            for x in re.findall(r"\[(?:item|fluid)=([^\]]+)\]", s["name"])}


def near_stop(x, y, r=18):
    best = None
    for si, s in enumerate(stops):
        d = math.hypot(x - s["x"], y - s["y"])
        if d <= r and (best is None or d < best[0]):
            best = (d, si)
    return best[1] if best else None


# ── logistic networks ──
logi_net = {(c[0], c[1]): c[2] for c in FL["logi"]}


def node_of(e):
    t = e["type"]
    if e["id"] in block_of:
        return ("B", block_of[e["id"]])
    if t == "logistic-container":
        n = logi_net.get((e["x"], e["y"]), -1)
        return ("L", n) if n >= 0 else ("C", e["id"])
    if t == "container":
        return ("C", e["id"])
    if t in WAGON or "rail" in t:
        s = near_stop(e["x"], e["y"])
        return ("S", s) if s is not None else None
    return None


# ── raw links: (src node, dst node) with a transport kind ──
raw = collections.defaultdict(set)  # (a, b) -> kinds
belt_sources = collections.defaultdict(set)
belt_sinks = collections.defaultdict(set)
for e in w.ents:
    if e["type"] != "inserter":
        continue
    pk, dr = w.inserter_ends(e)
    pn = {node_of(p) for p in pk} - {None}
    dn = {node_of(d) for d in dr} - {None}
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
        st.extend(w.down.get(x, ()))

# fluids: segment graph through pumps (directional), stations through pumps with an open (wagon) side
seg_prod = collections.defaultdict(set)  # seg -> (block, fluid)
seg_cons = collections.defaultdict(set)
for x, y, boxes in FL["machines"]:
    bi = pos_block.get((x, y))
    if bi is None:
        continue
    for bx in boxes:
        segs, ptype, fname = bx
        for seg in segs or []:
            if ptype in ("output", "input-output"):
                seg_prod[seg].add(("B", bi))
            if ptype in ("input", "input-output"):
                seg_cons[seg].add(("B", bi))
seg_next = collections.defaultdict(set)
for pr in FL["pumps"]:
    name, ptype, x, y, segs, targets, fname = (list(pr) + [None] * 7)[:7]
    targets = targets or []
    ins = [t for t, d in targets if d == "input"]
    outs = [t for t, d in targets if d == "output"]
    if ptype == "offshore-pump":
        for o in outs:
            if o >= 0:
                seg_prod[o].add(("W", short(fname or "water")))
        continue
    si = near_stop(x, y)
    for i in ins:
        for o in outs:
            if i >= 0 and o >= 0 and i != o:
                seg_next[i].add(o)
            elif i < 0 and o >= 0 and si is not None:
                seg_prod[o].add(("S", si))   # unloading a fluid wagon
            elif o < 0 and i >= 0 and si is not None:
                seg_cons[i].add(("S", si))   # loading a fluid wagon
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


# ── item labels: what can go along a raw link ──
def makes(n):
    k, i = n
    if k == "B":
        return blocks[i]["pro"]
    if k == "S":
        return stop_items(stops[i]) or None   # None = unknown, anything
    if k == "W":
        return {"nullius-" + i if not i.startswith("nullius") else i} | {i}
    return None


def needs(n):
    k, i = n
    if k == "B":
        return blocks[i]["ing"]
    if k == "S":
        return stop_items(stops[i]) or None
    return None


KIND = {}  # item/fluid name -> "item" | "fluid", from the recipes
for r in R.values():
    for n, _, t in list(r["ing"] or []) + list(r["pro"] or []):
        KIND[n] = t


def label(a, b, kinds):
    m, n = makes(a), needs(b)
    if m is None and n is None:
        return set()
    got = n if m is None else m if n is None else m & n
    # fluids only move through pipes, items only through belts/inserters/chests/robots
    keep = set()
    for it in got:
        k = KIND.get(it, "item")
        if (k == "fluid" and "pipe" in kinds) or (k == "item" and kinds - {"pipe"}):
            keep.add(it)
    return keep


# plain chests in between are contracted: X -> chest -> Y becomes X -> Y.
# logistic networks stay nodes: X -> L when X's inserters put products into a network chest, L -> Y when
# Y's inserters take from a network chest (a demand chest in practice).
out_raw = collections.defaultdict(list)
in_raw = collections.defaultdict(list)
for (a, b), kinds in raw.items():
    out_raw[a].append((b, kinds)); in_raw[b].append((a, kinds))
edges = collections.defaultdict(lambda: collections.defaultdict(set))  # (a, b) -> item -> kinds
for (a, b), kinds in raw.items():
    if a[0] == "C" or b[0] == "C" or (a[0] == "L" and b[0] == "L"):
        continue
    if b[0] == "L":
        for it in label(a, ("?", 0), kinds | {"robots"}):
            edges[(a, b)][it] |= kinds | {"robots"}
    elif a[0] == "L":
        for it in label(("?", 0), b, kinds | {"robots"}):
            edges[(a, b)][it] |= kinds | {"robots"}
    else:
        for it in label(a, b, kinds):
            edges[(a, b)][it] |= kinds
for mid in {n for n in list(out_raw) + list(in_raw) if n[0] == "C"}:
    for a, k1 in in_raw.get(mid, []):
        for b, k2 in out_raw.get(mid, []):
            if a == b or a[0] == "C" or b[0] == "C":
                continue
            ks = k1 | k2 | {"chest"}
            for it in label(a, b, ks):
                edges[(a, b)][it] |= ks
# what each network gets explicitly, what it hands out, and what it hands out with no producer in it
net_in = collections.defaultdict(lambda: collections.defaultdict(set))   # L -> item -> producers
net_out = collections.defaultdict(lambda: collections.defaultdict(set))  # L -> item -> consumers
for (a, b), items in edges.items():
    for it in items:
        if b[0] == "L":
            net_in[b][it].add(a)
        if a[0] == "L":
            net_out[a][it].add(b)
# a consumer only "needs" from the network what it does not already get physically
phys_in = collections.defaultdict(set)  # block -> items it gets by belt/inserter/pipe/chest
for (a, b), items in edges.items():
    if b[0] == "B" and a[0] != "L":
        for it in items:
            phys_in[b[1]].add(it)
for L, items in list(net_out.items()):
    for it in list(items):
        items[it] = {b for b in items[it] if it not in phys_in[b[1]]}
        if not items[it]:
            del items[it]
for (a, b), items in list(edges.items()):
    if a[0] == "L":
        for it in list(items):
            if it in phys_in[b[1]]:
                del items[it]
        if not items:
            del edges[(a, b)]


def feed_mode(bi):
    """robots: every ingredient that arrives comes from a logistic network; belts: none does;
    topped: belts carry most, robots bring some"""
    by_robot = {it for (a, b), items in edges.items() if b == ("B", bi) and a[0] == "L" for it in items}
    phys = phys_in[bi]
    if by_robot and not phys:
        return "robots"
    if by_robot:
        return "topped"
    return "belts"

t_build = time.time() - t0


# ── chains: connected parts of the block graph (stations and water cut chains) ──
adj = collections.defaultdict(set)
for (a, b), items in edges.items():
    # robots reach the whole network: like a station, they feed blocks but do not glue them into one chain
    if a[0] == "B" and b[0] == "B" and items:
        adj[a[1]].add(b[1]); adj[b[1]].add(a[1])
chain_of, chains = {}, []
for bi in range(len(blocks)):
    if bi in chain_of:
        continue
    comp, st = [], [bi]
    while st:
        x = st.pop()
        if x in chain_of:
            continue
        chain_of[x] = len(chains); comp.append(x)
        st.extend(adj[x])
    chains.append(comp)


def fmt(v):
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}".rstrip("0").rstrip(".")


def nname(n):
    k, i = n
    if k == "B":
        return f"B{i}"
    if k == "S":
        return f"S{i}"
    return f"{k}:{i}"


def stop_label(s):
    sig = re.findall(r"\[(?:virtual-signal|item|fluid)=([^\]]+)\]", s["name"])
    rest = re.sub(r"\[[^\]]*\]", "", s["name"]).strip()
    return " ".join([short(x).replace("signal-", "") for x in sig] + ([rest] if rest else [])) or s["name"]


def block_line(bi):
    b = blocks[bi]
    x0, y0, x1, y1 = b["bbox"]
    st = ", ".join(f"{k} {v}" for k, v in b["status"].most_common(2))
    inc = collections.defaultdict(list)
    for (a, c), items in edges.items():
        if c == ("B", bi):
            for it, kinds in items.items():
                inc[it].append(nname(a) + "(" + "/".join(sorted(kinds)) + ")")
    feeds = "; ".join(f"{short(it)} ← {' '.join(sorted(v))}" for it, v in sorted(inc.items()))
    missing = sorted(short(i) for i in b["ing"] if i not in inc)
    return (f"B{bi} {short(b['recipe'])} ×{len(b['machines'])} @({fmt((x0+x1)/2)},{fmt((y0+y1)/2)}) [{st}]"
            + (f" | {feeds}" if feeds else "") + (f" | no source found: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    nb = sum(1 for e in edges if e[0][0] == "B" and e[1][0] == "B")
    print(f"built in {t_build:.1f}s: {len(blocks)} blocks, {len(edges)} edges ({nb} block→block), "
          f"{len(chains)} chains ({sum(1 for c in chains if len(c) == 1)} lone blocks)")
    print("edge kinds:", collections.Counter(k for v in edges.values() for ks in v.values() for k in ks))
    json.dump(dict(blocks=[dict(id=b["id"], recipe=b["recipe"], n=len(b["machines"]), bbox=b["bbox"], chain=chain_of[b["id"]]) for b in blocks],
                   edges=[[nname(a), nname(b), {short(i): sorted(k) for i, k in it.items()}] for (a, b), it in edges.items()]),
              open(OUT + "graph.json", "w"))
