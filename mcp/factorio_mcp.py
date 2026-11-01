#!/usr/bin/env python3
"""MCP server `factorio` (stdio): agent tools that talk to the bridge over HTTP on 127.0.0.1.

Stdlib only, newline-delimited JSON-RPC 2.0 as in the MCP stdio transport.
Bridge URL: --bridge, else env FACTORIO_BRIDGE_URL, else the app's data/config.json, else
http://127.0.0.1:34198 (`_bridge_url`).
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


def _load_report():
    """report/kc_report.py (docs/sentry.md), one level up in the repo and in the app; None when
    it is missing or broken: the server runs the same without error reports."""
    d = Path(__file__).resolve().parents[1] / "report"
    if not (d / "kc_report.py").exists():
        return None
    sys.path.insert(0, str(d))
    try:
        import kc_report
        return kc_report
    except Exception:  # noqa: BLE001
        return None


report = _load_report()
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
            "end it with this call. Keep it to a few lines; the window is small. A last line "
            "`[OPTIONS: a | b]` (the player's language) becomes buttons that send the choice as his next message."
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
            "Skills (planners, each with a check(); calls in your rules): `import belts`, `power` (poles + line "
            "to the grid), `pipes` (oil fields, pipe networks), `mining` (ore outposts), `beacons`, `production` "
            "(ratios), `solar`. "
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
        "name": "factorio_panel",
        "description": (
            "Create or replace a LIVE PANEL in the game (give a short `title` and a one-line `description` for the saved artifact): a small window the mod refreshes by itself every "
            "`every` seconds (default 2) by running your Lua body, with no further turns from you. Use it when "
            "the player asks to SHOW/WATCH/KEEP an eye on something over time (a dashboard, 'show me live', "
            "'a panel with...'). `lua` has the factorio_query environment plus `input` (current values of "
            "`inputs`, strings) and `state` (a table kept between refreshes, e.g. for deltas). It must "
            "RETURN A VIEW: a list of blocks: a string (rich text ok, [item=iron-plate], [color=red]..[/color]); "
            "{title?, columns={...}, rows={{cell,...},...}, empty?}; {bar=v, max=m, caption?, color?}. "
            "Cells: string, number (formatted 1.2k), {item=name, text?} / {fluid=} / {signal=} (icon), "
            "{bar=v, max=m, caption?, color?}, {text=, color=red|green|yellow|orange|gray|blue}, "
            "LINK {gps={x,y}, surface?, text?, item?} (a button: a click opens the map at that "
            "position; any cell can be one, e.g. the number itself: {text=9759, gps=chest.position, surface='nauvis'}). "
            "[gps=..] rich text is NOT clickable in a panel (only in chat): use a link cell. "
            "BUTTONS WITH YOUR CODE: `actions` = {name: Lua body}; a cell {text=, action=name, args?={...}} is a "
            "button that runs that body on click, in the query environment plus `player` (who clicked), `args` "
            "(the cell's args; positions as {x=,y=}), `state`, `input`; the panel refreshes after it. With `gps` "
            "too, the map opens first, then the action runs. `kc.show_on_map(player, position, surface?, zoom?)` "
            "opens the map. Whatever the player asks a click to do (ping, zoom, mark, open a chest), write it as an action. "
            "`inputs`: drop-downs for the player, e.g. [{key:'period', label:'Period', options:['1m','10m','1h'], default:'10m'}]. "
            "The body runs once now and you get its `view` (or `error`) back: CHECK the numbers against a "
            "query before replying. It runs inside a game tick on every refresh: keep it cheap (filtered "
            "finds, no full-map scans; larger `every` for heavier bodies); errors show on the panel and "
            "5 in a row pause it. Same `name` replaces the panel; `remove: true` deletes it. "
            "`kc.panels()` in factorio_query lists panels with their last error. "
            "Every panel that works is also SAVED as a Kiro Crew artifact (`saved` in the answer; the "
            "player sees it in the dashboard and in the window's Library, a change is a new version). "
            "`restore: '<slug>'` (from factorio_saved_panels) brings a saved panel back."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "id, short; the same name replaces"},
                "title": {"type": "string"},
                "description": {"type": "string", "description": "one short sentence (max ~100 chars) for the saved artifact in the dashboard; not shown in the game"},
                "lua": {"type": "string"},
                "every": {"type": "integer", "description": "seconds between refreshes, 1-600"},
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"}, "label": {"type": "string"},
                            "options": {"type": "array", "items": {"type": "string"}},
                            "default": {"type": "string"},
                        },
                        "required": ["key", "options"],
                    },
                },
                "actions": {
                    "type": "object", "additionalProperties": {"type": "string"},
                    "description": "click handlers: name -> Lua body, run when the player clicks a cell {text=, action=name, args?}",
                },
                "chat": {"type": "string", "description": "the tab that asked"},
                "remove": {"type": "boolean"},
                "restore": {"type": "string", "description": "slug (or name, this run) of a saved panel to bring back; no lua needed"},
            },
        },
    },
    {
        "name": "factorio_saved_panels",
        "description": (
            "Panels saved as Kiro Crew artifacts in THIS RUN (the player's library; the window's Library "
            "button shows the same list), newest first: [{slug, title, description, updated}]. "
            "`query`: only those whose slug/title/description contain all its words; `all_runs`: every run's. "
            "Bring one back with factorio_panel(restore=slug)."
        ),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "all_runs": {"type": "boolean"}}},
    },
    {
        "name": "factorio_atlas",
        "description": (
            "Atlas of the player's base. level=map: the whole base, one line per zone (where, machines, how fed, main recipes, how many stand and why), filtered by `item` (makes or uses), `status` (stuck = anything but working/full output, or a status name like low_power; sorted by count), `feed`, `near` {x, y, r}, `min_machines` (default 3); `kind=station|train|network` lists stops, trains (schedules) or logistic networks instead. Other levels: which machines form a block (one recipe standing together), "
            "which blocks form a zone, and where every ingredient comes from (another zone by belt/"
            "inserter/chest/pipe, a train stop, or a logistic network by robots). "
            "level=zone: a zone's blocks with live statuses, its inputs with their sources, what has no "
            "source; level=block: one block with rates per second at full speed (need/make), every input's "
            "source and every output's consumer, plus `belt_supply` (lanes an input has on its belts, what they "
            "carry per second, where it was squeezed onto one lane, need vs enough); level=trace with `item` and "
            "`dir` (up: to where it is made, down: to where it is used): the item's way through blocks, train "
            "stops and the trains between them (live state, cargo, capacity), with rates at full speed and live "
            "statuses at the end, `made_per_s_at_sources` vs `needed_per_s`. Target: `id` (Z12, B480), `at` {x, y} (a click, a pointer), "
            "or `box` {x0, y0, x1, y1} (a drag pointer: the zone around it, the covered blocks marked "
            "selected). `item` keeps only what makes or uses that item. The structure is read from the game "
            "once and cached (a big base takes seconds, the game never freezes); `refresh` re-reads it after "
            "building. Statuses are always live."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["map", "zone", "block", "trace"]}, "kind": {"type": "string", "enum": ["zone", "station", "train", "network"]}, "status": {"type": "string"}, "feed": {"type": "string", "enum": ["belts", "robots", "topped"]}, "near": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "r": {"type": "number"}}}, "min_machines": {"type": "integer"}, "dir": {"type": "string", "enum": ["up", "down"]},
                "id": {"type": "string"}, "at": POS,
                "box": {"type": "object", "properties": {k: {"type": "number"} for k in ("x0", "y0", "x1", "y1")}},
                "item": {"type": "string"}, "limit": {"type": "integer"},
                "refresh": {"type": "boolean"}, "surface": {"type": "string"},
            },
        },
    },
    {
        "name": "factorio_feedback",
        "description": (
            "Send the developers of this app (Kira, the mod, the bridge) a bug report or a feature idea "
            "from the player. TWO calls, the player's yes in between: (1) without `send`: give `kind` "
            "(bug|feature), `text` (what happened or what is wanted, steps, what was expected, in the "
            "player's words plus what you found) and `chat`; it sends NOTHING and returns `draft` and "
            "`goes` (what the report holds). Show the player the text and `goes` with factorio_reply and "
            "ask for a yes. (2) Only after the player said yes in this chat: call with `send` = the draft "
            "id; it sends exactly that draft and returns `report_id`. A changed text needs a new draft."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["bug", "feature"]},
                "text": {"type": "string"},
                "chat": {"type": "string"},
                "send": {"type": "string", "description": "draft id, only after the player said yes"},
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
        if report:  # Kira's code that failed goes with it, as for Lua (docs/sentry.md)
            last = (res["stderr"].strip().splitlines() or ["python_error"])[-1]
            report.capture_message("ComputeError", last, {"part": "mcp", "tool": "factorio_compute"},
                                   [("main.py", code.encode(), "text/x-python"),
                                    ("stderr.txt", res["stderr"].encode(), "text/plain")])
    if len(json.dumps(result, ensure_ascii=False)) > COMPUTE_OUT_MAX:
        res.update(ok=False, result=None, error="result_too_large",
                   message="result over 64 KB; return less, e.g. a compact list of [x, y, name]")
    return res


def lua_long_string(s: str) -> str:
    """s as a Lua long string literal whose closing bracket does not occur inside it."""
    level = 0
    while f"]{'=' * level}]" in s:
        level += 1
    eq = "=" * level
    # a newline right after the opening bracket would be dropped by Lua; JSON never starts with one
    return f"[{eq}[{s}]{eq}]"


def panel_lua(args: dict) -> str:
    """The query that creates, replaces or removes a live panel (scripts/panels.lua via kc.panel)."""
    if args.get("remove"):
        body = json.dumps({"name": str(args.get("name", ""))}, ensure_ascii=False)
        return f"return kc.panel_remove(helpers.json_to_table({lua_long_string(body)}).name)"
    spec = {k: args[k] for k in ("name", "title", "description", "lua", "every", "inputs", "actions", "chat") if args.get(k) is not None}
    return f"return kc.panel(helpers.json_to_table({lua_long_string(json.dumps(spec, ensure_ascii=False))}))"


def panel(args: dict) -> dict:
    if args.get("restore"):
        got = library_get(str(args["restore"]))
        if not got.get("ok"):
            return got
        args = {**got["spec"], "chat": args.get("chat") or got["spec"].get("chat")}
    if not isinstance(args.get("name"), str) or not args["name"].strip():
        return {"ok": False, "error": "bad_request", "message": "name is required"}
    if not args.get("remove") and not (isinstance(args.get("lua"), str) and args["lua"].strip()):
        return {"ok": False, "error": "bad_request", "message": "lua is required (or remove: true, or restore: <saved name>)"}
    q = bridge_post("/query", {"lua": panel_lua(args), "_agent": True})
    if not q.get("ok"):
        return q  # the game or bridge refused the query itself
    res = q.get("data") if isinstance(q.get("data"), dict) else {"ok": False, "error": "unexpected", "data": q.get("data")}
    res.setdefault("ok", False)
    res["ms"] = q.get("ms")
    if res["ok"] and not args.get("remove"):
        # a working panel goes to the Kiro Crew artifacts: the player sees it in the dashboard,
        # and any save can get it back with restore
        res["saved"] = library_save(args, res)
    return res


# ── saved panels: Kiro Crew artifacts (kirocrew artifact ...), one per panel name ──

ARTIFACT_TAG = "factorio-panel"
ARTIFACT_PREFIX = "factorio-panel-"
SPEC_FENCE = "```json kc-panel"


def kirocrew_home() -> str | None:
    """The Kiro Crew home this server runs under, or None outside Kiro Crew.

    The dev stands export KIROCREW_HOME; an installed app's server gets no such env from the
    gateway, so its home is read from where it lives: <home>/apps/<app>/mcp/factorio_mcp.py.
    Not resolved through symlinks: the install path is what names the home."""
    if os.environ.get("KIROCREW_HOME"):
        return os.environ["KIROCREW_HOME"]
    mcp_dir = os.path.dirname(os.path.abspath(__file__))
    apps = os.path.dirname(os.path.dirname(mcp_dir))
    return os.path.dirname(apps) if os.path.basename(apps) == "apps" else None


def _kirocrew() -> str | None:
    """The CLI, when this server runs under a Kiro Crew gateway: the one next to the gateway's
    interpreter first (the gateway starts app servers with its own python), then PATH."""
    if not kirocrew_home() or os.environ.get("FC_NO_ARTIFACTS"):
        return None
    import shutil
    sibling = os.path.join(os.path.dirname(sys.executable), "kirocrew")
    return next((p for p in [sibling] if os.access(p, os.X_OK)), None) or shutil.which("kirocrew") or next(
        (p for p in [os.path.expanduser("~/.local/bin/kirocrew")] if os.access(p, os.X_OK)), None)


GATEWAY_PORT_FILE = Path(__file__).parents[1] / "data" / "gateway_port"  # written by the dispatcher


def gateway_port() -> str | None:
    """The port of the gateway this app runs under, for the CLI.

    The CLI otherwise dials KIROCREW_PORT or 5476, which on a machine with a second gateway (a
    desktop app on another port, another user's gateway on 5476) is not ours: every artifact call
    then fails with "gateway ownership not verified". Order: the bound port the gateway exports to
    its children, then the port the dispatcher saw (its cron env carries no port), then KIROCREW_PORT."""
    if os.environ.get("KIROCREW_BOUND_PORT", "").isdigit():
        return os.environ["KIROCREW_BOUND_PORT"]
    try:
        p = GATEWAY_PORT_FILE.read_text().strip()
        if p.isdigit():
            return p
    except OSError:
        pass
    return os.environ.get("KIROCREW_PORT") or None


def _kc(*argv: str, timeout: float = 30) -> subprocess.CompletedProcess | None:
    exe = _kirocrew()
    if not exe:
        return None
    env = {**os.environ, "KIROCREW_HOME": kirocrew_home()}
    if gateway_port():
        env["KIROCREW_PORT"] = gateway_port()
    try:
        return subprocess.run([exe, "artifact", *argv], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def _slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def run_tag(run_id) -> str:
    """The tag of one run's library (the dispatcher filters on it for the window)."""
    return "factorio-run-" + (_slugify(run_id) or "unknown")


def artifact_slug(name: str, run_id=None) -> str:
    """One artifact per panel name per run: the library is the run's, not shared between runs."""
    run = _slugify(run_id) if run_id else ""
    part = (_slugify(name) or "panel")[:60]
    if not str(name).isascii():
        # a Cyrillic name slugifies to nothing (or to its Latin bits only): two such panels would
        # share one artifact, so a short hash of the full name keeps them apart
        import hashlib
        part = part[:51] + "-" + hashlib.sha1(str(name).encode()).hexdigest()[:8]
    return ARTIFACT_PREFIX + (run + "-" if run else "") + part


def current_run_id():
    h = bridge_health()
    return h.get("run_id") if isinstance(h, dict) else None


# ── factorio_atlas: the base as blocks and zones, structure cached here, state read live ──

ATLAS_CACHE: dict[str, dict] = {}  # requested surface ("" = the player's) -> {"atlas", "run_id"}
STATUS_BATCH = 2000  # machines per status query: positions grouped by name, statuses back as numbers
STATUS_LIMIT = 20000
STATUS_LUA = '''
local P = %s
local names = {}
for k, v in pairs(defines.entity_status) do names[v] = k end
local out, codes = {}, {}
for name, xy in pairs(P) do
  local list = {}
  for i = 1, #xy, 2 do
    local e = surface.find_entity(name, {xy[i], xy[i + 1]})
    local s = e and e.status
    list[#list + 1] = s or -1
    if s then codes[tostring(s)] = names[s] end
  end
  out[name] = list
end
return {s = out, names = codes}
'''


def bridge_health() -> dict:
    try:
        with urllib.request.urlopen(BRIDGE + "/health", timeout=5) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def _plain(v) -> str:
    """A view cell as markdown text: rich-text tags dropped, [item=x] kept as x."""
    import re
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        a = abs(v)
        if a >= 1e6:
            return f"{v / 1e6:.1f}M"
        if a >= 1e4:
            return f"{v / 1e3:.1f}k"
        return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, dict):
        if "bar" in v:
            return v.get("caption") or (_plain(v["bar"]) + (f" / {_plain(v['max'])}" if v.get("max") is not None else ""))
        name = v.get("item") or v.get("fluid") or v.get("signal") or v.get("sprite")
        text = v.get("text")
        g = v.get("gps")
        if text is None and g is not None:  # a link cell without text shows its position
            if isinstance(g, dict):
                g = [g.get("x"), g.get("y")]
            elif isinstance(g, str):
                g = re.findall(r"-?[\d.]+", g)[:2]
            text = ", ".join(_plain(c) for c in g[:2]) if isinstance(g, list) else _plain(g)
        return " ".join(x for x in [str(name) if name else "", _plain(text) if text is not None else ""] if x)
    s = re.sub(r"\[(?:item|fluid|virtual-signal|entity|recipe)=([\w-]+)[^\]]*\]", r"\1", str(v if v is not None else ""))
    s = re.sub(r"\[/?(?:color|font)[^\]]*\]", "", s)
    return s.replace("|", "\\|").replace("\n", " ")


