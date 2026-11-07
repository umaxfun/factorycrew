"""Level 2 harness: headless Factorio 2.1.24 + bridge + MCP server, all inside one container.

UDP between the mod and the bridge is loopback-only, so everything shares the container's
network namespace. Test-only RCON is used to act as the player (remote.call("kc", "ask", ...))
and to read the mod's state; the code under test never uses RCON.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("FC_ROOT", "/work"))
DATA = Path(os.environ.get("FC_INT_DATA", ROOT / ".test-data" / "integration"))
FACTORIO = os.environ.get("FACTORIO_BIN", "/opt/factorio/bin/x64/factorio")
HTTP = "http://127.0.0.1:34198"
RCON_PORT, RCON_PASS = 27015, "l2-test"

# Short timeouts so the failure paths finish in seconds. Game speed is raised to 4 in setup,
# which makes the mod's 5 s heartbeat ~1.25 s and its 15 s pong timeout ~3.75 s of wall time.
BRIDGE_ARGS = ["--offline-after", "3", "--idle-after", "2", "--request-timeout", "5"]
GAME_SPEED = 4


def wait_until(pred, timeout=20.0, step=0.1, what="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = pred()
            if last:
                return last
        except Exception as e:  # process still starting, socket refused, ...
            last = e
        time.sleep(step)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; last={last!r}")


def http(method, path, body=None, timeout=70):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(HTTP + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def lua_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # plain \" \\ \n escapes are valid Lua


class Rcon:
    def __init__(self):
        self.sock = None
        self.n = 0

    def _pkt(self, kind, body):
        self.n += 1
        b = body.encode() + b"\x00\x00"
        self.sock.sendall(struct.pack("<iii", len(b) + 8, self.n, kind) + b)
        return self.n

    def _read(self):
        def exact(k):
            buf = b""
            while len(buf) < k:
                chunk = self.sock.recv(k - len(buf))
                if not chunk:
                    raise ConnectionError("rcon closed")
                buf += chunk
            return buf
        size, rid, kind = struct.unpack("<iii", exact(12))
        return rid, kind, exact(size - 8)[:-2].decode("utf-8", errors="replace")

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", RCON_PORT), timeout=10)
        rid = self._pkt(3, RCON_PASS)
        while True:
            got, kind, _ = self._read()
            if kind == 2:
                assert got == rid, "rcon auth failed"
                return

    def cmd(self, command: str) -> str:
        if self.sock is None:
            self.connect()
        self._pkt(2, command)
        return self._read()[2]

    def lua(self, body: str):
        """Run Lua on the server and return the JSON it prints."""
        out = self.cmd(f"/silent-command rcon.print(helpers.table_to_json({{v = (function() {body} end)()}}))")
        return json.loads(out)["v"] if out.strip() else None

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None


class Mcp:
    def __init__(self):
        self.p = subprocess.Popen([ "python3", str(ROOT / "mcp" / "factorio_mcp.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  env=dict(os.environ, FACTORIO_BRIDGE_URL=HTTP))
        self.n = 0
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "l2", "version": "0"}})
        assert init["serverInfo"]["name"] == "factorio"
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        assert resp["id"] == self.n, resp
        assert "error" not in resp, resp
        return resp["result"]

    def tool(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        payload = json.loads(r["content"][0]["text"])
        assert r["isError"] == (not payload.get("ok")), r
        return payload

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)


class Stack:
    def __init__(self):
        self.game = None
        self.bridge = None
        self.rcon = Rcon()
        self.logs = []

    # ── factorio ──

    def prepare(self):
        shutil.rmtree(DATA, ignore_errors=True)
        (DATA / "mods").mkdir(parents=True)
        (DATA / "saves").mkdir()
        shutil.copytree(ROOT / "mod" / "kc-companion", DATA / "mods" / "kc-companion")
        # Level 2 runs on headless 2.1 (2.0.x crashes on any UDP packet in a headless server, see
        # README); a 2.0 mod does not load on 2.1, so the test copy declares the running minor version.
        ver = subprocess.run([FACTORIO, "--version"], capture_output=True, text=True).stdout.split()[1]
        info_path = DATA / "mods" / "kc-companion" / "info.json"
        info = json.loads(info_path.read_text())
        info["factorio_version"] = ".".join(ver.split(".")[:2])
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2))
        (DATA / "mods" / "mod-list.json").write_text(json.dumps({"mods": [
            {"name": "base", "enabled": True}, {"name": "kc-companion", "enabled": True},
            {"name": "space-age", "enabled": False}, {"name": "quality", "enabled": False},
            {"name": "elevated-rails", "enabled": False},
        ]}))
        (DATA / "config.ini").write_text(f"[path]\nread-data=/opt/factorio/data\nwrite-data={DATA}\n")
        (DATA / "server-settings.json").write_text(json.dumps({
            "name": "factorycrew-l2", "description": "", "visibility": {"public": False, "lan": False},
            "require_user_verification": False, "auto_pause": False, "autosave_interval": 0,
        }))
        subprocess.run([FACTORIO, "-c", str(DATA / "config.ini"), "--create", str(DATA / "saves" / "l2.zip")],
                       check=True, stdout=open(DATA / "create.log", "w"), stderr=subprocess.STDOUT, timeout=180)

    def start_game(self):
        log = open(DATA / "factorio.log", "a")
        self.logs.append(log)
        # stdin must stay open: the headless server exits on EOF.
        self.game = subprocess.Popen(
            [FACTORIO, "-c", str(DATA / "config.ini"), "--start-server", str(DATA / "saves" / "l2.zip"),
             "--server-settings", str(DATA / "server-settings.json"), "--enable-lua-udp", "34200",
             "--rcon-port", str(RCON_PORT), "--rcon-password", RCON_PASS],
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
        wait_until(lambda: self.rcon.cmd("/silent-command rcon.print('up')").strip() == "up",
                   timeout=180, step=0.5, what="factorio RCON")

    def pause_game(self):
        self.game.send_signal(signal.SIGSTOP)

    def resume_game(self):
        self.game.send_signal(signal.SIGCONT)

    # ── bridge ──

    def start_bridge(self):
        log = open(DATA / "bridge.log", "a")
        self.logs.append(log)
        self.bridge = subprocess.Popen(["python3", str(ROOT / "bridge" / "factorio_bridge.py"), *BRIDGE_ARGS],
                                       stdout=log, stderr=subprocess.STDOUT)
        wait_until(lambda: http("GET", "/health")["ok"], timeout=10, what="bridge http")

    def stop_bridge(self):
        self.bridge.terminate()
        self.bridge.wait(5)

    def wait_online(self, timeout=30):
        return wait_until(lambda: http("GET", "/health")["game_online"], timeout=timeout, what="game online at bridge")

    # ── mod state over RCON ──

    def mod_state(self):
        return self.rcon.lua('return remote.call("kc", "state")')

    def ask(self, chat, text):
        return self.rcon.lua(f'return remote.call("kc", "ask", {lua_str(chat)}, {lua_str(text)})')

    def tab(self, chat):
        return (self.mod_state()["tabs"] or {}).get(chat)

    def stop(self):
        self.rcon.close()
        for p in (self.bridge, self.game):
            if p and p.poll() is None:
                p.send_signal(signal.SIGCONT)
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        for f in self.logs:
            f.close()


@pytest.fixture(scope="session")
def sentry():
    """A fake Sentry for every part of the stack (docs/sentry.md): what the bridge and the MCP
    server report lands here, and nothing a test plants reaches the real one."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from fake_sentry import FakeSentry
    s = FakeSentry()
    outbox = DATA / "outbox"
    shutil.rmtree(outbox, ignore_errors=True)
    os.environ.update(FC_SENTRY_DSN=s.dsn, FC_REPORT_OUTBOX=str(outbox), FC_REPORT_ENV="dev-l2")
    yield s
    s.close()


@pytest.fixture(scope="session")
def stack(sentry):
    s = Stack()
    s.prepare()
    s.start_bridge()
    s.start_game()
    try:
        s.wait_online(60)
        s.rcon.cmd(f"/silent-command game.speed = {GAME_SPEED}")
        yield s
    finally:
        s.stop()


@pytest.fixture()
def mcp(stack):
    m = Mcp()
    yield m
    m.close()


@pytest.fixture(autouse=True)
def drain(stack):
    """Each test starts with the bridge's ask queue empty and the game reachable."""
    if stack.bridge.poll() is not None:
        stack.start_bridge()
    stack.resume_game()
    stack.wait_online()
    asks = http("GET", "/asks?wait=0&lease=0.01")["asks"]
    if asks:
        http("POST", "/asks/done", {"ids": [a["id"] for a in asks]})
    yield
