"""Factorio dispatcher: a script-cron of the factorycrew Kiro Crew gateway.

Takes questions the game sent to the bridge and puts each one into its chat
`Factorio/<run>/<tab>` (SPEC.md, "Диспетчер"). One wake runs a long-poll loop for
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
    string as both, and slot keys are global, so two runs' "Логистика" would share a slot.
"""
from __future__ import annotations

import hashlib
import json
import os
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


def slot_key(run_id, tab):
    h = hashlib.sha1(f"{run_id}\t{tab}".encode()).hexdigest()[:12]
    return f"factorio-{h}"


def open_chat(ctx, cfg, st, ask):
    run_id, tab = ask["run_id"], ask["chat"]
    k = f"{run_id}\t{tab}"
    if k in st["slots"]:
        return st["slots"][k]
    folder = run_folder(ctx, cfg, st, run_id, ask.get("run_name"))
    res = _private_post(ctx, "/api/chat/slots", {
        "name": slot_key(run_id, tab), "title": tab, "folder_id": folder, "agent": cfg["agent"],
    })
    slot = res["key"]
    ctx.set_session_mode(slot, "trust_reads")
    st["slots"][k] = slot
    return slot


def render(ask):
    return (f"[Factorio · ран «{ask.get('run_name') or ask['run_id']}» · вкладка «{ask['chat']}» · "
            f"тик {ask.get('tick')}]\n{ask['text']}")


# ── loop ─────────────────────────────────────────────────────────────────


def handle(ctx, cfg, st, ask):
    try:
        slot = open_chat(ctx, cfg, st, ask)
        ctx.send_to_session(slot, render(ask))
    except Exception as exc:  # noqa: BLE001 - every failure is retried, then reported
        n = st["attempts"].get(ask["id"], 0) + 1
        st["attempts"][ask["id"]] = n
        print(f"ask {ask['id']} attempt {n} failed: {exc}")
        if n < cfg["max_attempts"]:
            return False  # not confirmed: the lease puts it back
        bridge(cfg, "POST", "/reply", {
            "chat": ask["chat"], "text": f"Не получилось передать вопрос в Kiro Crew: {str(exc)[:200]}"})
    st["attempts"].pop(ask["id"], None)
    bridge(cfg, "POST", "/asks/done", {"ids": [ask["id"]]})
    return True


def run(ctx):
    cfg = dict(CONFIG)
    if ctx.message.strip().startswith("{"):
        cfg.update(json.loads(ctx.message))
    path = state_path(cfg)
    st = load_state(path)
    deadline = time.monotonic() + float(cfg["run_seconds"])
    handled = 0
    parent = os.getppid()
    while time.monotonic() < deadline:
        if os.getppid() != parent:
            # The gateway that started this wake is gone (stopped or restarted) and does not
            # reap script children: exit, or two dispatchers end up sharing the queue.
            print("gateway gone, dispatcher exiting")
            return
        wait = max(0, min(cfg["poll_wait"], int(deadline - time.monotonic()) - 1))
        try:
            # lease shorter than our retry path, so a crash mid-ask returns it quickly
            asks = bridge(cfg, "GET", f"/asks?wait={wait}&lease=60", timeout=wait + 15)["asks"]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"bridge unreachable: {exc}")
            time.sleep(5)
            continue
        for ask in asks:
            if handle(ctx, cfg, st, ask):
                handled += 1
            save_state(path, st)
    print(f"dispatcher wake done, {handled} asks")
