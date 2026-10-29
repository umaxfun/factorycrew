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
        "name": "factorio_panel",
        "description": (
            "Create or replace a LIVE PANEL in the game (always give `title` and a `description` the player understands): a small window the mod refreshes by itself every "
            "`every` seconds (default 2) by running your Lua body, with no further turns from you. Use it when "
            "the player asks to SHOW/WATCH/KEEP an eye on something over time (a dashboard, 'show me live', "
            "'a panel with...'). `lua` has the factorio_query environment plus `input` (current values of "
            "`inputs`, strings) and `state` (a table kept between refreshes, e.g. for deltas). It must "
            "RETURN A VIEW: a list of blocks: a string (rich text ok, [item=iron-plate], [color=red]..[/color]); "
            "{title?, columns={...}, rows={{cell,...},...}, empty?}; {bar=v, max=m, caption?, color?}. "
            "Cells: string, number (formatted 1.2k), {item=name, text?} / {fluid=} / {signal=} (icon), "
            "{bar=v, max=m, caption?, color?}, {text=, color=red|green|yellow|orange|gray|blue}. "
            "`inputs`: drop-downs for the player, e.g. [{key:'period', label:'Period', options:['1m','10m','1h'], default:'10m'}]. "
            "The body runs once now and you get its `view` (or `error`) back: CHECK the numbers against a "
            "query before replying. It runs inside a game tick on every refresh: keep it cheap (filtered "
            "finds, no full-map scans; larger `every` for heavier bodies); errors show on the panel and "
            "5 in a row pause it. Same `name` replaces the panel; `remove: true` deletes it. "
            "`kc.panels()` in factorio_query lists panels with their last error. "
            "Every panel that works is also SAVED as a Kiro Crew artifact (`saved` in the answer; the "
            "player sees it in the dashboard, a change is a new version). `restore: '<name>'` brings a saved "
            "panel back into this game; `factorio_saved_panels` lists them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "id, short; the same name replaces"},
                "title": {"type": "string"},
                "description": {"type": "string", "description": "1-2 sentences for the player: what the panel shows and how the numbers are counted"},
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
                "chat": {"type": "string", "description": "the tab that asked"},
                "remove": {"type": "boolean"},
                "restore": {"type": "string", "description": "name of a saved panel to bring back (no lua needed)"},
            },
        },
    },
    {
        "name": "factorio_saved_panels",
        "description": "Panels saved as Kiro Crew artifacts, from any save: [{name, slug}]. Bring one back with factorio_panel(restore=name).",
        "inputSchema": {"type": "object", "properties": {}},
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
    spec = {k: args[k] for k in ("name", "title", "description", "lua", "every", "inputs", "chat") if args.get(k) is not None}
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


def _kirocrew() -> str | None:
    """The CLI, when this server runs under a Kiro Crew gateway (its env names the home and port)."""
    if not os.environ.get("KIROCREW_HOME") or os.environ.get("FC_NO_ARTIFACTS"):
        return None
    import shutil
    return shutil.which("kirocrew") or next(
        (p for p in [os.path.expanduser("~/.local/bin/kirocrew")] if os.access(p, os.X_OK)), None)


def _kc(*argv: str, timeout: float = 30) -> subprocess.CompletedProcess | None:
    exe = _kirocrew()
    if not exe:
        return None
    try:
        return subprocess.run([exe, "artifact", *argv], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def artifact_slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "panel"
    return ARTIFACT_PREFIX + s[:60]


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
    saved = {k: spec[k] for k in ("name", "title", "description", "lua", "every", "inputs") if spec.get(k) is not None}
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
    slug = artifact_slug(spec["name"])
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
                    "--content-file", path, "--description", desc, "--tags", ARTIFACT_TAG)
        if p is None or p.returncode != 0:
            return {"ok": False, "slug": slug, "reason": ((p.stderr or p.stdout).strip()[-300:] if p else "kirocrew failed")}
        return {"ok": True, "slug": slug}
    finally:
        os.unlink(path)


def library_list() -> dict:
    """Saved panels: [{slug, name}] from the artifacts tagged factorio-panel."""
    p = _kc("list", "--tag", ARTIFACT_TAG)
    if p is None:
        return {"ok": False, "error": "no_library", "message": "not running under Kiro Crew"}
    if p.returncode != 0:
        return {"ok": False, "error": "kirocrew_failed", "message": (p.stderr or p.stdout).strip()[-300:]}
    import re
    found = []
    for line in p.stdout.splitlines():
        m = re.search(r"(factorio-panel-[a-z0-9-]+)", line)
        if m and m.group(1) not in [x["slug"] for x in found]:
            found.append({"slug": m.group(1), "name": m.group(1)[len(ARTIFACT_PREFIX):], "line": line.strip()})
    return {"ok": True, "panels": found}


def library_get(name: str) -> dict:
    slug = name if name.startswith(ARTIFACT_PREFIX) else artifact_slug(name)
    p = _kc("show", slug)
    if p is None:
        return {"ok": False, "error": "no_library", "message": "not running under Kiro Crew"}
    if p.returncode != 0:
        listed = library_list()
        return {"ok": False, "error": "not_saved", "message": f"no saved panel {slug}",
                "saved": [x["name"] for x in listed.get("panels", [])]}
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


def call_tool(name: str, args: dict) -> dict:
    if name == "factorio_compute":
        return compute(args or {})
    if name == "factorio_panel":
        return panel(args or {})
    if name == "factorio_saved_panels":
        return library_list()
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
