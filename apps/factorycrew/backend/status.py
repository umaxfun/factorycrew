"""Liveness of every piece of the factorycrew app, for its status page in the dashboard.

Each check returns {"id", "label", "state": ok|warn|fail|idle, "detail", "fix"?}: `detail` says
what was seen, `fix` what the player can do about it. Nothing here changes anything.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # <home>/apps/factorycrew
APP = "factorycrew"
MOD = "kc-companion"
TOOLS = ("factorio_query", "factorio_reply")  # without these Kira cannot see or answer the game
DISPATCH_EVERY = 60  # the cron wakes every 30 s and long-polls; silence past this means it is not running
MCP_PROBE_TTL = 60.0

_mcp_cache: dict = {"at": 0.0, "result": None}


def check(cid, label, state, detail, fix=None):
    out = {"id": cid, "label": label, "state": state, "detail": detail}
    if fix:
        out["fix"] = fix
    return out


def kirocrew_home() -> Path:
    return Path(os.environ.get("KIROCREW_HOME") or ROOT.parents[1])


def agents_dirs(home: Path) -> list[Path]:
    """The gateway keeps agent files in <home>/kiro/agents, or ~/.kiro/agents for the default home."""
    dirs = [home / "kiro" / "agents"]
    if home == Path.home() / ".kiro" / "crew":
        dirs.insert(0, Path.home() / ".kiro" / "agents")
    return dirs


def kirocrew_version() -> str | None:
    """Version of the Kiro Crew that runs this backend: Info.plist of the .app bundle around Python."""
    p = Path(sys.executable).resolve()
    for parent in p.parents:
        if parent.suffix == ".app":
            try:
                with open(parent / "Contents" / "Info.plist", "rb") as f:
                    return plistlib.load(f).get("CFBundleShortVersionString")
            except (OSError, ValueError):
                return None
    return None


def version_tuple(v: str) -> tuple:
    head = v.split("-")[0]
    return tuple(int(x) for x in head.split(".") if x.isdigit())


def bridge_health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def probe_mcp() -> dict:
    """Start the MCP server the way the gateway does and ask for its tools (cached for a minute)."""
    now = time.monotonic()
    if _mcp_cache["result"] is not None and now - _mcp_cache["at"] < MCP_PROBE_TTL:
        return _mcp_cache["result"]
    script = ROOT / "mcp" / "factorio_mcp.py"
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "status"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    t0 = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-s", str(script)], input="".join(json.dumps(r) + "\n" for r in reqs),
                           capture_output=True, text=True, timeout=15)
        tools = []
        for line in p.stdout.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 2:
                tools = [t.get("name") for t in (msg.get("result") or {}).get("tools") or []]
        res = {"tools": tools, "ms": int((time.monotonic() - t0) * 1000), "stderr": p.stderr.strip()[-300:]}
    except (OSError, subprocess.TimeoutExpired) as e:
        res = {"tools": [], "ms": int((time.monotonic() - t0) * 1000), "stderr": str(e)}
    _mcp_cache.update(at=now, result=res)
    return res


def installed_agent(home: Path) -> tuple[Path | None, dict | None]:
    for d in agents_dirs(home):
        p = d / f"{APP}--factorio-companion.json"
        if p.exists():
            try:
                return p, json.loads(p.read_text())
            except (OSError, ValueError):
                return p, None
    return None, None


def status(config: dict, mods_dir: Path | None, mod_note: str) -> dict:
    home = kirocrew_home()
    port = int(config.get("http_port", 34198))
    game_port = int(config.get("game_port", 34200))
    checks = []

    v = kirocrew_version()
    if v and version_tuple(v) < (0, 11):
        checks.append(check("kirocrew", "Kiro Crew", "fail", f"version {v}",
                            "This app needs Kiro Crew 0.11 or newer: on 0.10 Kira gets no Factorio tools."))
    else:
        checks.append(check("kirocrew", "Kiro Crew", "ok", f"version {v or 'unknown'}, home {home}"))

    h = bridge_health(port)
    if h is None:
        checks.append(check("bridge", "Bridge", "fail", f"no answer on 127.0.0.1:{port}",
                            "Disable and enable the app; if it stays down, another program may hold the port."))
    else:
        checks.append(check("bridge", "Bridge", "ok", f"listening on :{port}, game expected on UDP :{game_port}"))

    if h is None:
        checks.append(check("game", "Factorio", "idle", "unknown while the bridge is down"))
    elif h.get("game_online"):
        run = h.get("run_name") or h.get("run_id") or "?"
        checks.append(check("game", "Factorio", "ok", f"online, run \"{run}\", tick {h.get('tick')}, "
                                                       f"tabs: {', '.join(h.get('tabs') or []) or 'none'}"))
    else:
        seen = h.get("last_seen_s")
        detail = "never seen since the app started" if seen is None else f"last heard {int(seen)} s ago"
        checks.append(check("game", "Factorio", "warn", detail,
                            f"Start Factorio with the launch option --enable-lua-udp {game_port}, load a save "
                            "with the kc-companion mod on, and do not leave it paused."))

    shipped = None
    try:
        shipped = json.loads((ROOT / "mod" / MOD / "info.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        pass
    dst = mods_dir / MOD if mods_dir else None
    if dst is None:
        checks.append(check("mod", "Mod", "fail", mod_note, "Start Factorio once so it creates its mods folder, "
                                                            "then disable and enable the app."))
    else:
        try:
            have = json.loads((dst / "info.json").read_text())["version"]
        except (OSError, ValueError, KeyError):
            have = None
        if have is None:
            checks.append(check("mod", "Mod", "fail", f"not in {mods_dir}", mod_note))
        elif have != shipped:
            checks.append(check("mod", "Mod", "warn", f"{have} in the mods folder, the app ships {shipped}", mod_note))
        else:
            checks.append(check("mod", "Mod", "ok", f"{have} in {mods_dir} (restart Factorio after an update)"))

    seen = None if h is None else h.get("dispatcher_seen_s")
    if h is None:
        checks.append(check("dispatcher", "Dispatcher", "idle", "unknown while the bridge is down"))
    elif seen is None or seen > DISPATCH_EVERY:
        checks.append(check("dispatcher", "Dispatcher", "fail",
                            "has not asked for questions" + ("" if seen is None else f" for {int(seen)} s"),
                            "Questions from the game reach no chat. Turn on agent.session_control in Settings "
                            "and check the app's cron factorio-dispatch under Crons."))
    else:
        queued = (h.get("queued_asks") or 0) + (h.get("leased_asks") or 0)
        checks.append(check("dispatcher", "Dispatcher", "ok", f"polled {int(seen)} s ago, {queued} questions in flight"))

    m = probe_mcp()
    missing = [t for t in TOOLS if t not in m["tools"]]
    if missing:
        checks.append(check("mcp", "MCP server", "fail", f"started without {', '.join(missing)}: {m['stderr'] or 'no output'}",
                            "Python could not run the app's MCP server; reinstall the app."))
    else:
        checks.append(check("mcp", "MCP server", "ok", f"starts and lists {len(m['tools'])} tools ({m['ms']} ms)"))

    path, agent = installed_agent(home)
    servers = (agent or {}).get("mcpServers") or {}
    if path is None:
        checks.append(check("agent", "Kira's tools", "fail", "agent factorio-companion is not registered",
                            "Disable and enable the app."))
    elif not any("factorio" in k for k in servers):
        checks.append(check("agent", "Kira's tools", "fail", f"{path.name} has no Factorio MCP server",
                            "Kira will say she has no tools. Update Kiro Crew to 0.11 or newer, then disable "
                            "and enable the app."))
    else:
        used = None if h is None else h.get("agent_seen_s")
        detail = "wired to the agent; " + ("not used yet since the app started" if used is None
                                            else f"last tool call {int(used)} s ago")
        checks.append(check("agent", "Kira's tools", "ok", detail))

    worst = "ok"
    for c in checks:
        if c["state"] == "fail":
            worst = "fail"
            break
        if c["state"] == "warn":
            worst = "warn"
    return {"ok": True, "state": worst, "checks": checks, "app_version": _app_version()}


def _app_version():
    try:
        return json.loads((ROOT / "app.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return None
