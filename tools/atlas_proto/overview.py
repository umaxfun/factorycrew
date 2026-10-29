"""Overview level: one short line per zone (ignoring lone blocks under 3 machines), measured."""
import sys, collections, io, contextlib
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stdout(io.StringIO()):
    import zones as Z
G, B = Z.G, Z.B
lines = []
for zi, z in enumerate(Z.zones):
    ms = sum(len(B[b]["machines"]) for b in z)
    if ms < 3:
        continue
    xs = [B[b]["bbox"][0] for b in z] + [B[b]["bbox"][2] for b in z]
    ys = [B[b]["bbox"][1] for b in z] + [B[b]["bbox"][3] for b in z]
    made = set().union(*(B[b]["pro"] for b in z)); used = set().union(*(B[b]["ing"] for b in z))
    top = [G.short(B[b]["recipe"]) for b in sorted(z, key=lambda b: -len(B[b]["machines"]))][:3]
    st = collections.Counter()
    for b in z:
        st.update(B[b]["status"])
    bad = sum(v for k, v in st.items() if k not in ("working", "full_output"))
    mode = collections.Counter(Z.MODE[b] for b in z).most_common(1)[0][0]
    lines.append(f"Z{zi} ({min(xs):.0f},{min(ys):.0f})-({max(xs):.0f},{max(ys):.0f}) {ms}m {mode}: {', '.join(top)}"
                 + (f" | stuck {bad}: {st.most_common(1)[0][0]}" if bad else ""))
text = "\n".join(lines)
print(f"{len(lines)} zones with 3+ machines, {len(text)} chars, ~{len(text) // 4} tokens")
print("\n".join(lines[:12]))
