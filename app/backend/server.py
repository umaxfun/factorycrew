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

log = logging.getLogger("factorycrew")
MOD = "kc-companion"
MANAGED_MARK = ".factorycrew-managed"  # a mod folder we installed and may replace


def load_config() -> dict:
    try:
        return json.loads((ROOT / "data" / "config.json").read_text())
    except (OSError, ValueError):
        return {}


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


async def health(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(reader.readline(), 5)
        body = json.dumps({"status": "ok", "app": "factorycrew"}).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config()
    try:
        log.info(install_mod(factorio_mods_dir()))
    except Exception:  # a failed mod copy must not keep the bridge down
        log.exception("mod install failed")
    s = fb.Settings(udp_port=int(cfg.get("udp_port", 34199)), game_port=int(cfg.get("game_port", 34200)),
                    http_port=int(cfg.get("http_port", 34198)))
    port = int(os.environ.get("PORT", "0"))
    # Health first: the gateway treats a child that dies on its first bind as a port collision.
    hs = await asyncio.start_server(health, "127.0.0.1", port)
    log.info("health on 127.0.0.1:%d", port)
    async with hs:
        await asyncio.gather(hs.serve_forever(), fb.serve(s))


if __name__ == "__main__":
    asyncio.run(main())