def view_markdown(view) -> str:
    out = []
    for b in view if isinstance(view, list) else []:
        if isinstance(b, dict) and "rows" in b:
            rows = b["rows"] if isinstance(b["rows"], list) else []
            if b.get("title"):
                out.append(f"**{_plain(b['title'])}**\n")
            cols = max([len(b.get("columns") or [])] + [len(r) if isinstance(r, list) else 1 for r in rows])
            if not rows:
                out.append(f"_{_plain(b.get('empty') or 'nothing')}_\n")
                continue
            head = [_plain(c) for c in (b.get("columns") or [])] + [""] * cols
            out.append("| " + " | ".join(head[:cols]) + " |")
            out.append("|" + "---|" * cols)
            for r in rows:
                cells = [_plain(c) for c in (r if isinstance(r, list) else [r])] + [""] * cols
                out.append("| " + " | ".join(cells[:cols]) + " |")
            if b.get("rows_total"):
                out.append(f"\n_{b['rows_total']} rows in all; the first {len(rows)} shown_")
            out.append("")
        else:
            out.append(_plain(b) + "\n")
    return "\n".join(out)


def panel_markdown(spec: dict, res: dict) -> str:
    inputs = spec.get("inputs") or []
    filters = "; ".join(f"{i.get('label') or i['key']}: {', '.join(i['options'])}" for i in inputs) or "none"
    chosen = ", ".join(f"{k} = {v}" for k, v in (res.get("input") or {}).items() if v) if res.get("input") else ""
    saved = {k: spec[k] for k in ("name", "title", "description", "lua", "every", "inputs", "actions") if spec.get(k) is not None}
    return "\n".join([
        f"# {spec.get('title') or spec['name']}",
        "",
        *([spec["description"], ""] if spec.get("description") else []),
        f"Live Factorio panel `{spec['name']}`, run \"{res.get('run_name') or '?'}\", saved "
        f"{time.strftime('%Y-%m-%d %H:%M')}. Refreshes every {res.get('every') or spec.get('every') or 2} s in the game. "
        f"Filters: {filters}.",
        "",
        f"To bring it back in any save, ask Kira: \"restore the panel {spec['name']}\".",
        "",
        f"## Snapshot when saved{(' (' + chosen + ')') if chosen else ''}",
        "",
        view_markdown(res.get("view")),
        "## Panel code",
        "",
        SPEC_FENCE,
        json.dumps(saved, ensure_ascii=False, indent=2),
        "```",
        "",
    ])


