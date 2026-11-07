"""Factorio dispatcher: a script-cron of the factorycrew Kiro Crew gateway.

Takes questions the game sent to the bridge and puts each one into its chat
`Factorio/<run>/<tab>` (SPEC.md, "How it works"; docs/areas/dispatcher.md). One wake runs a long-poll loop for
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
import sys
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


def _load_report():
    """report/kc_report.py (docs/sentry.md): one level up in the repo and in the app, or next to
    this file when scripts/kc.sh installed it into <home>/crons. None when missing: the
    dispatcher runs the same without error reports."""
    import sys
    here = Path(__file__).resolve().parent
    for d in (here.parent / "report", here / "report"):
        if (d / "kc_report.py").exists():
            sys.path.insert(0, str(d))
            try:
                import kc_report
                return kc_report
            except Exception:  # noqa: BLE001
                return None
    return None


try:
    report = _load_report()
except NameError:  # no __file__ (exec'd source)
    report = None
LOG_LINES = 400
RECENT_LOG: list = []  # this wake's last printed lines, for a bug report


LOG_FILE_MAX = 1024 * 1024


def log_path() -> Path:
    """<crew home>/factorio/dispatcher.log: the lines of every wake, so a bug report also holds
    what the wakes before this one did (a turn may start under one and end under the next)."""
    home = os.environ.get("KIROCREW_HOME") or str(Path.home() / ".kiro" / "crew")
    return Path(home) / "factorio" / "dispatcher.log"


def note(line: str) -> None:
    """print() for the cron's output, and a copy for a bug report (memory and the log file)."""
    print(line)
    stamped = time.strftime("%Y-%m-%d %H:%M:%S ") + line
    RECENT_LOG.append(stamped)
    del RECENT_LOG[:-LOG_LINES]
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_FILE_MAX:
            os.replace(p, p.with_suffix(".log.prev"))
        with open(p, "a", encoding="utf-8") as f:
            f.write(stamped + "\n")
    except OSError:
        pass


def log_tail(n_bytes: int = 512 * 1024) -> bytes:
    out = b""
    for p in (log_path().with_suffix(".log.prev"), log_path()):
        try:
            out += p.read_bytes()
        except OSError:
            pass
    return out[-n_bytes:] if out else "\n".join(RECENT_LOG).encode()


def report_exception(exc: BaseException, **tags) -> None:
    if report:
        report.capture_exception(exc, {"part": "dispatcher", **tags})


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
    s.setdefault("watch", {})  # slot -> a turn being followed (Tracer.snapshot)
    s.setdefault("gen", {})  # "<run_id>\t<tab>" -> slot key generation (bumped on not_creator)
    s.setdefault("panels_backfilled", [])  # runs whose unsaved in-game panels were saved once
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
    game = f"Factorio {ask['factorio_version']}" if ask.get("factorio_version") else "Factorio"
    head = (f"[{game} · run \"{ask.get('run_name') or ask['run_id']}\" · tab \"{ask['chat']}\" · "
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
# Follow a turn until it ends (turn_stats), whatever the silence: a model thinking hard writes
# nothing for minutes, and a 300 s idle limit once lost the fallback copy of an 8-minute turn.
# This only bounds a transcript whose end never shows (a gateway crash mid-turn).
TRACE_MAX_AGE = 3 * 3600.0
REREAD = 64 * 1024  # bytes read again before the saved offset on each poll (see Tracer.poll)
SEEN_MAX = 2000  # hashes of the rows already handled, per watched turn


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


def older_than(row, since):
    """True when the row's `ts` is before `since` (epoch seconds); rows without one are kept."""
    if since is None or not isinstance(row.get("ts"), str):
        return False
    try:
        from datetime import datetime
        return datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).timestamp() < since
    except ValueError:
        return False


def turn_ended(row):
    """The gateway writes turn_stats on the last assistant message of a turn."""
    return row.get("role") == "assistant" and bool((row.get("meta") or {}).get("turn_stats"))


