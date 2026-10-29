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
# FC_UI_HOME / FC_UI_HTTP_PORT: a second stand beside the default one (e.g. one per worktree),
# with its own data dir and ports http, http+1 (bridge UDP), http+2 (game UDP)
DEV = Path(os.environ.get("FC_UI_HOME") or Path.home() / ".factorycrew-dev")


def _in_linked_worktree():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-dir", "--git-common-dir"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        return len(out) == 2 and Path(ROOT, out[0]).resolve() != Path(ROOT, out[1]).resolve()
    except (OSError, subprocess.SubprocessError):
        return False
DATA = DEV / "factorio"
APP = Path(os.environ.get("FACTORIO_APP", "/Users/Shared/factorio.app"))
BIN = APP / "Contents" / "MacOS" / "factorio"
SAVE = DATA / "saves" / "ui-test.zip"
HTTP = int(os.environ.get("FC_UI_HTTP_PORT") or 34298)
UDP_BRIDGE, UDP_GAME = HTTP + 1, HTTP + 2
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


def mod_digest(src=None):
    """Hash of the mod as the game loads it (tests left out, protocol.lua's port line ignored)."""
    import hashlib
    src = Path(src or os.environ.get("FC_UI_MOD_SRC") or ROOT / "mod" / "kc-companion")
    h = hashlib.sha256()
    for f in sorted(p for p in src.rglob("*") if p.is_file() and "tests" not in p.relative_to(src).parts
                    and p.name != ".factorycrew-managed"):
        data = f.read_bytes()
        if f.name == "protocol.lua":
            data = b"\n".join(l for l in data.split(b"\n") if not l.startswith(b"M.BRIDGE_PORT ="))
        h.update(str(f.relative_to(src)).encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def deploy_fixture(mods):
    """FC_UI_MODS: play a player's mod set (docs/atlas.md, Fixture): their zips, mod list and
    settings, with this checkout's kc-companion in place of theirs. FC_UI_SAVE: load that save
    (copied in; the original is never written)."""
    src = Path(os.environ["FC_UI_MODS"])
    for f in mods.glob("*.zip"):
        f.unlink()
    for f in list(src.glob("*.zip")) + [src / "mod-settings.dat"]:
        if f.exists():
            subprocess.run(["cp", "-c", str(f), str(mods / f.name)], check=False, capture_output=True)
            if not (mods / f.name).exists():
                subprocess.run(["cp", str(f), str(mods / f.name)], check=True)
    ml = json.loads((src / "mod-list.json").read_text())
    ml["mods"] = [m for m in ml["mods"] if m["name"] != "kc-companion"] + [{"name": "kc-companion", "enabled": True}]
    (mods / "mod-list.json").write_text(json.dumps(ml, indent=1))


def save_path():
    if os.environ.get("FC_UI_SAVE"):
        return DATA / "saves" / ("fixture-" + Path(os.environ["FC_UI_SAVE"]).name)
    return SAVE


def deploy():
    mods = DATA / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    # FC_UI_MOD_SRC: take the mod from elsewhere, e.g. the built app (dist/factorycrew/mod/kc-companion)
    src = os.environ.get("FC_UI_MOD_SRC") or f"{ROOT}/mod/kc-companion"
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "tests", "--exclude", ".factorycrew-managed",
                    f"{src.rstrip('/')}/", f"{mods}/kc-companion/"], check=True)
    proto = mods / "kc-companion" / "scripts" / "protocol.lua"
    text = proto.read_text(encoding="utf-8").replace("M.BRIDGE_PORT = 34199", f"M.BRIDGE_PORT = {UDP_BRIDGE}")
    proto.write_text(text, encoding="utf-8")
    if os.environ.get("FC_UI_MODS"):
        deploy_fixture(mods)
    else:
        # a fixture run before left a player's mods and their settings here; with those settings the
        # plain dev save never came online (stuck after loading the map)
        for f in list(mods.glob("*.zip")) + [mods / "mod-settings.dat"]:
            if f.exists():
                f.unlink()
        (mods / "mod-list.json").write_text(json.dumps({"mods": [
            {"name": "base", "enabled": True}, {"name": "kc-companion", "enabled": True},
            {"name": "space-age", "enabled": False}, {"name": "quality", "enabled": False},
            {"name": "elevated-rails", "enabled": False}]}))
    cfg = DATA / "config.ini"
    if not cfg.exists():
        cfg.write_text(f"[path]\nread-data=__PATH__executable__/../data\nwrite-data={DATA}\n")
    if os.environ.get("FC_UI_SAVE"):
        save_path().parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", os.environ["FC_UI_SAVE"], str(save_path())], check=True)
    # the plain dev save is created only with the plain mod set: one created while a player's mods were
    # deployed (FC_UI_MODS) was a modded map that the plain client then hung on
    if not SAVE.exists() and not os.environ.get("FC_UI_MODS"):
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
    # FC_UI_EXTERNAL_BRIDGE=1: a bridge on these ports already runs, e.g. the backend of the
    # factorycrew app in a dev gateway (data/config.json 34298/34299/34300) for an app e2e.
    if not alive("bridge") and not os.environ.get("FC_UI_EXTERNAL_BRIDGE"):
        spawn("bridge", ["/usr/bin/python3", str(ROOT / "bridge" / "factorio_bridge.py"),
                         "--udp-port", str(UDP_BRIDGE), "--game-port", str(UDP_GAME), "--http-port", str(HTTP)])
        time.sleep(1)
    if not alive("game"):
        # The Steam build relaunches itself through Steam unless it is told its app id.
        env = dict(os.environ, SteamAppId="427520", SteamGameId="427520")
        spawn("game", [str(BIN), "-c", str(DATA / "config.ini"), "--load-game", str(save_path()),
                       "--enable-lua-udp", str(UDP_GAME)], env)
        # what this game process loaded; scripts/stand.sh check compares it with the checkout
        (DEV / "game-mod.sha").write_text(mod_digest())
    wait = float(os.environ.get("FC_UI_START_TIMEOUT") or (600 if os.environ.get("FC_UI_SAVE") else 120))
    deadline = time.time() + wait
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
    raise SystemExit(f"game not online after {wait:.0f} s, see {DEV / 'game.log'}")


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
    # a linked worktree never drives the shared default client: it has its own stand
    if (not os.environ.get("FC_UI_HOME") and not os.environ.get("FC_ALLOW_DEFAULT")
            and argv and argv[0] != "digest" and _in_linked_worktree()):
        raise SystemExit("ui_dev.py: this is a worktree; use its own stand: scripts/stand.sh ui " + " ".join(argv))
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
    elif cmd == "digest":
        print(mod_digest())
    elif cmd == "steps":
        for s in argv[1:]:
            run_step(s)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
