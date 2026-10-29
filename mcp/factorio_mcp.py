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
    """`--bridge URL`, else FACTORIO_BRIDGE_URL, else the http_port in <app>/data/config.json.

    The argument comes first: kiro-cli does not pass an agent spec's `env` block to the MCP
    process (measured 2026-10-29: the variable was missing, so a worktree's Kira talked to the
    player's bridge on 34198)."""
    if "--bridge" in sys.argv[1:-1]:
        return sys.argv[sys.argv.index("--bridge") + 1]
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
            "Library `import kc` (stdlib): kc.geo, kc.Grid, kc.search (bfs/astar/grid_path), kc.steiner.join, "
            "kc.cover.greedy, kc.wires (shorter-reach links, relay chains), kc.lp.solve (exact LP), kc.bp (blueprints), "
            "kc.world (scan/proto adapters), kc.circuits (load/explain/trace/run a network read with the mod's kc.circuits), "
            "kc.checks.all(plan, specs, ...) validator: [] before placing. "
            "Skills: `import belts` and the others in your rules. "
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
        "name": "factorio_atlas",
        "description": (
            "Atlas of the player's base: which machines form a block (one recipe standing together), "
            "which blocks form a zone, and where every ingredient comes from (another zone by belt/"
            "inserter/chest/pipe, a train stop, or a logistic network by robots). "
            "level=zone: a zone's blocks with live statuses, its inputs with their sources, what has no "
            "source; level=block: one block with rates per second at full speed (need/make), every input's "
            "source and every output's consumer. Target: `id` (Z12, B480), `at` {x, y} (a click, a pointer), "
            "or `box` {x0, y0, x1, y1} (a drag pointer: the zone around it, the covered blocks marked "
            "selected). `item` keeps only what makes or uses that item. The structure is read from the game "
            "once and cached (a big base takes seconds, the game never freezes); `refresh` re-reads it after "
            "building. Statuses are always live."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["zone", "block"]},
                "id": {"type": "string"}, "at": POS,
                "box": {"type": "object", "properties": {k: {"type": "number"} for k in ("x0", "y0", "x1", "y1")}},
                "item": {"type": "string"}, "limit": {"type": "integer"},
                "refresh": {"type": "boolean"}, "surface": {"type": "string"},
            },
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
import kc
with open("data.json", encoding="utf-8") as _f:
    data = _json.load(_f)
result = None
'''
KC_COMPUTE = Path(__file__).resolve().parent / "kc_compute.py"  # old name, a shim over kc/
KC_PKG = Path(__file__).resolve().parent / "kc"
SKILLS = Path(__file__).resolve().parents[1] / "skills"  # repo root or the installed app's root
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
        # copied in: the sandbox does not let the code read this repo or the app's install dir.
        # Skill code (skills/<name>/lib/*.py) comes along too, so `import belts` just works.
        for lib in [KC_COMPUTE, *sorted(SKILLS.glob("*/lib/*.py"))]:
            with open(lib, encoding="utf-8") as src, \
                    open(os.path.join(work, lib.name), "w", encoding="utf-8") as dst:
                dst.write(src.read())
        os.makedirs(os.path.join(work, "kc"), exist_ok=True)
        for lib in sorted(KC_PKG.glob("*.py")):  # the primitive library, as the package `kc`
            with open(lib, encoding="utf-8") as src, \
                    open(os.path.join(work, "kc", lib.name), "w", encoding="utf-8") as dst:
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


# ── factorio_atlas: the base as blocks and zones, structure cached here, state read live ──

ATLAS_CACHE: dict[str, dict] = {}  # requested surface ("" = the player's) -> {"atlas", "run_id"}
STATUS_LIMIT = 1500  # machines whose live status one call reads
STATUS_LUA = '''
local P = %s
local names = {}
for k, v in pairs(defines.entity_status) do names[v] = k end
local out = {}
for i, p in ipairs(P) do
  local e = surface.find_entity(p[1], {p[2], p[3]})
  out[i] = e and (names[e.status] or "?") or "gone"