# A turn Kiro Crew gives up on (credits spent, model refused, turn limit, kiro-cli logged out)
# writes one `error` row and no turn_stats, so without this the window said "thinking" until the
# bridge's 20-minute backstop. Not every error row ends the turn: "⟳ … retrying" notices
# (meta.kind transient_retry, or the ⟳ mark) come before a retry, and an approval that timed out
# is declined and the turn "carried on from the denial" (Kiro Crew 2026-10-31 wording).
USAGE_LIMIT = re.compile(r"UsageLimitReached|usage limit|limit for overages|out of credits", re.I)
USAGE_LIMIT_TEXT = ("Kiro has no credits left (usage limit reached), so Kira cannot answer now. "
                    "Questions work again when the limit resets, or after you add credits or upgrade "
                    "the plan of your Kiro account.")


def turn_failed(row):
    """The text to show in the tab when `row` is the error that ended the turn, else None."""
    if row.get("role") != "error":
        return None
    content = row.get("content")
    text = " ".join(content.split()) if isinstance(content, str) else ""
    if (row.get("meta") or {}).get("kind") == "transient_retry" or text.startswith("⟳") or "carried on" in text:
        return None
    if USAGE_LIMIT.search(text):
        return USAGE_LIMIT_TEXT
    text = text.lstrip("❌⏱️ ").strip() or "unknown error"
    return "Kiro Crew could not finish this turn: " + (text if len(text) <= 300 else text[:299] + "…")