def library_save(spec: dict, res: dict) -> dict:
    if not _kirocrew():
        return {"ok": False, "reason": "not running under Kiro Crew"}
    run_id = res.get("run_id")
    slug = artifact_slug(spec["name"], run_id)
    title = spec.get("title") or spec["name"]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(panel_markdown(spec, res))
        path = f.name
    try:
        desc = spec.get("description") or f"Factorio live panel '{spec['name']}', run {res.get('run_name') or '?'}"
        # update first: a panel changed later becomes a new version of the same artifact
        p = _kc("update", slug, "--content-file", path, "--name", f"Factorio panel: {title}", "--description", desc)
        if p is None or p.returncode != 0:
            p = _kc("save", "--slug", slug, "--name", f"Factorio panel: {title}", "--kind", "markdown",
                    "--content-file", path, "--description", desc,
                    "--tags", ",".join([ARTIFACT_TAG] + ([run_tag(run_id)] if run_id else [])))
        if p is None or p.returncode != 0:
            return {"ok": False, "slug": slug, "reason": ((p.stderr or p.stdout).strip()[-300:] if p else "kirocrew failed")}
        return {"ok": True, "slug": slug}
    finally:
        os.unlink(path)


def library_list(query: str | None = None, all_runs: bool = False) -> dict:
    """Saved panels from the artifacts tagged factorio-panel: [{name, slug, title, description, updated}].

    `query` keeps those whose name, title or description contain every word of it (any case)."""
    run_id = None if all_runs else current_run_id()
    p = _kc("list", "--tag", run_tag(run_id) if run_id else ARTIFACT_TAG)
    if p is None:
        return {"ok": False, "error": "no_library", "message": "not running under Kiro Crew"}
    if p.returncode != 0:
        return {"ok": False, "error": "kirocrew_failed", "message": (p.stderr or p.stdout).strip()[-300:]}
    import re
    found = []
    for line in p.stdout.splitlines():
        m = re.search(r"(factorio-panel-[a-z0-9-]+)", line)
        if m and m.group(1) not in [x["slug"] for x in found]:
            found.append({"slug": m.group(1)})

    def meta(x):
        p = _kc("show", x["slug"], "--meta")
        try:
            d = json.loads(p.stdout) if p and p.returncode == 0 else {}
        except ValueError:
            d = {}
        title = (d.get("name") or "").removeprefix("Factorio panel: ")
        return {**x, "title": title or x["slug"], "description": d.get("description") or "",
                "updated": d.get("updated_at") or ""}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = list(pool.map(meta, found))
    if query:
        words = query.lower().split()
        found = [x for x in found
                 if all(w in f"{x['slug']} {x['title']} {x['description']}".lower() for w in words)]
    found.sort(key=lambda x: x["updated"], reverse=True)
    return {"ok": True, "panels": found}