end
return out
'''


def bridge_health() -> dict:
    try:
        with urllib.request.urlopen(BRIDGE + "/health", timeout=5) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def _atlas(args: dict) -> tuple[object, dict | None]:
    """The cached atlas for the requested surface, re-read on refresh or when another save runs."""
    from kc import atlas as kc_atlas
    key = args.get("surface") or ""
    health = bridge_health()
    run_id = health.get("run_id")
    c = ATLAS_CACHE.get(key)
    if args.get("refresh") or c is None or c["run_id"] != run_id:
        t0 = time.monotonic()
        r = bridge_post("/atlas", {"surface": args.get("surface"), "_agent": True}, timeout=330)
        if not r.get("ok"):
            return None, {"ok": False, "error": r.get("error") or "atlas_failed", "message": r.get("message"),
                          "detail": {k: v for k, v in r.items() if k != "text"}}
        try:
            a = kc_atlas.Atlas(r["text"])
        except ValueError as e:
            return None, {"ok": False, "error": "atlas_bad_dump", "message": str(e)}
        c = {"atlas": a, "run_id": run_id, "read_s": round(time.monotonic() - t0, 2), "dump": r.get("data")}
        ATLAS_CACHE[key] = c
    c["tick_now"] = health.get("tick")
    return c, None


def _live_status(atlas, machines: list) -> dict:
    machines = machines[:STATUS_LIMIT]
    pts = ",".join("{%s,%s,%s}" % (json.dumps(m["name"]), m["x"], m["y"]) for m in machines)
    q = bridge_post("/query", {"lua": STATUS_LUA % ("{" + pts + "}"), "surface": atlas.surface, "_agent": True})
    if not q.get("ok"):
        return {}
    data = q.get("data") or []
    if isinstance(data, dict):  # empty table
        data = []
    return {m["id"]: s for m, s in zip(machines, data)}


def atlas_tool(args: dict) -> dict:
    c, err = _atlas(args)
    if err:
        return err
    a = c["atlas"]
    level = args.get("level") or "zone"
    limit = max(1, min(int(args.get("limit") or 40), 200))
    tid, at, box = args.get("id"), args.get("at"), args.get("box")
    selected = None
    bi = zi = None
    if isinstance(tid, str) and tid[:1] in "ZB" and tid[1:].isdigit():
        n = int(tid[1:])
        if tid[0] == "Z" and n < len(a.zones):
            zi = n
        elif tid[0] == "B" and n < len(a.blocks):
            bi = n
    elif isinstance(box, dict) and all(k in box for k in ("x0", "y0", "x1", "y1")):
        x0, x1 = sorted((box["x0"], box["x1"]))
        y0, y1 = sorted((box["y0"], box["y1"]))
        selected = a.blocks_in_box(x0, y0, x1, y1)
        if selected:
            bi = selected[0]
    elif isinstance(at, dict) and "x" in at and "y" in at:
        bi = a.block_at(at["x"], at["y"])
    if bi is None and zi is None:
        return {"ok": False, "error": "not_found",
                "message": "no machine block at that id/place (a click must be on or next to a machine)"}
    if level == "block":
        if bi is None:
            return {"ok": False, "error": "bad_request", "message": "level=block needs a block: id B<n>, at or box"}
        view = a.block_view(bi, _live_status(a, a.blocks[bi]["machines"]))
        if selected:
            view["selected_blocks"] = [f"B{b}" for b in selected]
    else:
        zi = a.zone_of[bi] if zi is None else zi
        view = a.zone_view(zi, _live_status(a, a.machines_of(a.zones[zi]["blocks"])), item=args.get("item"),
                           selected=set(selected) if selected else None)
        rows = view["block_list"]
        if len(rows) > limit:
            view["block_list"], view["more_blocks"] = rows[:limit], len(rows) - limit
        if selected:
            others = sorted({a.zone_of[b] for b in selected} - {zi})
            if others:
                view["box_also_covers_zones"] = [f"Z{z}" for z in others]
    view["ok"] = True
    view["surface"] = a.surface
    if c.get("tick_now") is not None and a.tick is not None:
        view["structure_age_s"] = round((c["tick_now"] - a.tick) / 60)
    return view


def call_tool(name: str, args: dict) -> dict:
    if name == "factorio_compute":
        return compute(args or {})
    if name == "factorio_atlas":
        return atlas_tool(args or {})
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