class Tracer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.watch = {}  # slot -> {"chat", "run_id", "ask_id", "pos", "started", "last_text", "since", "asked"}

    # A turn can outlive the wake that sent its question (run_seconds, a gateway restart, a new
    # dispatcher after an app update): the watches are kept in dispatch-state.json, and the next
    # wake goes on reading each transcript from where the last one stopped. Before 0.3.10 they
    # lived in memory only, and a turn that ended under the next wake never reached the game
    # (2026-11-01, gamer: both answers of a tab stayed in the chat, the window said "thinking").

    def snapshot(self) -> dict:
        return {slot: dict(w) for slot, w in self.watch.items()}

    def restore(self, saved) -> None:
        for slot, w in (saved or {}).items():
            if isinstance(w, dict) and {"chat", "run_id", "pos", "started"} <= set(w) and slot not in self.watch:
                self.watch[slot] = {"ask_id": None, "last_text": "", "since": None, "asked": False, **w}

    @staticmethod
    def offset(slot):
        """The transcript's size now; take it BEFORE the question is sent (see start)."""
        p = transcript_path(slot)
        return p.stat().st_size if p.exists() else 0

    def start(self, slot, ask, since=None, pos=None):
        """Follow the turn `ask` started. `since`: wall-clock time before the question was sent;
        `pos`: the transcript's size before it was sent (now, when not given).

        Only rows after the question's own user row count. The previous turn can still be closing
        when the question arrives: its last message (with turn_stats) is written after `since`,
        before this question's user row, and it was forwarded as this turn's answer 1.5 s after a
        quick "yes" (app e2e, consent test). The byte offset alone is not enough either: the
        gateway may rewrite the file when the question arrives, and `since` drops the old rows
        met again that way."""
        if pos is None:
            pos = self.offset(slot)
        self.watch[slot] = {"chat": ask["chat"], "run_id": ask["run_id"], "ask_id": ask.get("id"), "pos": pos,
                            "started": time.time(), "last_text": "", "since": since, "asked": False}

    def ended(self, w, failure=False):
        """The turn ended without factorio_reply: forward its last text, then tell the bridge the
        turn is over, so a turn that left no text stops "thinking" now and not at idle_after."""
        self.forward(w, failure)
        try:
            bridge(self.cfg, "POST", "/turn_end", {"chat": w["chat"], "ask_id": w.get("ask_id"),
                                                   **({"failure": True} if failure else {})}, timeout=10)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            note(f"could not report the end of the turn for [{w['chat']}]: {exc}")

    def forward(self, w, failure=False):
        """The turn ended without factorio_reply (a light model under "auto" once answered "how are
        you?" in the chat only, and the window kept "thinking"): send its last text to the tab."""
        text = w["last_text"].strip()
        if not text:
            return
        try:
            # fallback: the bridge drops it if the turn did answer. The transcript may show the
            # turn's end before its factorio_reply row, and then this copy (in the dashboard's
            # language, with the dashboard's [OPTIONS]) came on top of the real answer.
            # failure: Kiro Crew killed the turn, so no Stop hook fires and this is the only news of it
            r = bridge(self.cfg, "POST", "/reply", {"chat": w["chat"], "text": text, "fallback": True,
                                                    "ask_id": w.get("ask_id"), **({"failure": True} if failure else {})},
                       timeout=10)
            if r.get("skipped"):
                return
            note(f"turn for [{w['chat']}] ended without factorio_reply; forwarded its last text")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            note(f"could not forward the answer for [{w['chat']}]: {exc}")

    def poll(self):
        for slot, w in list(self.watch.items()):
            p = transcript_path(slot)
            # The gateway rewrites the whole file when it updates the metadata row on line 1, so
            # a byte offset can land a few bytes off: inside the question's own row, which then
            # failed to parse, and the turn was never followed (gamer, 2026-11-01: no trace, no
            # answer in the game, "thinking" until the backstop). Read from a little before the
            # offset (from the start if the file shrank) and skip rows already seen.
            try:
                with open(p, "rb") as f:
                    size = f.seek(0, os.SEEK_END)
                    start = 0 if size < w["pos"] else max(0, w["pos"] - REREAD)
                    f.seek(start)
                    data = f.read()
            except OSError:
                data, start = b"", w["pos"]
            if start > 0:
                cut = data.find(b"\n") + 1  # the line the backstep landed in is not whole
                data, start = data[cut:], start + cut
            # only whole lines; a half-written one waits for the next poll
            end = data.rfind(b"\n") + 1
            w["pos"] = start + end
            seen = w.setdefault("seen", [])
            done = False
            for raw in data[:end].splitlines():
                key = hashlib.sha1(raw).hexdigest()[:12]
                if key in seen:
                    continue
                seen.append(key)
                del seen[:-SEEN_MAX]
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if older_than(row, w.get("since")):
                    continue  # an earlier turn's row, met again after the file was rewritten
                if not w["asked"]:
                    # the previous turn's closing rows, until this question's user row appears
                    w["asked"] = row.get("role") == "user"
                    continue
                failure = turn_failed(row)
                if failure:
                    # the turn died in Kiro Crew: its half-written text is no answer, the error is
                    note(f"turn for [{w['chat']}] failed in Kiro Crew: {str(row.get('content'))[:160]}")
                    if report:
                        # Kiro Crew's own error text (credits spent, model refused, ...), not the
                        # player's: the developer sees how often turns die and why
                        report.capture_message(
                            "KiroCrewTurnError", " ".join(str(row.get("content") or "").split())[:300],
                            {"part": "dispatcher",
                             "usage_limit": str(USAGE_LIMIT.search(str(row.get("content") or "")) is not None)},
                            level="warning")
                    w["last_text"] = failure
                    self.ended(w, failure=True)
                    done = True
                    break
                if turn_ended(row):
                    # the turn's last message carries turn_stats; no factorio_reply came before it
                    content = row.get("content")
                    if isinstance(content, str) and content.strip():
                        w["last_text"] = content
                    self.ended(w)
                    done = True
                    break
                if row.get("role") == "assistant" and isinstance(row.get("content"), str) and row["content"].strip():
                    w["last_text"] = row["content"]
                for kind, text, fin in trace_lines(row):
                    if fin:
                        done = True
                        break
                    try:
                        bridge(self.cfg, "POST", "/trace", {"chat": w["chat"], "kind": kind, "text": text}, timeout=5)
                    except (urllib.error.URLError, OSError, ValueError):
                        pass
                if done:
                    break
            if done or time.time() - w["started"] > TRACE_MAX_AGE:
                del self.watch[slot]