def library_get(name: str) -> dict:
    slug = name if name.startswith(ARTIFACT_PREFIX) else artifact_slug(name, current_run_id())
    p = _kc("show", slug)
    if p is None:
        return {"ok": False, "error": "no_library", "message": "not running under Kiro Crew"}
    if p.returncode != 0:
        listed = library_list()
        return {"ok": False, "error": "not_saved", "message": f"no saved panel {slug}",
                "saved": [x["slug"] for x in listed.get("panels", [])]}
    text = p.stdout
    start = text.find(SPEC_FENCE)
    end = text.find("\n```", start + len(SPEC_FENCE)) if start >= 0 else -1
    if start < 0 or end < 0:
        return {"ok": False, "error": "bad_artifact", "message": f"{slug} has no panel code block"}
    try:
        spec = json.loads(text[start + len(SPEC_FENCE):end])
    except ValueError as e:
        return {"ok": False, "error": "bad_artifact", "message": f"{slug}: {e}"}
    return {"ok": True, "slug": slug, "spec": spec}


# ── feedback to the developers (docs/sentry.md, T4) ──────────────────────
# A draft first, the player's yes, then the send: the draft is kept on disk (the server can
# re-exec itself between the two calls), and the send takes the draft as it was shown.

FEEDBACK_GOES = ("the text above, the versions of the app, the mod and the game, the bridge's state and recent "
                 "log, and this tab's chat with Kira (questions, answers, tool calls). Only the developers see it.")
