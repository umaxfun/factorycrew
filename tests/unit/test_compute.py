"""factorio_compute without a game: data in, result out, sandbox, timeout (bridge faked)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import factorio_mcp as m  # noqa: E402


@pytest.fixture
def bridge(monkeypatch):
    calls = []

    def fake(path, body, timeout=30.0):
        calls.append((path, body))
        if path == "/query":
            return {"ok": True, "data": [{"x": 1, "y": 2}, {"x": 40, "y": 2}, {"x": 2, "y": 3}]}
        return {"ok": True}
    monkeypatch.setattr(m, "bridge_post", fake)
    return calls


def test_data_from_the_game_and_result_back(bridge):
    r = m.call_tool("factorio_compute", {
        "data_lua": "return wells",
        "python": "near = [p for p in data if abs(p['x'] - 1) < 5]\nresult = {'n': len(near)}\nprint('hi')",
    })
    assert r["ok"], r
    assert r["result"] == {"n": 2} and r["stdout"].strip() == "hi"
    assert bridge[0][0] == "/query" and bridge[0][1]["_agent"] is True
    assert any(p == "/alive" for p, _ in bridge)  # the window learns that Kira is computing


def test_python_error_is_reported(bridge):
    r = m.call_tool("factorio_compute", {"python": "1/0"})
    assert not r["ok"] and r["error"] == "python_error" and "ZeroDivisionError" in r["stderr"]


def test_timeout(bridge):
    r = m.call_tool("factorio_compute", {"python": "while True: pass", "timeout": 2})
    assert not r["ok"] and r["error"] == "timeout" and r["seconds"] < 10


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt sandbox is macOS only")
def test_sandbox_blocks_network_home_and_writes(bridge, tmp_path):
    secret = Path.home() / ".kc-compute-probe"
    secret.write_text("secret")
    try:
        r = m.call_tool("factorio_compute", {"python": f'''
import urllib.request, pathlib
out = {{}}
try:
    urllib.request.urlopen("https://example.com", timeout=5); out["net"] = "open"
except Exception as e:
    out["net"] = "blocked"
try:
    out["home_read"] = open({str(secret)!r}).read()
except Exception:
    out["home_read"] = "blocked"
try:
    open({str(tmp_path / "w.txt")!r}, "w").write("x"); out["write_outside"] = "open"
except Exception:
    out["write_outside"] = "blocked"
open("inside.txt", "w").write("ok"); out["write_inside"] = "ok"
result = out
'''})
    finally:
        secret.unlink()
    assert r["ok"] and r["sandboxed"], r
    assert r["result"] == {"net": "blocked", "home_read": "blocked", "write_outside": "blocked",
                           "write_inside": "ok"}, r["result"]


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt sandbox is macOS only")
def test_runs_inside_an_outer_sandbox(bridge):
    """Under Kiro Crew the server is already sandboxed and macOS refuses a nested profile:
    the code must still run (without our own sandbox) instead of failing every call."""
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); import factorio_mcp as m; "
            "m.bridge_post = lambda *a, **k: {'ok': True}; "
            "r = m.call_tool('factorio_compute', {'python': 'result = 6 * 7'}); "
            "print(r['ok'], r['result'], r['sandboxed'])") % str(ROOT / "mcp")
    p = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=60)
    assert p.stdout.split() == ["True", "42", "False"], (p.stdout, p.stderr[-500:])


def test_kc_helpers_route_around_bodies_and_catch_a_broken_network(bridge):
    """The layout bug seen live: a feeder row through another pumpjack's body is cut off."""
    r = m.call_tool("factorio_compute", {"python": '''
jack = kc.footprint(298.5, 309.5, 3, 3)
straight = [(x + 0.5, 310.5) for x in range(294, 310) if (x + 0.5, 310.5) not in jack]
broken = kc.unreached(straight, (294.5, 310.5), [(309.5, 310.5)])
path = kc.astar((309.5, 310.5), (294.5, 310.5), jack)
ok = kc.unreached(path, (294.5, 310.5), [(309.5, 310.5)])
result = {"jack": len(jack), "broken": broken, "path_ok": ok, "avoids": not (set(path) & jack), "len": len(path)}
'''})
    assert r["ok"], r
    res = r["result"]
    assert res["jack"] == 9 and res["broken"] == [[309.5, 310.5]] and res["path_ok"] == [] and res["avoids"], res


def test_kc_unwired_uses_the_shorter_reach(bridge):
    r = m.call_tool("factorio_compute", {"python": '''
grid = (258, 300, 32)
result = {
    "split": kc.unwired([(287, 300, 32), (301, 294, 18), (302, 312, 18)], grid),
    "ok": kc.unwired([(287, 300, 32), (301, 294, 18), (301, 311, 18)], grid),
}
'''})
    assert r["ok"], r
    assert r["result"]["split"] == [[302, 312, 18]] and r["result"]["ok"] == [], r["result"]


def test_kc_helpers_take_dicts_too(bridge):
    """Kira passed pipes as {name, x, y} dicts and got nonsense; every helper takes any point form."""
    r = m.call_tool("factorio_compute", {"python": '''
pipes = [{"name": "pipe", "x": 1.5 + i, "y": 0.5} for i in range(4)]
result = {
    "unreached": kc.unreached(pipes, {"x": 1.5, "y": 0.5}, [{"x": 4.5, "y": 0.5}, (9.5, 0.5)]),
    "path": len(kc.astar({"position": {"x": 0.5, "y": 0.5}}, [3.5, 0.5], [{"x": 2.5, "y": 0.5}])),
    "unwired": kc.unwired([{"x": 10, "y": 0, "reach": 18}], {"x": 0, "y": 0, "reach": 32}),
}
'''})
    assert r["ok"], r
    assert r["result"] == {"unreached": [[9.5, 0.5]], "path": 6, "unwired": []}, r["result"]


def test_kc_connect_joins_every_output_around_bodies(bridge):
    r = m.call_tool("factorio_compute", {"python": '''
wells = [(300.5, 300.5), (305.5, 296.5), (310.5, 301.5), (304.5, 305.5), (298.5, 309.5), (311.5, 309.5), (306.5, 313.5)]
blocked = set()
for w in wells:
    blocked |= kc.footprint(w[0], w[1], 3, 3)
outs = [(w[0] - 2, w[1] + 1) for w in wells]   # west-facing pumpjack outputs
sink = (294.5, 306.5)
net = kc.connect(outs, sink, blocked)
result = {"failed": net["failed"], "unreached": kc.unreached(net["tiles"], sink, outs),
          "overlap": len({tuple(t) for t in net["tiles"]} & blocked), "n": len(net["tiles"])}
'''})
    assert r["ok"], r
    res = r["result"]
    assert res["failed"] == [] and res["unreached"] == [] and res["overlap"] == 0 and res["n"] < 80, res


def test_skill_code_is_importable_in_compute(bridge):
    r = m.call_tool("factorio_compute", {"python": '''
import belts
r = belts.route((0.5, 0.5), (5.5, 0.5))
result = {"ok": r["ok"], "n": len(r["entities"]), "check": belts.check(r["entities"])}
'''})
    assert r["ok"], r
    assert r["result"] == {"ok": True, "n": 6, "check": []}, r["result"]
