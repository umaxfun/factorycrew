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


def fake_install(tmp_path, monkeypatch, mcp_servers=True, version="0.11.0-rc.1"):
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
    monkeypatch.setattr(st, "probe_library", lambda: {"ok": True, "n": 0})
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


SHIPPED = json.loads((ROOT / "mod" / "kc-companion" / "info.json").read_text())["version"]
HEALTHY = {"ok": True, "game_online": True, "run_name": "Run 1", "tick": 100, "tabs": ["General"], "mod_version": SHIPPED,
           "last_seen_s": 0.2, "queued_asks": 0, "leased_asks": 0, "dispatcher_seen_s": 3.0, "agent_seen_s": 40.0}


def test_all_green(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch)
    srv = fake_bridge(HEALTHY)
    res = st.status({"http_port": srv.server_port}, mods, "mod installed")
    srv.shutdown()
    assert res["state"] == "ok", res
    assert all(c["state"] == "ok" for c in res["checks"]), res


def test_old_kirocrew_and_an_agent_without_tools_are_named(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch, mcp_servers=False, version="0.10.0")
    srv = fake_bridge(HEALTHY)
    c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
    srv.shutdown()
    assert c["kirocrew"]["state"] == "fail" and "0.11" in c["kirocrew"]["fix"]
    assert c["agent"]["state"] == "fail" and "no Factorio MCP server" in c["agent"]["detail"]


def test_bridge_down_marks_what_depends_on_it_unknown(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch)
    c = by_id(st.status({"http_port": 1}, mods, ""))
    assert c["bridge"]["state"] == "fail"
    assert c["game"]["state"] == "idle" and c["dispatcher"]["state"] == "idle"


def test_offline_game_and_a_silent_dispatcher(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch)
    srv = fake_bridge({**HEALTHY, "game_online": False, "last_seen_s": None, "dispatcher_seen_s": 600.0})
    res = st.status({"http_port": srv.server_port, "game_port": 34200}, mods, "")
    srv.shutdown()
    c = by_id(res)
    assert c["game"]["state"] == "warn" and "--enable-lua-udp 34200" in c["game"]["fix"]
    assert c["dispatcher"]["state"] == "fail" and "600 s" in c["dispatcher"]["detail"]
    assert res["state"] == "fail"


def test_stale_mod_in_the_mods_folder(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch)
    (mods / "kc-companion" / "info.json").write_text(json.dumps({"version": "0.0.1"}))
    srv = fake_bridge(HEALTHY)
    c = by_id(st.status({"http_port": srv.server_port}, mods, "left as is"))
    srv.shutdown()
    assert c["mod"]["state"] == "warn" and "0.0.1" in c["mod"]["detail"]


def test_a_game_still_running_the_old_mod_asks_for_a_restart(tmp_path, monkeypatch):
    # the update copied the new mod in, but Factorio loads mods only at start
    mods = fake_install(tmp_path, monkeypatch)
    for running, shown in (("0.0.1", "the game runs 0.0.1"), (None, "the game runs an older version")):
        srv = fake_bridge({**HEALTHY, "mod_version": running})
        c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
        srv.shutdown()
        assert c["mod"]["state"] == "warn" and c["mod"]["detail"].startswith(shown), c["mod"]
        assert c["mod"]["fix"] == "Restart Factorio to load the updated mod."
    srv = fake_bridge({**HEALTHY, "game_online": False, "mod_version": None})
    c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
    srv.shutdown()
    assert c["mod"]["state"] == "ok"  # nothing to say about a game that is not running


def test_mcp_probe_starts_the_real_server(monkeypatch):
    monkeypatch.setattr(st, "ROOT", ROOT)  # the repo has mcp/ at the root, like the built app
    st._mcp_cache.update(at=0.0, result=None)
    m = st.probe_mcp()
    assert "factorio_query" in m["tools"] and "factorio_reply" in m["tools"], m


# ── the mod follows the app: an app update replaces the mod it installed, never someone else's ──

sys.path.insert(0, str(ROOT / "bridge"))  # the built app has bridge/ beside backend/; the repo at its root
import server  # noqa: E402  (app/backend/server.py, on sys.path above)


def _app_with_mod(tmp_path, version):
    app = tmp_path / f"app-{version}"
    (app / "mod" / "kc-companion").mkdir(parents=True)
    (app / "mod" / "kc-companion" / "info.json").write_text(json.dumps({"name": "kc-companion", "version": version}))
    return app