# ── the run's panel library: saved panels opened from the game without a model ──
#
# Kira's MCP server saves every working panel as a Kiro Crew artifact (mcp/factorio_mcp.py,
# library_save): slug factorio-panel-<run>-<name>, tags factorio-panel and factorio-run-<run>,
# the panel code in a fenced `json kc-panel` block. The window asks for the run's list, opens one
# or deletes one; this wake answers through the bridge. The same names are used on both sides
# (tests/unit/test_panel_tool.py::test_dispatcher_and_mcp_agree_on_names checks that they agree).

ARTIFACT_PREFIX = "factorio-panel-"
SPEC_FENCE = "```json kc-panel"
LIST_LINE = re.compile(r"^\s*(factorio-panel-[a-z0-9-]+)\s+v\d+\s+\S+\s+\[[^\]]*\]\s+(.*)$")


class LibraryError(RuntimeError):
    pass


def run_tag(run_id):
    return "factorio-run-" + (re.sub(r"[^a-z0-9]+", "-", str(run_id).lower()).strip("-") or "unknown")


def kirocrew_exe():
    """The CLI of the gateway running this cron (its Python's own launcher), then PATH: a stale
    ~/.local/bin/kirocrew can belong to another, older install."""
    sibling = os.path.join(os.path.dirname(sys.executable), "kirocrew")
    if os.access(sibling, os.X_OK):
        return sibling
    return shutil.which("kirocrew") or os.path.expanduser("~/.local/bin/kirocrew")


def artifact_cli(*argv, timeout=30):
    """`kirocrew artifact ...` against this gateway: KIROCREW_PORT is the cron's own port, set by
    remember_gateway_port, so a second gateway on the default port is never asked."""
    try:
        # cwd: the kirocrew CLI reads its working directory on start (project-dir lookup), and a process
    # of an app that was updated runs in the deleted old install folder: FileNotFoundError
    # from os.getcwd(), and no panel was saved (gamer, 2026-11-02, after 0.4.0)
        p = subprocess.run([kirocrew_exe(), "artifact", *argv], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=os.path.expanduser("~"))
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
    except Exception as exc:  # the window waits for an answer: always send one
        res.update(ok=False, error=str(exc)[-300:] or type(exc).__name__)
    try:
        bridge(cfg, "POST", "/library_result", res, timeout=10)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        note(f"library {op}: could not answer the game: {exc}")
    return res


# ── panels already in the game but not saved (made while installs saved none) ──

PANEL_NAMES_LUA = ("local out = {} for _, p in ipairs(remote.call('kc', 'panels', false)) do "
                   "if not p.error then out[#out + 1] = p.name end end return out")
PANEL_LUA = "for _, p in ipairs(remote.call('kc', 'panels')) do if p.name == %s then return p end end"


_MCP = []


