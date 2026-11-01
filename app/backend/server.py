#!/usr/bin/env python3
"""Backend of the factorycrew Kiro Crew app.

Kiro Crew starts this process when the app is enabled, health-checks it on $PORT and
stops it when the app is disabled. It:
  * runs the bridge (bridge/factorio_bridge.py) on the ports from data/config.json;
  * answers /health on $PORT (the gateway's port, separate from the bridge's own API);
  * installs or updates the kc-companion mod in the player's Factorio mods folder.

Ports live in <app>/data/config.json so the MCP server and the dispatcher read the same
values; defaults match the mod (game sends to 34199, listens on 34200).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

import factorio_bridge as fb  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import status  # noqa: E402  (status page checks)

report = fb.report  # report/kc_report.py, loaded by the bridge module; None if missing

log = logging.getLogger("factorycrew")
MOD = "kc-companion"
MANAGED_MARK = ".factorycrew-managed"  # a mod folder we installed and may replace


def load_config() -> dict:
    try:
        return json.loads((ROOT / "data" / "config.json").read_text())
    except (OSError, ValueError):
        return {}


def settings() -> dict:
    """What the status page can switch. `telemetry`: automatic error reports to the developer
    (docs/sentry.md), on unless the player turned it off."""
    return {"ok": True, "telemetry": load_config().get("telemetry", True) is not False}


def save_settings(body: dict) -> dict:
    """Only known keys with the right type; the rest of data/config.json is kept as it is.
    Every part reads `telemetry` from the file on each report, so no restart is needed."""
    if not isinstance(body, dict) or not isinstance(body.get("telemetry"), bool):
        return {"ok": False, "error": "bad_request", "message": "expected {\"telemetry\": true|false}"}
    cfg = load_config()
    cfg["telemetry"] = body["telemetry"]
    path = ROOT / "data" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
    log.info("error reports %s", "on" if body["telemetry"] else "off")
    return settings()


def factorio_mods_dir() -> Path | None:
    home = Path.home()
    if sys.platform == "darwin":
        d = home / "Library" / "Application Support" / "factorio" / "mods"
    elif sys.platform.startswith("win"):
        d = Path(os.environ.get("APPDATA", home)) / "Factorio" / "mods"
    else:
        d = home / ".factorio" / "mods"
    return d if d.is_dir() else None


def install_mod(mods: Path | None) -> str:
    """Copy the shipped mod into the game's mods folder; never touch a folder we did not put there."""
    src = ROOT / "mod" / MOD
    if mods is None:
        return "no Factorio mods folder found; start the game once, then restart the app"
    dst = mods / MOD
    version = json.loads((src / "info.json").read_text())["version"]
    if dst.is_symlink() or (dst.exists() and not (dst / MANAGED_MARK).exists()):
        return f"{dst} exists and is not managed by the app; left as is"
    if dst.exists():
        try:
            if json.loads((dst / "info.json").read_text())["version"] == version and \
                    (dst / MANAGED_MARK).read_text() == version:
                return f"mod {version} already installed"
        except (OSError, ValueError, KeyError):
            pass
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    (dst / MANAGED_MARK).write_text(version)
    return f"mod {version} installed into {dst} (restart Factorio to load it)"


STATE = {"config": {}, "mods": None, "mod_note": ""}


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """/health for the gateway; /api/status (the dashboard page, through the gateway's proxy
    /apps/factorycrew/api/status) for the liveness of every piece."""
    try:
        line = await asyncio.wait_for(reader.readline(), 5)
        length = 0
        while (h := await asyncio.wait_for(reader.readline(), 5)) not in (b"\r\n", b"\n", b""):
            name, _, value = h.decode("latin-1").partition(":")
            if name.strip().lower() == "content-length":
                length = min(int(value.strip() or 0), 4096)
        body_in = await asyncio.wait_for(reader.readexactly(length), 5) if length else b""
        parts = line.decode("latin-1").split(" ")
        method = parts[0]
        path = parts[1].split("?", 1)[0] if len(parts) > 1 else "/"
        status_code = "200 OK"
        if path == "/api/settings":
            if method == "POST":
                obj = save_settings(json.loads(body_in or b"{}"))
            else:
                obj = settings()
        elif path == "/api/status":
            obj = await asyncio.to_thread(status.status, STATE["config"], STATE["mods"], STATE["mod_note"])
        elif path in ("/health", "/api/health"):
            obj = {"status": "ok", "app": "factorycrew"}
        else:
            status_code, obj = "404 Not Found", {"ok": False, "error": "not_found", "path": path}
        body = json.dumps(obj, ensure_ascii=False).encode()
        writer.write(f"HTTP/1.1 {status_code}\r\nContent-Type: application/json; charset=utf-8\r\n"
                     f"Cache-Control: no-store\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                     + body)
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError):
        pass
    except Exception as e:  # a broken check must not take the backend down
        log.exception("request failed")
        if report:
            report.capture_exception(e, {"part": "backend", "where": "http"})
    finally:
        writer.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if report:
        report.configure("backend")  # the bridge in this process reports through it too
        report.install_hooks()
    cfg = load_config()
    mods = factorio_mods_dir()
    STATE.update(config=cfg, mods=mods)
    try:
        STATE["mod_note"] = install_mod(mods)
        log.info(STATE["mod_note"])
    except Exception as e:  # a failed mod copy must not keep the bridge down
        STATE["mod_note"] = f"mod install failed: {e}"
        log.exception("mod install failed")
        if report:
            report.capture_exception(e, {"part": "backend", "where": "install_mod"})
    s = fb.Settings(udp_port=int(cfg.get("udp_port", 34199)), game_port=int(cfg.get("game_port", 34200)),
                    http_port=int(cfg.get("http_port", 34198)), app_version=status._app_version())
    port = int(os.environ.get("PORT", "0"))
    # Health first: the gateway treats a child that dies on its first bind as a port collision.
    hs = await asyncio.start_server(handle, "127.0.0.1", port)
    log.info("health on 127.0.0.1:%d", port)
    async with hs:
        await asyncio.gather(hs.serve_forever(), fb.serve(s))


if __name__ == "__main__":
    asyncio.run(main())
