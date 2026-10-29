"""The app's status page checks (app/backend/status.py), against a fake home and a fake bridge."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "backend"))
import status as st  # noqa: E402


def fake_bridge(health):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(health).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def setup(tmp_path, monkeypatch, mcp_servers=True, version="0.11.0-rc.1"):
    home = tmp_path / "home"
    agents = home / "kiro" / "agents"
    agents.mkdir(parents=True)
    agent = {"name": "factorio-companion"}
    if mcp_servers:
        agent["mcpServers"] = {"factorycrew:factorio": {"command": "python3"}}
    (agents / "factorycrew--factorio-companion.json").write_text(json.dumps(agent))
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    monkeypatch.setattr(st, "kirocrew_version", lambda: version)
    monkeypatch.setattr(st, "probe_mcp", lambda: {"tools": ["factorio_query", "factorio_reply"], "ms": 5, "stderr": ""})
    mods = tmp_path / "mods"
    (mods / "kc-companion").mkdir(parents=True)
    shipped = json.loads((ROOT / "mod" / "kc-companion" / "info.json").read_text())["version"]
    monkeypatch.setattr(st, "ROOT", tmp_path / "app")
    (tmp_path / "app" / "mod" / "kc-companion").mkdir(parents=True)
    (tmp_path / "app" / "mod" / "kc-companion" / "info.json").write_text(json.dumps({"version": shipped}))
    (mods / "kc-companion" / "info.json").write_text(json.dumps({"version": shipped}))
    return mods


def by_id(res):
    return {c["id"]: c for c in res["checks"]}


HEALTHY = {"ok": True, "game_online": True, "run_name": "Run 1", "tick": 100, "tabs": ["General"],
           "last_seen_s": 0.2, "queued_asks": 0, "leased_asks": 0, "dispatcher_seen_s": 3.0, "agent_seen_s": 40.0}


def test_all_green(tmp_path, monkeypatch):
    mods = setup(tmp_path, monkeypatch)
    srv = fake_bridge(HEALTHY)
    res = st.status({"http_port": srv.server_port}, mods, "mod installed")
    srv.shutdown()
    assert res["state"] == "ok", res
    assert all(c["state"] == "ok" for c in res["checks"]), res


def test_old_kirocrew_and_an_agent_without_tools_are_named(tmp_path, monkeypatch):
    mods = setup(tmp_path, monkeypatch, mcp_servers=False, version="0.10.0")
    srv = fake_bridge(HEALTHY)
    c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
    srv.shutdown()
    assert c["kirocrew"]["state"] == "fail" and "0.11" in c["kirocrew"]["fix"]
    assert c["agent"]["state"] == "fail" and "no Factorio MCP server" in c["agent"]["detail"]


def test_bridge_down_marks_what_depends_on_it_unknown(tmp_path, monkeypatch):
    mods = setup(tmp_path, monkeypatch)
    c = by_id(st.status({"http_port": 1}, mods, ""))
    assert c["bridge"]["state"] == "fail"
    assert c["game"]["state"] == "idle" and c["dispatcher"]["state"] == "idle"


def test_offline_game_and_a_silent_dispatcher(tmp_path, monkeypatch):
    mods = setup(tmp_path, monkeypatch)
    srv = fake_bridge({**HEALTHY, "game_online": False, "last_seen_s": None, "dispatcher_seen_s": 600.0})
    res = st.status({"http_port": srv.server_port, "game_port": 34200}, mods, "")
    srv.shutdown()
    c = by_id(res)
    assert c["game"]["state"] == "warn" and "--enable-lua-udp 34200" in c["game"]["fix"]
    assert c["dispatcher"]["state"] == "fail" and "600 s" in c["dispatcher"]["detail"]
    assert res["state"] == "fail"


def test_stale_mod_in_the_mods_folder(tmp_path, monkeypatch):
    mods = setup(tmp_path, monkeypatch)
    (mods / "kc-companion" / "info.json").write_text(json.dumps({"version": "0.0.1"}))
    srv = fake_bridge(HEALTHY)
    c = by_id(st.status({"http_port": srv.server_port}, mods, "left as is"))
    srv.shutdown()
    assert c["mod"]["state"] == "warn" and "0.0.1" in c["mod"]["detail"]


def test_mcp_probe_starts_the_real_server(monkeypatch):
    monkeypatch.setattr(st, "ROOT", ROOT)  # the repo has mcp/ at the root, like the built app
    st._mcp_cache.update(at=0.0, result=None)
    m = st.probe_mcp()
    assert "factorio_query" in m["tools"] and "factorio_reply" in m["tools"], m