def _mcp_module():
    """The MCP server's code beside this script, loaded once: one place builds and saves a panel's
    artifact and calls the CLI."""
    if not _MCP:
        import importlib.util
        path = Path(__file__).parents[1] / "mcp" / "factorio_mcp.py"
        spec = importlib.util.spec_from_file_location("factorio_mcp_for_dispatch", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MCP.append(m)
    return _MCP[0]


def save_unsaved_panels(cfg, run_id, run_name, m=None):
    """Save every working panel of the loaded run that has no artifact yet. Returns the saved slugs."""
    m = m or _mcp_module()
    q = bridge(cfg, "POST", "/query", {"lua": PANEL_NAMES_LUA}, timeout=20)
    names = q.get("data") if q.get("ok") else None
    if not names:
        return []
    have = {p["slug"] for p in library_list(run_id)}
    saved = []
    for name in names:
        if m.artifact_slug(name, run_id) in have:
            continue
        r = bridge(cfg, "POST", "/query", {"lua": PANEL_LUA % m.lua_long_string(name)}, timeout=20)
        p = r.get("data") if r.get("ok") else None
        if not isinstance(p, dict):
            continue
        spec = {k: p[k] for k in ("name", "title", "description", "lua", "every", "inputs", "actions")
                if p.get(k) not in (None, {}, [])}
        res = {"run_id": run_id, "run_name": run_name, "every": p.get("every"), "input": p.get("input"),
               "view": p.get("view")}
        out = m.library_save(spec, res)
        if not out.get("ok"):
            # not done: the run is tried again on the next wake instead of being marked as saved
            raise LibraryError(f"panel {name}: not saved: {out.get('reason')}")
        saved.append(out["slug"])
    return saved


def panel_sync(cfg, run_id, run_name, done):
    """Once per run (dispatcher state `panels_backfilled`); `done` gets the run_id on success."""
    try:
        saved = save_unsaved_panels(cfg, run_id, run_name)
    except Exception as exc:  # a thread: anything uncaught would vanish (in a checkout there is no mcp/ beside it)
        note(f"saving the game's panels: {exc}")  # tried again on the next wake
        if not isinstance(exc, (LibraryError, urllib.error.URLError, OSError, ValueError)):
            report_exception(exc, where="panel_sync")  # a bug, not a gateway or network hiccup
        return
    if saved:
        note(f"saved {len(saved)} panels made before they were saved: {', '.join(saved)}")
    done.append(run_id)


def handle(ctx, cfg, st, ask, tracer=None):
    try:
        slot = open_chat(ctx, cfg, st, ask)
        since = time.time() - 1  # rows of this turn are written after this; a second of clock slack
        pos = Tracer.offset(slot)  # before sending: the question's own user row comes after it
        try:
            ctx.send_to_session(slot, render(ask))
        except Exception as exc:  # noqa: BLE001
            if "not_creator" not in str(exc):
                raise
            # A chat only takes messages from the cron job that opened it, and an app cron gets a
            # new id on every reinstall or off/on. That chat belongs to a previous install: open
            # a fresh one for this tab (next key generation, same title and folder) and resend.
            k = f"{ask['run_id']}\t{ask['chat']}"
            note(f"chat {slot} was opened by an earlier cron job; opening a new one for [{ask['chat']}]")
            st["slots"].pop(k, None)
            st["gen"][k] = st["gen"].get(k, 0) + 1
            slot = open_chat(ctx, cfg, st, ask)
            pos = Tracer.offset(slot)
            ctx.send_to_session(slot, render(ask))
        if tracer is not None:
            tracer.start(slot, ask, since, pos)
    except Exception as exc:  # noqa: BLE001 - every failure is retried, then reported
        n = st["attempts"].get(ask["id"], 0) + 1
        st["attempts"][ask["id"]] = n
        note(f"ask {ask['id']} attempt {n} failed: {exc}")
        if n < cfg["max_attempts"]:
            return False  # not confirmed: the lease puts it back
        report_exception(exc, where="pass_question", attempts=n)
        bridge(cfg, "POST", "/reply", {
            "chat": ask["chat"], "text": f"Could not pass the question to Kiro Crew: {str(exc)[:200]}"})
    st["attempts"].pop(ask["id"], None)
    bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
    return True


def crew_home() -> Path:
    return Path(os.environ.get("KIROCREW_HOME") or str(Path.home() / ".kiro" / "crew"))


def bug_report_files(st, ask) -> dict:
    """{name: bytes} of a report from the window: the bridge's part (report.json, its health and
    log; bridge/factorio_bridge.py, bug_report_files) plus this dispatcher's log, its state and
    the tab's whole raw session (report.session_files: the transcript, the kiro-cli conversation
    and its logs). The player saw this list in the dialog (mod/kc-companion/scripts/gui.lua)."""
    info = {k: ask.get(k) for k in ("id", "chat", "run_id", "run_name", "mod_version", "factorio_version", "tick")}
    info["dispatcher"] = {"file": __file__, "stamp": source_stamp()}
    files = {
        "report.json": json.dumps(info, ensure_ascii=False, indent=1).encode(),
        "bridge-health.json": json.dumps(ask.get("health"), ensure_ascii=False, indent=1).encode(),
        "bridge.log": (ask.get("bridge_log") or "").encode(),
        "dispatcher.log": log_tail(),
        "dispatch-state.json": json.dumps({k: st.get(k) for k in ("slots", "gen", "runs")}, ensure_ascii=False,
                                          indent=1).encode(),
    }
    slot = st.get("slots", {}).get(f"{ask.get('run_id')}\t{ask.get('chat')}")
    if report and slot:
        files.update(report.session_files(crew_home(), slot))
    elif not slot:
        files["session/missing.txt"] = f"tab {ask.get('chat')!r} has no chat yet".encode()
    if report:
        files["screenshot.png"] = report.read_screenshot(ask.get("screenshot"))
    return files


def bug_report(cfg, st, ask):
    """The window's "Report a problem": build the report, send it (or keep it for later) and tell
    the window its id."""
    if report:
        res = report.bug_report(ask.get("comment") or "", bug_report_files(st, ask), "button",
                                {"by": "dispatcher", "mod_version": ask.get("mod_version") or "?",
                                 "factorio": ask.get("factorio_version") or "?"})
    else:
        res = {"ok": False, "error": "error reports are not installed (report/ is missing)"}
    note(f"bug report {ask.get('id')}: {res.get('report_id') or res.get('error')}"
         f"{' sent' if res.get('sent') else ' kept for later' if res.get('ok') else ''}")
    try:
        bridge(cfg, "POST", "/bug_report_result", {"id": ask["id"], "run_id": ask.get("run_id"), **res}, timeout=10)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        note(f"bug report {ask.get('id')}: could not tell the game: {exc}")
    return res


def stop_turn(ctx, cfg, st, ask, tracer=None):
    """The window's Stop: end the tab's running turn the way the dashboard's Stop button does,
    then tell the bridge. A soft stop: the chat keeps what the turn did, and its next question
    runs as usual (measured on Kiro Crew 2026-10-31). A third private call: ScriptContext has no
    stop, and /api/session-control/stop refuses a cron (caller_unidentified)."""
    slot = st["slots"].get(f"{ask['run_id']}\t{ask['chat']}")
    res = {"chat": ask["chat"], "run_id": ask["run_id"], "ok": True}
    if slot:
        try:
            _private_post(ctx, f"/api/chat/slots/{slot}/stop", {})
            if tracer is not None:
                # the stopped turn's last text is half an answer: not forwarded to the tab
                tracer.watch.pop(slot, None)
        except DispatchError as exc:
            res.update(ok=False, error=str(exc)[:160])
    note(f"stop [{ask['chat']}]: {'stopped' if res['ok'] else res['error']}")
    try:
        bridge(cfg, "POST", "/stopped", res, timeout=10)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        note(f"stop [{ask['chat']}]: could not tell the bridge: {exc}")
    return res


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


def remember_gateway_port(ctx):
    """Tell the CLI calls which gateway is ours. A cron child's env names no port (the context pops
    it), so the CLI would dial 5476, another gateway on a machine with two; the MCP server reads the
    same file (its env may lack the port too)."""
    port = getattr(ctx, "_port", None)
    if not isinstance(port, int) or port <= 0:
        return
    os.environ["KIROCREW_PORT"] = str(port)
    app = Path(__file__).parents[1]
    if (app / "app.json").exists():  # an installed app, not a checkout
        try:
            (app / "data").mkdir(exist_ok=True)
            (app / "data" / "gateway_port").write_text(f"{port}\n")
        except OSError as exc:
            note(f"could not write the gateway port: {exc}")


def run(ctx):
    """The cron's entry point: one wake. A crash is reported, then raised as before."""
    if report:
        report.configure("dispatcher")
        report.install_hooks()  # threads (library, panel sync) that die uncaught
    try:
        return wake(ctx)
    except Exception as exc:
        report_exception(exc, where="wake")
        if report:
            report._drain()
        raise


def wake(ctx):
    remember_gateway_port(ctx)
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
    note(f"wake started (pid {os.getpid()})")
    tracer.restore(st.get("watch"))  # turns an earlier wake was following
    if tracer.watch:
        note(f"following {len(tracer.watch)} turn(s) started before this wake: {', '.join(w['chat'] for w in tracer.watch.values())}")
    synced = set()  # runs whose unsaved panels this wake started saving
    synced_ok = []  # runs whose saving finished (appended by the thread)
    while time.monotonic() < deadline:
        tracer.poll()
        if tracer.snapshot() != (st.get("watch") or {}):
            st["watch"] = tracer.snapshot()
            save_state(path, st)
        if os.getppid() != parent:
            # The gateway that started this wake is gone (stopped or restarted) and does not
            # reap script children: exit, or two dispatchers end up sharing the queue.
            note("gateway gone, dispatcher exiting")
            return
        if source_stamp() != stamp:
            # scripts/kc.sh install put a new version in place: without this the old code
            # keeps serving for up to run_seconds (it once sent a stale chat header).
            note("dispatcher script updated, exiting so the next wake runs the new code")
            return
        # once per wake and loaded run: panels in the game that have no artifact get one
        # (each CLI call takes ~2 s, so off the loop like the library)
        try:
            h = bridge(cfg, "GET", "/health", timeout=5)
        except (urllib.error.URLError, OSError, ValueError):
            h = {}
        while synced_ok:  # finished in the thread: never again for that run, so a panel the
            run_id = synced_ok.pop()  # player deletes from the library stays deleted
            st.setdefault("panels_backfilled", []).append(run_id)
            save_state(path, st)
        if h.get("game_online") and h.get("run_id") and h["run_id"] not in synced \
                and h["run_id"] not in st.get("panels_backfilled", []):
            synced.add(h["run_id"])
            threading.Thread(target=panel_sync, args=(cfg, h["run_id"], h.get("run_name"), synced_ok),
                             daemon=True).start()
        # while a turn is being traced, come back every second instead of every poll_wait
        wait = max(0, min(1 if tracer.watch else cfg["poll_wait"], int(deadline - time.monotonic()) - 1))
        try:
            # lease shorter than our retry path, so a crash mid-ask returns it quickly
            asks = bridge(cfg, "GET", f"/asks?wait={wait}&lease=60", timeout=wait + 15)["asks"]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            note(f"bridge unreachable: {exc}")
            time.sleep(5)
            continue
        for ask in asks:
            if ask.get("kind") == "library":
                # each CLI call takes ~2 s: answer off the loop so questions are not held up
                bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
                threading.Thread(target=library, args=(cfg, ask), daemon=True).start()
                continue
            if ask.get("kind") == "stop":
                bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
                stop_turn(ctx, cfg, st, ask, tracer)
                continue
            if ask.get("kind") == "bug_report":
                # reading the chat, a screenshot and sending take seconds: off the loop
                bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
                threading.Thread(target=bug_report, args=(cfg, dict(st), ask), daemon=True).start()
                continue
            if handle(ctx, cfg, st, ask, tracer):
                handled += 1
            save_state(path, st)
    note(f"dispatcher wake done, {handled} asks")
