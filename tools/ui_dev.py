#!/usr/bin/env python3
"""UI iteration loop: a real Factorio client under this account, driven over the bridge.

The game is the player's copy at /Users/Shared/factorio.app (2.0.77, Steam build) with its
own data dir ~/.factorycrew-dev/factorio, and a dev bridge on its own ports, so it never
clashes with the game the player runs under `gamer` (34199/34200/34198).

  tools/ui_dev.py start                 # deploy the mod, start bridge + game, wait until online
  tools/ui_dev.py stop
  tools/ui_dev.py restart               # after a mod change (data.lua / control.lua)
  tools/ui_dev.py shot NAME [STEP ...]  # run steps, then a screenshot with the GUI
  tools/ui_dev.py lua 'return ...'      # one query

Steps (each one argument, verb:args):
  open | close | click:<element> | type:<element>=<text> | reply:<tab>=<text>
  status:<tab>=thinking|idle|no_reply | lua:<body> | wait:<seconds>

Screenshots land in ~/.factorycrew-dev/factorio/script-output/kc/<NAME>.png; the path is printed.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = Path.home() / ".factorycrew-dev"
DATA = DEV / "factorio"
APP = Path(os.environ.get("FACTORIO_APP", "/Users/Shared/factorio.app"))
BIN = APP / "Contents" / "MacOS" / "factorio"
SAVE = DATA / "saves" / "ui-test.zip"
UDP_BRIDGE, UDP_GAME, HTTP = 34299, 34300, 34298
BRIDGE = f"http://127.0.0.1:{HTTP}"
SHOTS = DATA / "script-output" / "kc"


def http(method, path, body=None, timeout=20):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BRIDGE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def online():
    try:
        return http("GET", "/health", timeout=2)["game_online"]
    except OSError:
        return False


def lua(body):
    r = http("POST", "/query", {"lua": body, "timeout": 15})
    if not r.get("ok"):
        raise SystemExit(f"query failed: {r}")
    return r.get("data")


def lua_str(s):
    return json.dumps(s, ensure_ascii=False)


# ── processes ────────────────────────────────────────────────────────────


def pid_file(name):
    return DEV / f"{name}.pid"


def alive(name):
    try:
        pid = int(pid_file(name).read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def deploy():
    mods = DATA / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "tests",
                    f"{ROOT}/mod/kc-companion/", f"{mods}/kc-companion/"], check=True)
    proto = mods / "kc-companion" / "scripts" / "protocol.lua"
    text = proto.read_text(encoding="utf-8").replace("M.BRIDGE_PORT = 34199", f"M.BRIDGE_PORT = {UDP_BRIDGE}")
    proto.write_text(text, encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [
        {"name": "base", "enabled": True}, {"name": "kc-companion", "enabled": True},
        {"name": "space-age", "enabled": False}, {"name": "quality", "enabled": False},
        {"name": "elevated-rails", "enabled": False}]}))
    cfg = DATA / "config.ini"
    if not cfg.exists():
        cfg.write_text(f"[path]\nread-data=__PATH__executable__/../data\nwrite-data={DATA}\n")
    if not SAVE.exists():
        SAVE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(BIN), "-c", str(cfg), "--create", str(SAVE)], check=True,
                       stdout=open(DEV / "create.log", "w"), stderr=subprocess.STDOUT, timeout=180)


def spawn(name, argv, env=None):
    log = open(DEV / f"{name}.log", "w")
    p = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=env, start_new_session=True)
    pid_file(name).write_text(str(p.pid))


def start():
    DEV.mkdir(parents=True, exist_ok=True)
    deploy()
    if not alive("bridge"):
        spawn("bridge", ["/usr/bin/python3", str(ROOT / "bridge" / "factorio_bridge.py"),
                         "--udp-port", str(UDP_BRIDGE), "--game-port", str(UDP_GAME), "--http-port", str(HTTP)])
        time.sleep(1)
    if not alive("game"):
        # The Steam build relaunches itself through Steam unless it is told its app id.
        env = dict(os.environ, SteamAppId="427520", SteamGameId="427520")
        spawn("game", [str(BIN), "-c", str(DATA / "config.ini"), "--load-game", str(SAVE),
                       "--enable-lua-udp", str(UDP_GAME)], env)
    deadline = time.time() + 120
    while time.time() < deadline:
        if online():
            # the freeplay intro cutscene would sit on top of every screenshot
            lua("local p = game.get_player(1) if p and p.controller_type == defines.controllers.cutscene "
                "then p.exit_cutscene() end return true")
            print("game online")
            return
        if not alive("game"):
            raise SystemExit(f"game exited, see {DEV / 'game.log'}")
        time.sleep(1)
    raise SystemExit(f"game not online after 120 s, see {DEV / 'game.log'}")


def stop():
    for name in ("game", "bridge"):
        pid = alive(name)
        if pid:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(50):
                if not alive(name):
                    break
                time.sleep(0.2)
        pid_file(name).unlink(missing_ok=True)
    print("stopped")


# ── steps ────────────────────────────────────────────────────────────────


def run_step(step):
    verb, _, arg = step.partition(":")
    if verb == "open":
        lua('return remote.call("kc", "ui_open")')
    elif verb == "close":
        lua('return remote.call("kc", "ui_close")')
    elif verb == "click":
        if not lua(f'return remote.call("kc", "ui_click", {lua_str(arg)})'):
            raise SystemExit(f"no element {arg!r} to click")
    elif verb == "type":
        name, _, text = arg.partition("=")
        if not lua(f'return remote.call("kc", "ui_confirm", {lua_str(name)}, {lua_str(text)})'):
            raise SystemExit(f"no field {name!r}")
    elif verb == "reply":
        tab, _, text = arg.partition("=")
        http("POST", "/reply", {"chat": tab, "text": text})
    elif verb == "status":
        tab, _, state = arg.partition("=")
        note = "no_reply" if state == "no_reply" else None
        state = "idle" if state == "no_reply" else state
        # straight into the mod, as if the bridge had sent it
        msg = {"type": "status", "chat": tab, "state": state}
        if note:
            msg["note"] = note
        lua(f'return remote.call("kc", "ui_inject", helpers.json_to_table({lua_str(json.dumps(msg, ensure_ascii=False))}))')
    elif verb == "lua":
        print(json.dumps(lua(arg), ensure_ascii=False))
    elif verb == "wait":
        time.sleep(float(arg))
    else:
        raise SystemExit(f"unknown step {step!r}")
    time.sleep(0.4)  # let the game run a few ticks so replies and refreshes land


def shot(name, steps):
    for s in steps:
        run_step(s)
    path = SHOTS / f"{name}.png"
    if path.exists():
        path.unlink()
    lua(f'game.take_screenshot{{player = game.get_player(1), show_gui = true, resolution = {{1600, 1000}}, '
        f'path = "kc/{name}.png", allow_in_replay = true}} return true')
    deadline = time.time() + 20
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            time.sleep(0.3)  # finished writing
            print(path)
            return
        time.sleep(0.2)
    raise SystemExit("no screenshot file; is the game window rendering?")


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "restart":
        stop()
        start()
    elif cmd == "shot":
        shot(argv[1], argv[2:])
    elif cmd == "lua":
        print(json.dumps(lua(argv[1]), ensure_ascii=False))
    elif cmd == "steps":
        for s in argv[1:]:
            run_step(s)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
