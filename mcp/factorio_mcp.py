#!/usr/bin/env python3
"""MCP server `factorio` (stdio): agent tools that talk to the bridge over HTTP on 127.0.0.1.

Stdlib only, newline-delimited JSON-RPC 2.0 as in the MCP stdio transport.
Bridge URL: env FACTORIO_BRIDGE_URL, default http://127.0.0.1:34198.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
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
            "Env: game, helpers, defines, prototypes, surface (player's surface or the one given), player, "
            "kc (helpers: kc.place_ghosts(surface, items) the ONLY way to place ghosts, refuses overlaps; "
            "kc.poles() best researched electric poles; kc.nearest_pole(surface, pos, radius)). "
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
        "name": "factorio_compute",
        "description": (
            "Run Python 3 (stdlib only: math, itertools, heapq, collections, json...) for anything heavier "
            "than a lookup: clustering, layouts, path finding for pipes/belts, pole placement, production "
            "ratios. Optional `data_lua` runs first in the game and its return value is the variable `data` "
            "(e.g. resource positions, entities in the area, recipes and machine speeds from prototypes). "
            "Helpers as `kc`: kc.connect(outputs, sink, blocked) one connected pipe/belt network joining all outputs, "
            "kc.footprint(x, y, w, h) tiles of an entity, kc.astar(start, goal, blocked) "
            "pipe/belt route around blocked tiles, kc.unreached(pipes, start, targets) connectivity check "
            "(must be [] before placing a pipe network), kc.unwired(poles, grid) copper-wire check (must be []), "
            "kc.tile(x, y). "
            "Set the variable `result` to what you need back (JSON-serialisable, ≤64 KB, e.g. a list of "
            "{name, x, y, direction}); print() goes to stdout. Off the game tick, so the game does not freeze. "
            "No network, no files outside its temp dir. Timeout 60 s by default, `timeout` up to 120."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "python": {"type": "string"}, "data_lua": {"type": "string"},
                "surface": {"type": "string"}, "timeout": {"type": "integer"},
            },
            "required": ["python"],
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


# ── factorio_compute: Python for the heavy part (layouts, optimisation, production math) ──

COMPUTE_TIMEOUT, COMPUTE_TIMEOUT_MAX = 60, 120
COMPUTE_OUT_MAX = 64 * 1024
PRELUDE = '''\
import json as _json
import sys as _sys
_sys.path.insert(0, ".")  # -I leaves the script's dir off sys.path; kc_compute.py sits there
import kc_compute as kc
with open("data.json", encoding="utf-8") as _f:
    data = _json.load(_f)
result = None
'''
KC_COMPUTE = Path(__file__).resolve().parent / "kc_compute.py"
EPILOGUE = '''
with open("result.json", "w", encoding="utf-8") as _f:
    _json.dump(result, _f, ensure_ascii=False)
'''


def sandbox_profile(work: str) -> str:
    """macOS seatbelt: no network, writes only in the work dir, no reading the user's home.

    The interpreter's own tree stays readable even if it lives under the home (uv, pyenv).
    Rules later in the profile win over earlier ones.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    keep = {os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, work)}
    reads = " ".join(f'(subpath "{p}")' for p in sorted(keep))
    return ("(version 1)(allow default)(deny network*)"
            f'(deny file-write*)(allow file-write* (subpath "{work}") (literal "/dev/null"))'
            f'(deny file-read* (subpath "{home}"))(allow file-read* {reads})')


_SANDBOX_OK: bool | None = None


def own_sandbox_works(work: str) -> bool:
    """Whether our seatbelt profile can be applied here; probed once per process.

    Under Kiro Crew this server already runs inside the gateway's own sandbox, and macOS refuses
    to nest one (`sandbox_apply: Operation not permitted`). Then the outer sandbox is what holds,
    and the code runs without ours rather than not at all.
    """
    global _SANDBOX_OK
    if _SANDBOX_OK is None:
        _SANDBOX_OK = False
        if sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec") \
                and not os.environ.get("FC_COMPUTE_NO_SANDBOX"):
            try:
                p = subprocess.run(["/usr/bin/sandbox-exec", "-p", sandbox_profile(work), "/usr/bin/true"],
                                   capture_output=True, timeout=10)
                _SANDBOX_OK = p.returncode == 0
            except (OSError, subprocess.SubprocessError):
                pass
    return _SANDBOX_OK


def compute(args: dict) -> dict:
    code = args.get("python")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "bad_request", "message": "python is required"}
    timeout = max(1, min(int(args.get("timeout") or COMPUTE_TIMEOUT), COMPUTE_TIMEOUT_MAX))
    data = None
    if args.get("data_lua"):
        q = bridge_post("/query", {"lua": args["data_lua"], "surface": args.get("surface"), "_agent": True})
        if not q.get("ok"):
            return {"ok": False, "error": "data_lua_failed", "message": q.get("message") or q.get("error"), "query": q}
        data = q.get("data")
    bridge_post("/alive", {"_agent": True, "text": "computing in Python"}, timeout=3)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="kc-compute-") as work:
        work = os.path.realpath(work)
        with open(os.path.join(work, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        with open(os.path.join(work, "main.py"), "w", encoding="utf-8") as f:
            f.write(PRELUDE + code + "\n" + EPILOGUE)
        # copied in: the sandbox does not let the code read this repo or the app's install dir
        with open(KC_COMPUTE, encoding="utf-8") as src, \
                open(os.path.join(work, "kc_compute.py"), "w", encoding="utf-8") as dst:
            dst.write(src.read())
        argv = [sys.executable, "-I", "main.py"]
        sandboxed = own_sandbox_works(work)
        if sandboxed:
            argv = ["/usr/bin/sandbox-exec", "-p", sandbox_profile(work), *argv]
        env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HOME": work, "TMPDIR": work}
        proc = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err, timed_out = b"", b"", False
        deadline = t0 + timeout
        while True:
            try:
                out, err = proc.communicate(timeout=max(0.1, min(15, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    proc.kill()
                    out, err = proc.communicate()
                    timed_out = True
                    break
                # a long computation is a working turn: keep the window's clock and idle timer alive
                bridge_post("/alive", {"_agent": True, "text": "", "step": False}, timeout=3)
        result = None
        try:
            with open(os.path.join(work, "result.json"), encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            pass
    res = {
        "ok": proc.returncode == 0 and not timed_out,
        "result": result,
        "stdout": out.decode("utf-8", "replace")[-COMPUTE_OUT_MAX:],
        "stderr": err.decode("utf-8", "replace")[-4000:],
        "seconds": round(time.monotonic() - t0, 2),
        "sandboxed": sandboxed,
    }
    if timed_out:
        res["error"] = "timeout"
        res["message"] = f"stopped after {timeout} s; make the computation smaller or raise timeout (max {COMPUTE_TIMEOUT_MAX})"
    elif proc.returncode != 0:
        res["error"] = "python_error"
    if len(json.dumps(result, ensure_ascii=False)) > COMPUTE_OUT_MAX:
        res.update(ok=False, result=None, error="result_too_large",
                   message="result over 64 KB; return less, e.g. a compact list of [x, y, name]")
    return res


def call_tool(name: str, args: dict) -> dict:
    if name == "factorio_compute":
        return compute(args or {})
    path = ROUTES.get(name)
    if not path:
        return {"ok": False, "error": "unknown_tool", "message": name}
    # _agent: tells the bridge this call is a step of Kira's turn (the window counts those)
    return bridge_post(path, {**(args or {}), "_agent": True})


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
