#!/usr/bin/env python3
"""MCP server `factorio` (stdio): agent tools that talk to the bridge over HTTP on 127.0.0.1.

Stdlib only, newline-delimited JSON-RPC 2.0 as in the MCP stdio transport.
Bridge URL: env FACTORIO_BRIDGE_URL, default http://127.0.0.1:34198.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import urllib.error
import urllib.request



def _bridge_url() -> str:
    """FACTORIO_BRIDGE_URL, else the http_port in <app>/data/config.json (Kiro Crew app install)."""
    if os.environ.get("FACTORIO_BRIDGE_URL"):
        return os.environ["FACTORIO_BRIDGE_URL"]
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "data" / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    return f"http://127.0.0.1:{int(cfg.get('http_port', 34198))}"


BRIDGE = _bridge_url().rstrip("/")
PROTOCOL_VERSION = "2025-06-18"

POS = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"]}

TOOLS = [
    {
        "name": "factorio_query",
        "description": (
            "Run a Lua body on the live save and return what it `return`s, as JSON. "
            "Env: game, helpers, defines, prototypes, surface (player's surface or the one given), player. "
            "The game is FROZEN while the query runs and Lua has no instruction limit: search by area or "
            "filter, aggregate in Lua (counts, groups, positions), never dump entities. Result cap 64 KB. "
            "`ms` in the answer is the round trip through the bridge (includes up to ~170 ms of polling)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"lua": {"type": "string"}, "surface": {"type": "string"}},
            "required": ["lua"],
        },
    },
    {
        "name": "factorio_mark",
        "description": (
            "Put map marks (chart tags prefixed [KC]) for a chat tab. Returns ids of placed marks and "
            "`failed` [{index, reason}] for ones the game refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat": {"type": "string"},
                "marks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "surface": {"type": "string"}, "position": POS,
                            "text": {"type": "string"}, "icon": {"type": "string", "description": "item name"},
                        },
                        "required": ["position"],
                    },
                },
            },
            "required": ["chat", "marks"],
        },
    },
    {
        "name": "factorio_unmark",
        "description": "Remove marks this mod placed: by ids within the chat, all of the chat, or all. Player tags are never touched.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat": {"type": "string"}, "ids": {"type": "array", "items": {"type": "integer"}},
                "all_in_chat": {"type": "boolean"}, "all": {"type": "boolean"},
            },
            "required": ["chat"],
        },
    },
    {
        "name": "factorio_list_marks",
        "description": "Live marks with id, chat, surface, position and text. Omit chat for all chats.",
        "inputSchema": {"type": "object", "properties": {"chat": {"type": "string"}}},
    },
    {
        "name": "factorio_reply",
        "description": (
            "Show the answer in the in-game tab. When the turn was started by a question from the game, "
            "end it with this call. Keep it to a few lines; the window is small."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"chat": {"type": "string"}, "text": {"type": "string"}},
            "required": ["chat", "text"],
        },
    },
    {
        "name": "factorio_progress",
        "description": (
            "Show the player what you are doing during a long turn, e.g. 'planning the layout', "
            "'placing 120 lamp ghosts'. One short line (≤80 chars) under 'thinking…' in the tab; it does "
            "not end the turn and is not an answer. The window already counts your tool calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"chat": {"type": "string"}, "text": {"type": "string"}},
            "required": ["chat", "text"],
        },
    },
]

ROUTES = {
    "factorio_progress": "/progress",
    "factorio_query": "/query",
    "factorio_mark": "/mark",
    "factorio_unmark": "/unmark",
    "factorio_list_marks": "/list_marks",
    "factorio_reply": "/reply",
}


def bridge_post(path: str, body: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(BRIDGE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"ok": False, "error": "bridge_http_error", "message": str(e)}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": "bridge_unreachable", "message": f"{BRIDGE}: {e}"}


def call_tool(name: str, args: dict) -> dict:
    path = ROUTES.get(name)
    if not path:
        return {"ok": False, "error": "unknown_tool", "message": name}
    return bridge_post(path, args or {})


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if rid is None:  # notification
        return None
    if method == "initialize":
        client_ver = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        result = {"protocolVersion": client_ver, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "factorio", "version": "0.1.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = req.get("params") or {}
        res = call_tool(p.get("name"), p.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                  "isError": not res.get("ok", False)}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = handle(req)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