DRAFT_MAX_AGE = 24 * 3600


def _drafts_dir() -> Path:
    home = kirocrew_home() or os.path.expanduser("~/.kiro/crew")
    return Path(home) / "factorio" / "feedback-drafts"


def _tab_chat(home: str | None, chat: str) -> bytes | None:
    """The end of the tab's chat transcript, found through the dispatcher's state."""
    if not home:
        return None
    try:
        st = json.loads((Path(home) / "factorio" / "dispatch-state.json").read_text())
    except (OSError, ValueError):
        return None
    run = current_run_id()
    slot = (st.get("slots") or {}).get(f"{run}\t{chat}")
    if not slot:
        return None
    try:
        data = (Path(home) / "sessions" / f"dashboard_{slot}.jsonl").read_bytes()
    except OSError:
        return None
    return data[-2 * 1024 * 1024:]


def feedback(args: dict) -> dict:
    if not report:
        return {"ok": False, "error": "unavailable", "message": "error reports are not installed (report/ is missing)"}
    d = _drafts_dir()
    if args.get("send"):
        draft_id = str(args["send"])
        if not draft_id.isalnum():
            return {"ok": False, "error": "bad_request", "message": "send must be a draft id"}
        p = d / f"{draft_id}.json"
        try:
            draft = json.loads(p.read_text())
        except (OSError, ValueError):
            return {"ok": False, "error": "no_draft", "message": f"no draft {draft_id}: make one first (call without send)"}
        if time.time() - draft["created"] > DRAFT_MAX_AGE:
            return {"ok": False, "error": "draft_expired", "message": "the draft is over a day old: make a new one"}
        try:
            health = json.loads(urllib.request.urlopen(BRIDGE + "/health", timeout=5).read())
        except (OSError, ValueError):
            health = {"error": "the bridge did not answer"}
        try:
            recent = json.loads(urllib.request.urlopen(BRIDGE + "/recent_log", timeout=5).read()).get("lines") or []
        except (OSError, ValueError):
            recent = []
        info = {k: draft.get(k) for k in ("kind", "chat", "created")}
        info.update(app_version=app_version() or STARTED_VERSION, mod_version=health.get("mod_version"),
                    run_id=health.get("run_id"), run_name=health.get("run_name"))
        files = {"report.json": json.dumps(info, ensure_ascii=False, indent=1).encode(),
                 "bridge-health.json": json.dumps(health, ensure_ascii=False, indent=1).encode(),
                 "bridge.log": "\n".join(recent).encode(),
                 "chat.jsonl": _tab_chat(kirocrew_home(), draft["chat"])}
        res = report.bug_report(f"[{draft['kind']}] {draft['text']}", files, "kira", {"kind": draft["kind"], "by": "kira"})
        if res.get("ok"):
            p.unlink(missing_ok=True)
        return res
    kind, text, chat = args.get("kind"), args.get("text"), args.get("chat")
    if kind not in ("bug", "feature") or not isinstance(text, str) or not text.strip() or not isinstance(chat, str):
        return {"ok": False, "error": "bad_request", "message": "kind (bug|feature), text and chat are required"}
    import uuid
    draft_id = uuid.uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.json"):  # drafts nobody confirmed
        try:
            if time.time() - old.stat().st_mtime > DRAFT_MAX_AGE:
                old.unlink()
        except OSError:
            pass
    (d / f"{draft_id}.json").write_text(json.dumps(
        {"kind": kind, "text": text.strip()[:4000], "chat": chat, "created": time.time()}, ensure_ascii=False))
    return {"ok": True, "draft": draft_id, "sent": False, "text": text.strip()[:4000], "goes": FEEDBACK_GOES,
            "next": "show the player the text and what goes, ask for a yes; after the yes call again with "
                    f"send=\"{draft_id}\""}