def test_app_update_replaces_the_mod_it_installed(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.setattr(server, "ROOT", _app_with_mod(tmp_path, "0.3.2"))
    assert "installed" in server.install_mod(mods)
    assert "already installed" in server.install_mod(mods)
    monkeypatch.setattr(server, "ROOT", _app_with_mod(tmp_path, "0.3.3"))
    assert "0.3.3 installed" in server.install_mod(mods)
    assert json.loads((mods / "kc-companion" / "info.json").read_text())["version"] == "0.3.3"
    assert (mods / "kc-companion" / server.MANAGED_MARK).read_text() == "0.3.3"


def _factorio_started(data, version, how="log"):
    """What a Factorio start leaves in its data dir: factorio-current.log (every start) or
    player-data.json (written by the game, read when there is no log)."""
    if how == "log":
        (data / "factorio-current.log").write_text(
            f"   0.000 2026-11-07 14:19:01; Factorio {version} (build 87808, mac-arm64, steam, space-age)\n"
            "   0.001 Operating system: macOS 15\n")
    else:
        (data / "player-data.json").write_text(json.dumps({"last-played-version": {"game_version": version}}))


def _line(mods):
    return json.loads((mods / "kc-companion" / "info.json").read_text())["factorio_version"]


def test_the_mod_is_installed_for_the_factorio_the_player_runs(tmp_path, monkeypatch):
    # Factorio loads a mod only when its info.json names the game's own line: 2.0 or 2.1
    data = tmp_path / "factorio"
    mods = data / "mods"
    mods.mkdir(parents=True)
    monkeypatch.setattr(server, "ROOT", _app_with_mod(tmp_path, "0.4.3"))
    _factorio_started(data, "2.0.77", how="player-data")
    assert "for Factorio 2.0" in server.install_mod(mods) and _line(mods) == "2.0"
    assert "already installed" in server.install_mod(mods)
    # Steam updated the game: the log of its last start says 2.1, the mod follows
    _factorio_started(data, "2.1.24")
    note = server.install_mod(mods)
    assert _line(mods) == "2.1" and "reinstalled for Factorio 2.1" in note, note
    assert (mods / "kc-companion" / server.MANAGED_MARK).read_text() == "0.4.3"
    assert "already installed" in server.install_mod(mods)


def test_a_line_change_asks_for_a_restart_until_the_game_is_back(tmp_path, monkeypatch):
    # the backend's own loop: update_mod notices the reinstall, /api/status says restart, and the
    # prompt goes once the restarted game is online
    data = tmp_path / "factorio"
    mods = data / "mods"
    mods.mkdir(parents=True)
    fake_install(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "ROOT", _app_with_mod(tmp_path, SHIPPED))
    monkeypatch.setattr(server, "STATE", {"config": {}, "mods": mods, "mod_note": "", "bridge_error": None,
                                          "mod_switched": False})
    _factorio_started(data, "2.0.77")
    server.update_mod()
    assert server.STATE["mod_switched"] is False and _line(mods) == "2.0"
    _factorio_started(data, "2.1.24")
    server.update_mod()
    assert server.STATE["mod_switched"] is True and _line(mods) == "2.1", server.STATE
    server.update_mod()  # nothing new: the prompt stays
    assert server.STATE["mod_switched"] is True
    offline = fake_bridge({**HEALTHY, "game_online": False})
    server.STATE["config"] = {"http_port": offline.server_port}
    c = by_id(server.status_now())
    assert c["mod"]["state"] == "warn" and "Restart Factorio" in c["mod"]["fix"] and server.STATE["mod_switched"]
    offline.shutdown()
    # the old 2.0 game is still sending heartbeats: that is not the restarted game
    stale = fake_bridge({**HEALTHY, "factorio_version": "2.0.77"})
    server.STATE["config"] = {"http_port": stale.server_port}
    server.status_now()
    stale.shutdown()
    assert server.STATE["mod_switched"] is True
    online = fake_bridge({**HEALTHY, "factorio_version": "2.1.24"})
    server.STATE["config"] = {"http_port": online.server_port}
    server.status_now()
    online.shutdown()
    assert server.STATE["mod_switched"] is False


def test_a_game_never_started_gets_the_current_stable_line(tmp_path, monkeypatch):
    mods = tmp_path / "factorio" / "mods"
    mods.mkdir(parents=True)
    monkeypatch.setattr(server, "ROOT", _app_with_mod(tmp_path, "0.4.3"))
    server.install_mod(mods)
    assert _line(mods) == st.DEFAULT_LINE == "2.1"


def test_the_mod_check_follows_the_game_version(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch)
    data = mods.parent
    info = mods / "kc-companion" / "info.json"
    info.write_text(json.dumps({"version": SHIPPED, "factorio_version": "2.0"}))
    _factorio_started(data, "2.0.77")
    srv = fake_bridge({**HEALTHY, "factorio_version": "2.0.77"})
    c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
    assert c["mod"]["state"] == "ok" and "for Factorio 2.0" in c["mod"]["detail"], c["mod"]
    assert "Factorio 2.0.77" in c["game"]["detail"], c["game"]
    # the game went to 2.1 and the backend has not reinstalled yet
    _factorio_started(data, "2.1.24")
    c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
    assert c["mod"]["state"] == "warn" and "made for Factorio 2.0" in c["mod"]["detail"], c["mod"]
    # reinstalled for 2.1, the game has not come back yet: say what to do
    info.write_text(json.dumps({"version": SHIPPED, "factorio_version": "2.1"}))
    offline = fake_bridge({**HEALTHY, "game_online": False})
    c = by_id(st.status({"http_port": offline.server_port}, mods, "", mod_switched=True))
    assert c["mod"]["state"] == "warn" and "Restart Factorio" in c["mod"]["fix"] and "Mods" in c["mod"]["fix"], c["mod"]
    # a line the mod was not tested on loads, with a warning
    _factorio_started(data, "2.2.0")
    info.write_text(json.dumps({"version": SHIPPED, "factorio_version": "2.2"}))
    c = by_id(st.status({"http_port": srv.server_port}, mods, ""))
    assert c["mod"]["state"] == "warn" and "2.0 and 2.1" in c["mod"]["detail"], c["mod"]
    srv.shutdown()
    offline.shutdown()


def test_a_mod_folder_the_app_did_not_put_there_is_left_alone(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    (mods / "kc-companion").mkdir(parents=True)
    (mods / "kc-companion" / "info.json").write_text(json.dumps({"version": "9.9.9"}))
    monkeypatch.setattr(server, "ROOT", _app_with_mod(tmp_path, "0.3.3"))
    assert "not managed by the app" in server.install_mod(mods)
    assert json.loads((mods / "kc-companion" / "info.json").read_text())["version"] == "9.9.9"


def test_a_library_the_cli_cannot_reach_fails_the_page(tmp_path, monkeypatch):
    mods = fake_install(tmp_path, monkeypatch)
    monkeypatch.setattr(st, "probe_library", lambda: {"ok": False, "error": "gateway ownership not verified"})
    srv = fake_bridge(HEALTHY)
    res = st.status({"http_port": srv.server_port}, mods, "")
    srv.shutdown()
    c = by_id(res)["library"]
    assert res["state"] == "fail" and c["state"] == "fail" and "ownership not verified" in c["detail"], c


def test_a_bridge_that_lost_its_port_is_named_and_the_other_one_is_not_read(tmp_path, monkeypatch):
    # the app in a second Kiro Crew: the first one's bridge answers on the port, healthy
    mods = fake_install(tmp_path, monkeypatch)
    srv = fake_bridge(HEALTHY)
    res = st.status({"http_port": srv.server_port}, mods, "", "cannot open UDP :34199 / HTTP :34198: Address already in use")
    srv.shutdown()
    c = by_id(res)
    assert res["state"] == "fail" and c["bridge"]["state"] == "fail" and "already in use" in c["bridge"]["detail"]
    assert "another Kiro Crew" in c["bridge"]["fix"]
    assert c["game"]["state"] == "idle" and c["dispatcher"]["state"] == "idle"  # not the other bridge's game


def test_the_backend_survives_a_taken_bridge_port():
    import asyncio
    import socket
    sys.path.insert(0, str(ROOT / "app" / "backend"))
    import server
    tcp = socket.socket()
    tcp.bind(("127.0.0.1", 0))
    tcp.listen()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    try:
        s = server.fb.Settings(udp_port=udp.getsockname()[1], http_port=tcp.getsockname()[1], atlas_prefetch=False)
        asyncio.run(asyncio.wait_for(server.run_bridge(s), 5))  # returns instead of raising
    finally:
        tcp.close()
        udp.close()
    assert "cannot open UDP" in server.STATE["bridge_error"], server.STATE


# ── the error-report switch on the status page (docs/sentry.md) ──

def test_error_reports_are_on_by_default_and_the_switch_keeps_the_rest_of_the_config(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    assert server.settings() == {"ok": True, "telemetry": True}  # no config file yet
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text(json.dumps({"http_port": 40001}))
    assert server.save_settings({"telemetry": False}) == {"ok": True, "telemetry": False}
    assert json.loads((tmp_path / "data" / "config.json").read_text()) == {"http_port": 40001, "telemetry": False}
    assert server.save_settings({"telemetry": "no"})["ok"] is False
    assert server.settings()["telemetry"] is False


def test_the_switch_is_reachable_over_http(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(server, "ROOT", tmp_path)

    async def go():
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]

        async def call(raw):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(raw)
            await w.drain()
            data = await r.read()
            w.close()
            return json.loads(data.split(b"\r\n\r\n", 1)[1])

        body = b'{"telemetry": false}'
        posted = await call(b"POST /api/settings HTTP/1.1\r\nContent-Type: application/json\r\n"
                            b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        got = await call(b"GET /api/settings HTTP/1.1\r\n\r\n")
        srv.close()
        return posted, got

    posted, got = asyncio.run(go())
    assert posted == got == {"ok": True, "telemetry": False}
