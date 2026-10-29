"""Factorio dispatcher: a script-cron of the factorycrew Kiro Crew gateway.

Takes questions the game sent to the bridge and puts each one into its chat
`Factorio/<run>/<tab>` (SPEC.md, "Dispatcher"). One wake runs a long-poll loop for
almost the whole script timeout (Kiro Crew caps it at 3600 s), then returns; the
cron fires it again within `every` seconds. Questions that arrive in between wait
in the bridge's queue.

ctx.message is optional JSON overriding CONFIG keys, e.g.
  {"bridge": "http://127.0.0.1:34198", "run_seconds": 3500}

Two private ScriptContext calls are used, both behind `_private_post` so a Kiro Crew
update that breaks them fails with a clear message:
  * POST /api/chat/folders with parent_id: the public create_session_folder() has no
    parent (github.com/kirodotdev/KiroCrew/issues/25210);
  * POST /api/chat/slots with a title separate from the key: open_session() sends one
    string as both, and slot keys are global, so two runs' "Logistics" would share a slot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = {
    "bridge": "http://127.0.0.1:34198",
    "agent": "factorio-companion",
    "root_folder": "Factorio",
    "run_seconds": 3500,  # stay under the 3600 s script timeout
    "poll_wait": 25,
    "max_attempts": 3,  # then tell the game and drop the question
    "state_file": "",  # default: <KIROCREW_HOME>/factorio/dispatch-state.json
}


class DispatchError(RuntimeError):
    pass


# ── bridge ───────────────────────────────────────────────────────────────


def bridge(cfg, method, path, body=None, timeout=40.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(cfg["bridge"] + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── state ────────────────────────────────────────────────────────────────


def state_path(cfg) -> Path:
    if cfg["state_file"]:
        return Path(cfg["state_file"])
    home = os.environ.get("KIROCREW_HOME") or str(Path.home() / ".kiro" / "crew")
    return Path(home) / "factorio" / "dispatch-state.json"


def load_state(path: Path) -> dict:
    try:
        s = json.loads(path.read_text())
    except (OSError, ValueError):
        s = {}
    s.setdefault("root_folder", "")
    s.setdefault("runs", {})  # run_id -> {"folder": id, "name": str}
    s.setdefault("slots", {})  # "<run_id>\t<tab>" -> slot key
    s.setdefault("attempts", {})  # ask id -> failures
    s.setdefault("gen", {})  # "<run_id>\t<tab>" -> slot key generation (bumped on not_creator)
    return s


def save_state(path: Path, s: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


# ── Kiro Crew ────────────────────────────────────────────────────────────


def _private_post(ctx, path, body):
    post = getattr(ctx, "_post", None)
    if post is None:
        raise DispatchError(f"ScriptContext._post is gone (Kiro Crew update?); cannot call {path}")
    result = post(path, body)
    if not isinstance(result, dict) or "error" in result:
        raise DispatchError(f"{path} refused: {result!r}"[:300])
    return result


def find_folder(ctx, name, parent_id):
    for f in ctx.list_session_folders():
        if f.get("name") == name and str(f.get("parent_id") or "") == parent_id:
            return f["id"]
    return None


def ensure_folder(ctx, name, parent_id=""):
    fid = find_folder(ctx, name, parent_id)
    if fid:
        return fid
    try:
        return _private_post(ctx, "/api/chat/folders", {"name": name, "parent_id": parent_id})["id"]
    except DispatchError:
        # lost a race with another creator, or the duplicate-name refusal: look again
        fid = find_folder(ctx, name, parent_id)
        if fid:
            return fid
        raise


def folder_exists(ctx, fid):
    return any(f.get("id") == fid for f in ctx.list_session_folders())


def run_folder(ctx, cfg, st, run_id, run_name):
    root = st["root_folder"]
    if not root or not folder_exists(ctx, root):
        root = st["root_folder"] = ensure_folder(ctx, cfg["root_folder"])
    run = st["runs"].get(run_id)
    if run and folder_exists(ctx, run["folder"]):
        return run["folder"]
    # Folder name carries the run name; the key is run_id, so a renamed run keeps its folder.
    name = run_name or run_id
    taken = {f.get("name") for f in ctx.list_session_folders() if str(f.get("parent_id") or "") == root}
    if name in taken and not (run and run.get("name") == name):
        name = f"{name} · {run_id[-6:]}"  # two different runs that the player named alike
    fid = ensure_folder(ctx, name, root)
    st["runs"][run_id] = {"folder": fid, "name": name}
    return fid


def slot_key(run_id, tab, gen=0):
    h = hashlib.sha1(f"{run_id}\t{tab}".encode()).hexdigest()[:12]
    return f"factorio-{h}" if not gen else f"factorio-{h}-{gen}"


def open_chat(ctx, cfg, st, ask):
    run_id, tab = ask["run_id"], ask["chat"]
    k = f"{run_id}\t{tab}"
    if k in st["slots"]:
        return st["slots"][k]
    folder = run_folder(ctx, cfg, st, run_id, ask.get("run_name"))
    res = _private_post(ctx, "/api/chat/slots", {
        "name": slot_key(run_id, tab, st["gen"].get(k, 0)), "title": tab, "folder_id": folder,
        "agent": cfg["agent"],
    })
    slot = res["key"]
    ctx.set_session_mode(slot, "trust_reads")
    st["slots"][k] = slot
    return slot


SCRIPTS = (  # (name used in the header, unicode ranges)
    ("Russian or another Cyrillic language", ((0x0400, 0x04FF),)),
    ("Chinese", ((0x4E00, 0x9FFF),)),
    ("Japanese", ((0x3040, 0x30FF),)),
    ("Korean", ((0xAC00, 0xD7AF),)),
)


def reply_language(text):
    """The language the answer must be in, from the letters of the question itself.

    The model kept answering an English question in Russian because the run was named "Ран 1"
    (and its memory is partly Russian); a rule about it did not hold, a direct instruction does.
    Returns None when the letters do not decide it."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    for name, ranges in SCRIPTS:
        n = sum(1 for ch in letters if any(a <= ord(ch) <= b for a, b in ranges))
        if n * 2 >= len(letters):
            return name
    if all(ch.isascii() for ch in letters):
        return "English"
    return None