def _atlas(args: dict) -> tuple[object, dict | None]:
    """The cached atlas for the requested surface, re-read on refresh or when another save runs."""
    from kc import atlas as kc_atlas
    key = args.get("surface") or ""
    health = bridge_health()
    run_id = health.get("run_id")
    c = ATLAS_CACHE.get(key)
    if c is not None and c["run_id"] != run_id:
        c = None
    # the bridge keeps the structure and re-reads only the chunks that changed; it answers
    # "unchanged" when what we hold is current
    t0 = time.monotonic()
    body = {"surface": args.get("surface"), "_agent": True, "refresh": bool(args.get("refresh"))}
    if c is not None:
        body["have_version"] = c["version"]
    r = bridge_post("/atlas", body, timeout=330)
    if not r.get("ok"):
        return None, {"ok": False, "error": r.get("error") or "atlas_failed", "message": r.get("message"),
                      "detail": {k: v for k, v in r.items() if k != "text"}}
    if not r.get("unchanged") or c is None:
        if "text" not in r:
            return None, {"ok": False, "error": "atlas_failed", "message": "the bridge sent no structure"}
        try:
            a = kc_atlas.Atlas(r["text"])
        except ValueError as e:
            return None, {"ok": False, "error": "atlas_bad_dump", "message": str(e)}
        c = {"atlas": a, "run_id": run_id, "version": r.get("version")}
        ATLAS_CACHE[key] = c
    c.update(read_s=round(time.monotonic() - t0, 2), how=r.get("how"), chunks_read=r.get("chunks_read"))
    c["tick_now"] = health.get("tick")
    return c, None


