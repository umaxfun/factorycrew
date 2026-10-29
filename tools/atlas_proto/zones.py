"""Two levels: zones (recipe blocks standing together, cut at rails) are the graph nodes; edges between
zones carry items. Inside a zone: its recipe blocks and their internal links."""
import sys, collections, math
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph as G
B = G.blocks
ZGAP = 5

parent = list(range(len(B)))
def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]; a = parent[a]
    return a
cell = collections.defaultdict(list)
for b in B:
    x0, y0, x1, y1 = b["bbox"]
    for cx in range(int(x0 // 32), int(x1 // 32) + 1):
        for cy in range(int(y0 // 32), int(y1 // 32) + 1):
            cell[(cx, cy)].append(b["id"])
MODE = {b["id"]: G.feed_mode(b["id"]) for b in B}
CLASS = {i: ("robots" if m == "robots" else "phys") for i, m in MODE.items()}
print("feed modes:", collections.Counter(MODE.values()))
for ids in cell.values():
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if find(a) == find(b) or CLASS[a] != CLASS[b]:
                continue
            # nearest machine pair between the two blocks
            best = None
            for m in B[a]["machines"]:
                for n in B[b]["machines"]:
                    A, C = G.fp(m), G.fp(n)
                    g = max(C[0] - A[2], A[0] - C[2], C[1] - A[3], A[1] - C[3], 0)
                    if best is None or g < best[0]:
                        best = (g, A, C)
                    if g == 0:
                        break
            if best and best[0] <= ZGAP and not G.gap_has_rail(best[1], best[2]):
                parent[find(a)] = find(b)
zones = collections.defaultdict(list)
for b in B:
    zones[find(b["id"])].append(b["id"])
zones = sorted(zones.values(), key=lambda z: -sum(len(B[b]["machines"]) for b in z))
zone_of = {b: zi for zi, z in enumerate(zones) for b in z}


def zname(n):
    k, i = n
    return f"Z{zone_of[i]}" if k == "B" else G.nname(n)


zedges = collections.defaultdict(lambda: collections.defaultdict(set))  # (za, zb) -> item -> kinds
for (a, b), items in G.edges.items():
    za, zb = zname(a), zname(b)
    if za == zb:
        continue
    for it, ks in items.items():
        zedges[(za, zb)][it] |= ks
print(f"{len(zones)} zones ({sum(1 for z in zones if len(z) == 1)} with one block), {len(zedges)} zone edges")


def zone_line(zi, detail=False):
    z = zones[zi]
    ms = sum(len(B[b]["machines"]) for b in z)
    xs = [B[b]["bbox"][0] for b in z] + [B[b]["bbox"][2] for b in z]
    ys = [B[b]["bbox"][1] for b in z] + [B[b]["bbox"][3] for b in z]
    made = set().union(*(B[b]["pro"] for b in z)); used = set().union(*(B[b]["ing"] for b in z))
    out = sorted(G.short(x) for x in made - used)
    inn = collections.defaultdict(set)
    for (za, zb), items in zedges.items():
        if zb == f"Z{zi}":
            for it in items:
                inn[it].add(za)
    need = sorted(G.short(x) for x in used - made)
    st = collections.Counter()
    for b in z:
        st.update(B[b]["status"])
    rc = collections.Counter({G.short(B[b]["recipe"]): len(B[b]["machines"]) for b in z})
    fm = collections.Counter(MODE[b] for b in z)
    topped = sorted({G.short(it) for b in z if MODE[b] == "topped"
                     for (a, c), items in G.edges.items() if c == ("B", b) and a[0] == "L" for it in items})
    s = (f"Z{zi}: {len(z)} blocks, {ms} machines, ({min(xs):.0f},{min(ys):.0f})-({max(xs):.0f},{max(ys):.0f}) "
         f"[{', '.join(f'{k} {v}' for k, v in st.most_common(2))}] fed by: {dict(fm)}"
         + (f", robots top up: {', '.join(topped[:8])}" if topped else "") + "\n"
         f"   makes: {', '.join(out[:10])}{' …' if len(out) > 10 else ''}\n"
         f"   recipes: {', '.join(f'{k} {v}' for k, v in rc.most_common(8))}{' …' if len(rc) > 8 else ''}\n"
         f"   gets: " + "; ".join(f"{G.short(it)} ← {' '.join(sorted(inn[it]))}" for it in sorted(inn)
                                  if it in used and it not in made)[:700])
    nos = [x for x in need if not any(G.short(i) == x for i in inn)]
    if nos:
        s += f"\n   no source: {', '.join(nos[:12])}"
    if detail:
        for b in sorted(z, key=lambda b: -len(B[b]["machines"]))[:12]:
            s += "\n     " + G.block_line(b)[:260]
    return s


def zone_at(x, y):
    for b in B:
        x0, y0, x1, y1 = b["bbox"]
        if x0 <= x <= x1 and y0 <= y <= y1:
            return zone_of[b["id"]]


print("\n== logistic networks")
for L in sorted(set(G.net_in) | set(G.net_out), key=lambda L: -len(G.net_out.get(L, {}))):
    gi, go = G.net_in.get(L, {}), G.net_out.get(L, {})
    put_by = collections.Counter(zname(a) for v in gi.values() for a in v)
    fed = collections.Counter(zname(b) for v in go.values() for b in v)
    orphan = sorted(G.short(it) for it in go if it not in gi)
    print(f"{G.nname(L)}: gets {len(gi)} items explicitly from {len(put_by)} zones ({', '.join(f'{z}' for z, _ in put_by.most_common(6))}); "
          f"hands out {len(go)} items to {len(fed)} zones ({', '.join(f'{z}' for z, _ in fed.most_common(6))})")
    if orphan:
        print(f"   handed out but nobody puts them in (player/trash/other surface?): {', '.join(orphan[:15])}{' …' if len(orphan) > 15 else ''} ({len(orphan)})")

print("\nsizes (machines) of the top zones:", [sum(len(B[b]["machines"]) for b in z) for z in zones[:15]])
for title, (x, y) in [("tetrachloride", (-357.5, -122.5)), ("silicon pile", (120.5, 44)), ("glass in the pile", (111, 45)),
                      ("robomall", (226, -25.5)), ("mall", (214, -5))]:
    zi = zone_at(x, y)
    print(f"\n== {title}: ", end="")
    print(zone_line(zi, detail=title in ("silicon pile",)))