def render(ask):
    lang = reply_language(ask["text"])
    reply_in = f" · reply in {lang}" if lang else ""
    where = ""
    p = ask.get("player")
    if isinstance(p, dict) and "x" in p and "y" in p:
        who = f"player {p['name']} " if p.get("name") else "player "
        where = f" · {who}at ({p['x']}, {p['y']}) on {p.get('surface') or '?'}"
    head = (f"[Factorio · run \"{ask.get('run_name') or ask['run_id']}\" · tab \"{ask['chat']}\" · "
            f"tick {ask.get('tick')}{where}{reply_in}]")
    lines = [render_pointer(p) for p in ask.get("pointers") or [] if isinstance(p, dict)]
    return "\n".join([head, *lines, ask["text"]])


def _num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _counts(items, key="n"):
    return ", ".join(f"{_num(i.get(key))} {i.get('name')}" for i in items or [] if isinstance(i, dict))


def render_pointer(p):
    """One line per pointer the player made with the tool (mod/kc-companion/scripts/pointers.lua)."""
    on = f" on {p.get('surface') or '?'}"
    if p.get("kind") == "box":
        x0, y0, x1, y1 = (p.get("area") or [0, 0, 0, 0])[:4]
        s = f"[@{p.get('n')}: box ({x0}, {y0})-({x1}, {y1}), {p.get('w')}×{p.get('h')}{on}; {p.get('count', 0)} entities"
        if p.get("counts"):
            s += f": {_counts(p['counts'])}"
        if p.get("recipes"):
            s += f"; recipes: {_counts(p['recipes'])}"
        if p.get("resources"):
            s += "; ore: " + ", ".join(f"{r.get('name')} {_num(r.get('amount'))}" for r in p["resources"])
        return s + "]"
    s = f"[@{p.get('n')}: click at ({_num(p.get('x'))}, {_num(p.get('y'))}){on}"
    e = p.get("entity")
    if isinstance(e, dict):
        name = f"{e.get('ghost')} ghost" if e.get("ghost") else e.get("name")
        s += f"; {name} ({e.get('type')}) at ({_num(e.get('x'))}, {_num(e.get('y'))})"
        for k, label in (("recipe", "recipe"), ("mining", "mining"), ("amount", "amount"), ("status", "status")):
            if e.get(k) is not None:
                s += f", {label} {_num(e[k])}"
        if p.get("resource"):
            s += f", on {p['resource']}"
    else:
        s += f"; empty ground ({p.get('tile') or '?'})"
    return s + "]"