def _live_status(atlas, machines: list) -> dict:
    """machine entity id -> live status name, read in batches (a whole base is a few queries)"""
    out = {}
    machines = machines[:STATUS_LIMIT]
    for k in range(0, len(machines), STATUS_BATCH):
        batch = machines[k:k + STATUS_BATCH]
        by_name: dict[str, list] = {}
        for m in batch:
            by_name.setdefault(m["name"], []).append(m)
        lua_tab = "{" + ",".join("[%s]={%s}" % (json.dumps(n), ",".join(f"{m['x']},{m['y']}" for m in ms))
                                 for n, ms in by_name.items()) + "}"
        q = bridge_post("/query", {"lua": STATUS_LUA % lua_tab, "surface": atlas.surface, "_agent": True})
        if not q.get("ok") or not isinstance(q.get("data"), dict):
            continue
        codes = q["data"].get("names") or {}
        got = q["data"].get("s") or {}
        for n, ms in by_name.items():
            vals = got.get(n) or []
            if isinstance(vals, dict):
                vals = []
            for m, v in zip(ms, vals):
                out[m["id"]] = "gone" if v == -1 else codes.get(str(v), "?")
    return out


def atlas_tool(args: dict) -> dict:
    c, err = _atlas(args)
    if err:
        return err
    a = c["atlas"]
    level = args.get("level") or "zone"
    asked = args.get("item")
    if asked:
        item, candidates = a.resolve_item(asked)
        if item is None:
            return {"ok": False, "error": "unknown_item", "message": f"no item {asked!r} in this base",
                    "candidates": candidates}
        args = dict(args, item=item)
    resolved = {"item": args["item"]} if asked and args["item"] != asked else {}
    limit = max(1, min(int(args.get("limit") or 40), 200))
    tid, at, box = args.get("id"), args.get("at"), args.get("box")
    if level == "map":
        kind = args.get("kind") or "zone"
        status = args.get("status")
        live = None
        if kind == "zone" and (status or args.get("live", True)):
            live = _live_status(a, [m for b in a.blocks for m in b["machines"]])
        near = args.get("near") if isinstance(args.get("near"), dict) else None
        view = a.map_view(kind=kind, item=args.get("item"), status=status, feed=args.get("feed"), near=near,
                          min_machines=int(args.get("min_machines") or (3 if kind == "zone" else 1)),
                          limit=limit if args.get("limit") else 60, live=live)
        view.update(ok=True, surface=a.surface)
        if resolved:
            view["resolved_item"] = resolved["item"]
        if c.get("tick_now") is not None and a.tick is not None:
            view["structure_age_s"] = max(0, round((c["tick_now"] - a.tick) / 60))
        return view
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
    if level == "trace":
        if bi is None or not args.get("item"):
            return {"ok": False, "error": "bad_request",
                    "message": "level=trace needs a block (id B<n>, at or box) and an item"}
        view = trace_view(a, bi, args["item"], args.get("dir") or "up")
    elif level == "block":
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
    if resolved:
        view["resolved_item"] = resolved["item"]
    if c.get("tick_now") is not None and a.tick is not None:
        view["structure_age_s"] = max(0, round((c["tick_now"] - a.tick) / 60))
    return view


TRAINS_LUA = '''
local want, item = %s, %s
local names = {}
for k, v in pairs(defines.train_state) do names[v] = k end
local stack = item and prototypes.item[item] and prototypes.item[item].stack_size
local out = {}
for _, tr in pairs(game.train_manager.get_trains{surface = surface}) do
  if want[tr.id] then
    local items = {}
    for _, x in pairs(tr.get_contents()) do items[x.name] = (items[x.name] or 0) + x.count end
    local slots = 0
    for _, w in pairs(tr.cargo_wagons) do slots = slots + #w.get_inventory(defines.inventory.cargo_wagon) end
    local st = tr.station
    local fs = tr.front_stock
    out[tostring(tr.id)] = {state = names[tr.state], cargo = items, at_stop = st and st.backer_name or nil,
      at = fs and {fs.position.x, fs.position.y} or nil, capacity = stack and slots * stack or nil}
  end
end
return out
'''


def _walk_trace(hop, blocks: set, trains: set):
    if hop.get("node", "").startswith("B"):
        blocks.add(int(hop["node"][1:]))
    for t in hop.get("trains", []):
        trains.add(t["train"])
        for h in t.get("next", []):
            _walk_trace(h, blocks, trains)
    for h in hop.get("next", []):
        _walk_trace(h, blocks, trains)


def _fill_trace(hop, a, status, live_trains):
    if hop.get("node", "").startswith("B") and not hop.get("seen"):
        b = a.blocks[int(hop["node"][1:])]
        st = {}
        for m in b["machines"]:
            s = status.get(m["id"], "?")
            st[s] = st.get(s, 0) + 1
        hop["status"] = dict(sorted(st.items(), key=lambda kv: -kv[1]))
    for t in hop.get("trains", []):
        t["live"] = live_trains.get(str(t["train"]))
        for h in t.get("next", []):
            _fill_trace(h, a, status, live_trains)
    for h in hop.get("next", []):
        _fill_trace(h, a, status, live_trains)


def _leaf_rate(hop, key, seen):
    """sum of make/need per second over the hops that end the way (the producers or consumers)"""
    total = 0.0
    kids = list(hop.get("next", [])) + [h for t in hop.get("trains", []) for h in t.get("next", [])]
    if not kids and key in hop and hop["node"] not in seen:
        seen.add(hop["node"])
        return hop[key]
    for h in kids:
        total += _leaf_rate(h, key, seen)
    return total


def trace_view(a, bi, item, direction):
    tree = a.trace(("B", bi), item, "down" if direction == "down" else "up")
    blocks, trains = set(), set()
    _walk_trace(tree, blocks, trains)
    status = _live_status(a, a.machines_of(sorted(blocks)))
    live = {}
    if trains:
        want = "{" + ",".join(f"[{t}]=true" for t in sorted(trains)) + "}"
        q = bridge_post("/query", {"lua": TRAINS_LUA % (want, json.dumps(item)), "surface": a.surface, "_agent": True})
        if q.get("ok") and isinstance(q.get("data"), dict):
            live = q["data"]
    _fill_trace(tree, a, status, live)
    key = "make_per_s" if direction != "down" else "need_per_s"
    view = {"trace": item, "dir": "down" if direction == "down" else "up", "from": f"B{bi}", "way": tree}
    if direction != "down":
        view["needed_per_s"] = tree.get("need_per_s")
        view["made_per_s_at_sources"] = round(_leaf_rate(tree, key, set()), 3)
    else:
        view["made_per_s"] = tree.get("make_per_s")
        view["needed_per_s_at_consumers"] = round(_leaf_rate(tree, key, set()), 3)
    return view


def call_tool(name: str, args: dict) -> dict:
    if name == "factorio_compute":
        return compute(args or {})
    if name == "factorio_panel":
        return panel(args or {})
    if name == "factorio_saved_panels":
        return library_list((args or {}).get("query"), bool((args or {}).get("all_runs")))
    if name == "factorio_atlas":
        return atlas_tool(args or {})
    if name == "factorio_feedback":
        return feedback(args or {})
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
        try:
            res = call_tool(p.get("name"), p.get("arguments") or {})
        except Exception as e:  # a broken tool answers with an error instead of killing the server
            if report:
                report.capture_exception(e, {"part": "mcp", "tool": str(p.get("name"))})
            res = {"ok": False, "error": "internal", "message": f"{type(e).__name__}: {e}"[:300]}
        result = {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                  "isError": not res.get("ok", False)}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


# ── self-update: an app update replaces the files under a running server ──
# Each chat keeps its own server process for as long as the chat lives, so after an update the
# old code would keep serving. Before every tool call the server compares the installed app
# version with the one it started with; on a change it re-executes itself in the same process
# (same pid, same stdio, so the chat's connection stays) and the new code handles that call.

APP_JSON = Path(__file__).parents[1] / "app.json"  # only in an installed app; the repo has app/app.json
PENDING_ENV = "FC_MCP_PENDING_FILE"


def app_version() -> str | None:
    try:
        return json.loads(APP_JSON.read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


STARTED_VERSION = app_version()


def updated() -> bool:
    now = app_version()
    return bool(STARTED_VERSION and now and now != STARTED_VERSION)


def reexec(pending: bytes) -> None:
    """Start the new code in this process. `pending` is the request being handled plus whatever
    the client already sent after it; the new process reads it before stdin."""
    fd, path = tempfile.mkstemp(prefix="factorio-mcp-pending-")
    with os.fdopen(fd, "wb") as f:
        f.write(pending)
    sys.stdout.flush()
    sys.stderr.write(f"factorio MCP: app updated {STARTED_VERSION} -> {app_version()}, restarting in place\n")
    sys.stderr.flush()
    os.environ[PENDING_ENV] = path
    # orig_argv keeps the interpreter flags (the gateway starts us with -s); it is 3.10+, and the
    # server may run on macOS's /usr/bin/python3 3.9
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    os.execv(sys.executable, [sys.executable, *argv[1:]])


def pending_input() -> bytes:
    """What the previous code of this process had read but not handled (see reexec)."""
    path = os.environ.pop(PENDING_ENV, None)
    if not path:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> None:
    if report:
        report.configure("mcp")
        report.install_hooks()
    # own line buffer over fd 0: on re-exec everything read so far must be handed over,
    # and sys.stdin's buffer would hide what it read ahead
    buf = pending_input()
    while True:
        nl = buf.find(b"\n")
        if nl < 0:
            chunk = os.read(0, 1 << 16)
            if not chunk:
                if not buf.strip():
                    return
                buf += b"\n"  # a last request without a newline
            buf += chunk
            continue
        raw, buf = buf[:nl], buf[nl + 1:]
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(req, dict) and req.get("method") == "tools/call" and updated():
                reexec(raw + b"\n" + buf)
            out = handle(req) if isinstance(req, dict) else \
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