# ── loop ─────────────────────────────────────────────────────────────────


# ── live trace of a running turn ─────────────────────────────────────────
#
# The bridge sees only tool calls. What Kira writes between them is in the chat transcript,
# which the gateway appends to as the turn runs. While a turn is on, follow that file and send
# each new step to the tab. The transcript is Kiro Crew's own storage, read-only here; if its
# shape changes the trace stops and nothing else breaks.

TOOL_LABELS = {
    "factorio_query": "running a Lua query",
    "factorio_mark": "placing map marks",
    "factorio_unmark": "removing marks",
    "factorio_list_marks": "listing marks",
}
TRACE_MAX = 220  # chars per line in the window
TRACE_IDLE = 300.0  # stop following a chat after this long without new lines


def transcript_path(slot):
    home = os.environ.get("KIROCREW_HOME") or str(Path.home() / ".kiro" / "crew")
    return Path(home) / "sessions" / f"dashboard_{slot}.jsonl"


def trace_lines(row):
    """(kind, text, done) for one transcript row; done=True once the answer went to the game."""
    role, content = row.get("role"), row.get("content")
    if role == "assistant" and isinstance(content, str) and content.strip():
        text = " ".join(content.split())
        return [("text", text if len(text) <= TRACE_MAX else text[:TRACE_MAX - 1] + "…", False)]
    if role == "tool":
        meta = row.get("meta") or {}
        name = str(content or "").rsplit("/", 1)[-1].strip()
        if name == "factorio_reply":
            return [("", "", True)]
        if name == "factorio_progress":
            return []  # already shown as the activity line
        if not name.startswith("factorio_") and not meta.get("mcp_server"):
            return []
        label = TOOL_LABELS.get(name) or name
        return [("tool", label, False)] if label else []
    return []


def turn_ended(row):
    """The gateway writes turn_stats on the last assistant message of a turn."""
    return row.get("role") == "assistant" and bool((row.get("meta") or {}).get("turn_stats"))


class Tracer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.watch = {}  # slot -> {"chat", "run_id", "pos", "seen"}

    def start(self, slot, ask):
        p = transcript_path(slot)
        pos = p.stat().st_size if p.exists() else 0  # only what this turn adds
        self.watch[slot] = {"chat": ask["chat"], "run_id": ask["run_id"], "pos": pos, "seen": time.monotonic(),
                            "last_text": ""}

    def forward(self, w):
        """The turn ended without factorio_reply (a light model under "auto" once answered "how are
        you?" in the chat only, and the window kept "thinking"): send its last text to the tab."""
        text = w["last_text"].strip()
        if not text:
            return
        try:
            bridge(self.cfg, "POST", "/reply", {"chat": w["chat"], "text": text}, timeout=10)
            print(f"turn for [{w['chat']}] ended without factorio_reply; forwarded its last text")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"could not forward the answer for [{w['chat']}]: {exc}")

    def poll(self):
        for slot, w in list(self.watch.items()):
            p = transcript_path(slot)
            try:
                with open(p, "rb") as f:
                    f.seek(w["pos"])
                    data = f.read()
            except OSError:
                data = b""
            # only whole lines; a half-written one waits for the next poll
            end = data.rfind(b"\n") + 1
            w["pos"] += end
            done = False
            for raw in data[:end].splitlines():
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if turn_ended(row):
                    # the turn's last message carries turn_stats; no factorio_reply came before it
                    content = row.get("content")
                    if isinstance(content, str) and content.strip():
                        w["last_text"] = content
                    self.forward(w)
                    done = True
                    break
                if row.get("role") == "assistant" and isinstance(row.get("content"), str) and row["content"].strip():
                    w["last_text"] = row["content"]
                for kind, text, fin in trace_lines(row):
                    if fin:
                        done = True
                        break
                    w["seen"] = time.monotonic()
                    try:
                        bridge(self.cfg, "POST", "/trace", {"chat": w["chat"], "kind": kind, "text": text}, timeout=5)
                    except (urllib.error.URLError, OSError, ValueError):
                        pass
                if done:
                    break
            if done or time.monotonic() - w["seen"] > TRACE_IDLE:
                del self.watch[slot]


# ── the run's panel library: saved panels opened from the game without a model ──
#
# Kira's MCP server saves every working panel as a Kiro Crew artifact (mcp/factorio_mcp.py,
# library_save): slug factorio-panel-<run>-<name>, tags factorio-panel and factorio-run-<run>,
# the panel code in a fenced `json kc-panel` block. The window asks for the run's list, opens one
# or deletes one; this wake answers through the bridge. The same names are used on both sides
# (tests/unit/test_dispatch.py checks that they agree).

ARTIFACT_PREFIX = "factorio-panel-"
SPEC_FENCE = "```json kc-panel"
LIST_LINE = re.compile(r"^\s*(factorio-panel-[a-z0-9-]+)\s+v\d+\s+\S+\s+\[[^\]]*\]\s+(.*)$")


class LibraryError(RuntimeError):
    pass


def run_tag(run_id):
    return "factorio-run-" + (re.sub(r"[^a-z0-9]+", "-", str(run_id).lower()).strip("-") or "unknown")


def artifact_cli(*argv, timeout=30):
    exe = shutil.which("kirocrew") or os.path.expanduser("~/.local/bin/kirocrew")
    try:
        p = subprocess.run([exe, "artifact", *argv], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LibraryError(f"kirocrew artifact {argv[0]}: {exc}") from exc
    if p.returncode != 0:
        raise LibraryError(((p.stderr or p.stdout).strip() or f"kirocrew artifact {argv[0]} failed")[-200:])
    return p.stdout


def library_list(run_id):
    """[{slug, title}] of the run's saved panels, in the CLI's order."""
    out = []
    for line in artifact_cli("list", "--tag", run_tag(run_id)).splitlines():
        m = LIST_LINE.match(line)
        if m:
            out.append({"slug": m.group(1), "title": m.group(2).strip().removeprefix("Factorio panel: ")})
    return out


def library_spec(slug):
    text = artifact_cli("show", slug)
    start = text.find(SPEC_FENCE)
    end = text.find("\n```", start + len(SPEC_FENCE)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise LibraryError(f"{slug} holds no panel code")
    try:
        spec = json.loads(text[start + len(SPEC_FENCE):end])
    except ValueError as exc:
        raise LibraryError(f"{slug}: bad panel code ({exc})") from exc
    if not isinstance(spec, dict) or not spec.get("name") or not spec.get("lua"):
        raise LibraryError(f"{slug}: the panel code has no name or lua")
    return spec


def library(cfg, ask):
    """Answer one library request: list, open (the saved spec) or delete (then the new list).

    Only artifacts of this run's list are opened or deleted, so the window cannot reach any other
    artifact of the gateway."""
    op, run_id, slug = ask.get("op") or "list", ask.get("run_id"), ask.get("slug")
    res = {"id": ask["id"], "run_id": run_id, "op": op, "ok": True}
    try:
        if op in ("open", "delete"):
            listed = library_list(run_id)
            if not isinstance(slug, str) or slug not in {p["slug"] for p in listed}:
                raise LibraryError(f"no saved panel {slug} in this run")
            if op == "open":
                res["spec"] = library_spec(slug)
            else:
                artifact_cli("delete", slug)
                res["panels"] = [p for p in listed if p["slug"] != slug]
        else:
            res["panels"] = library_list(run_id)
    except LibraryError as exc:
        res.update(ok=False, error=str(exc))
    try:
        bridge(cfg, "POST", "/library_result", res, timeout=10)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"library {op}: could not answer the game: {exc}")
    return res


def handle(ctx, cfg, st, ask, tracer=None):
    try:
        slot = open_chat(ctx, cfg, st, ask)
        try:
            ctx.send_to_session(slot, render(ask))
        except Exception as exc:  # noqa: BLE001
            if "not_creator" not in str(exc):
                raise
            # A chat only takes messages from the cron job that opened it, and an app cron gets a
            # new id on every reinstall or off/on. That chat belongs to a previous install: open
            # a fresh one for this tab (next key generation, same title and folder) and resend.
            k = f"{ask['run_id']}\t{ask['chat']}"
            print(f"chat {slot} was opened by an earlier cron job; opening a new one for [{ask['chat']}]")
            st["slots"].pop(k, None)
            st["gen"][k] = st["gen"].get(k, 0) + 1
            slot = open_chat(ctx, cfg, st, ask)
            ctx.send_to_session(slot, render(ask))
        if tracer is not None:
            tracer.start(slot, ask)
    except Exception as exc:  # noqa: BLE001 - every failure is retried, then reported
        n = st["attempts"].get(ask["id"], 0) + 1
        st["attempts"][ask["id"]] = n
        print(f"ask {ask['id']} attempt {n} failed: {exc}")
        if n < cfg["max_attempts"]:
            return False  # not confirmed: the lease puts it back
        bridge(cfg, "POST", "/reply", {
            "chat": ask["chat"], "text": f"Could not pass the question to Kiro Crew: {str(exc)[:200]}"})
    st["attempts"].pop(ask["id"], None)
    bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
    return True


def source_stamp():
    """mtimes of this script and the app config; a wake reads both once, so a change needs a new wake."""
    stamp = []
    for p in ("__file__", "config"):
        try:
            path = __file__ if p == "__file__" else Path(__file__).resolve().parents[1] / "data" / "config.json"
            stamp.append(os.stat(path).st_mtime_ns)
        except (NameError, OSError):
            stamp.append(None)
    return tuple(stamp)


def app_config() -> dict:
    """Overrides from <app>/data/config.json when running as a Kiro Crew app cron."""
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "data" / "config.json").read_text())
    except (NameError, OSError, ValueError):
        return {}
    out = {}
    if "http_port" in cfg:
        out["bridge"] = f"http://127.0.0.1:{int(cfg['http_port'])}"
    for k in ("agent", "state_file"):
        if k in cfg:
            out[k] = cfg[k]
    return out


def run(ctx):
    cfg = dict(CONFIG)
    cfg.update(app_config())
    if ctx.message.strip().startswith("{"):
        cfg.update(json.loads(ctx.message))
    path = state_path(cfg)
    st = load_state(path)
    deadline = time.monotonic() + float(cfg["run_seconds"])
    handled = 0
    parent = os.getppid()
    stamp = source_stamp()
    tracer = Tracer(cfg)
    while time.monotonic() < deadline:
        tracer.poll()
        if os.getppid() != parent:
            # The gateway that started this wake is gone (stopped or restarted) and does not
            # reap script children: exit, or two dispatchers end up sharing the queue.
            print("gateway gone, dispatcher exiting")
            return
        if source_stamp() != stamp:
            # scripts/kc.sh install put a new version in place: without this the old code
            # keeps serving for up to run_seconds (it once sent a stale chat header).
            print("dispatcher script updated, exiting so the next wake runs the new code")
            return
        # while a turn is being traced, come back every second instead of every poll_wait
        wait = max(0, min(1 if tracer.watch else cfg["poll_wait"], int(deadline - time.monotonic()) - 1))
        try:
            # lease shorter than our retry path, so a crash mid-ask returns it quickly
            asks = bridge(cfg, "GET", f"/asks?wait={wait}&lease=60", timeout=wait + 15)["asks"]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"bridge unreachable: {exc}")
            time.sleep(5)
            continue
        for ask in asks:
            if ask.get("kind") == "library":
                # each CLI call takes ~2 s: answer off the loop so questions are not held up
                bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
                threading.Thread(target=library, args=(cfg, ask), daemon=True).start()
                continue
            if handle(ctx, cfg, st, ask, tracer):
                handled += 1
            save_state(path, st)
    print(f"dispatcher wake done, {handled} asks")
