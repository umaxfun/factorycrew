"""Watchdog for e2e waits: fail within seconds of a hang, and say where the chain stopped.

The chain of one question: mod -> (ack) bridge -> (lease) dispatcher -> chat in Kiro Crew ->
agent turn (MCP calls, transcript lines) -> factorio_reply -> mod feed. A plain "wait 180 s
for a reply" turns any break in it into the same silent timeout ("the game loaded and nothing
happens"). wait_reply() instead checks every link once a second and raises Stuck naming the
first one that stopped moving, with what it saw there:

  * the game stopped sending heartbeats (paused, frozen, crashed);
  * the bridge never acked the question (mod -> bridge UDP);
  * the question sits in the bridge queue (no dispatcher is polling it);
  * the dispatcher took it but no chat appeared, or the chat's turn failed (role "error" in the
    transcript, e.g. "Crew Member ... is unavailable");
  * the turn runs but nothing moves: no MCP step, no trace line, no transcript growth;
  * the bridge itself gave up (90 s without a sign of life -> "did not answer here").
"""
from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path

ACK_S = float(os.environ.get("FC_E2E_ACK_S", "10"))
PICKUP_S = float(os.environ.get("FC_E2E_PICKUP_S", "60"))
CHAT_S = float(os.environ.get("FC_E2E_CHAT_S", "45"))
SILENCE_S = float(os.environ.get("FC_E2E_SILENCE_S", "120"))
HEARTBEAT_S = 15.0  # the bridge marks the game offline after 15 s, same rule


class Stuck(AssertionError):
    pass


def _dispatch_states(kc_home: Path):
    for p in glob.glob(str(kc_home / "factorio" / "dispatch-state*.json")) + \
             glob.glob(str(kc_home / "apps" / "*" / "data" / "dispatch-state*.json")):
        try:
            yield json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue


def chat_slot(kc_home: Path, run_id: str | None, chat: str) -> str | None:
    """The Kiro Crew slot the dispatcher opened for this tab, from its state file."""
    for st in _dispatch_states(kc_home):
        slots = st.get("slots") or {}
        if run_id and f"{run_id}\t{chat}" in slots:
            return slots[f"{run_id}\t{chat}"]
        for k, v in slots.items():
            if k.endswith("\t" + chat):
                return v
    return None


def transcript(kc_home: Path, slot: str) -> Path:
    return kc_home / "sessions" / f"dashboard_{slot}.jsonl"


def _lines(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _short(v, n=300):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def tail(path: Path, n=6) -> str:
    rows = [r for r in _lines(path) if r.get("role")]
    return "\n".join(f"    {r.get('ts', '')[11:19]} {r['role']}: {_short(r.get('content'), 200)}" for r in rows[-n:])


def wait_reply(*, health, tab, chat: str, before: int, kc_home: Path, timeout: float, text: str = ""):
    """Wait for Kira's next line in `chat` (more than `before` of them).

    Returns (feed entry, seconds until the dispatcher picked the question up).

    health(): the bridge's /health dict. tab(chat): the mod's tab {feed, state, note, steps, trace}.
    """
    t0 = time.monotonic()
    last_move, last_sig = t0, None
    picked = chat_seen = None
    errors_seen = errors_before(kc_home, health().get("run_id"), chat)  # older failures are not this wait's
    what = f"reply to {text!r}" if text else f"reply in tab {chat!r}"

    def stuck(stage, detail, path=None):
        msg = f"e2e stuck at: {stage} ({what}, {time.monotonic() - t0:.0f} s in)\n  {detail}"
        if path and path.exists():
            msg += f"\n  last lines of {path}:\n{tail(path)}"
        raise Stuck(msg)

    while True:
        now = time.monotonic()
        h = health()
        seen = h.get("last_seen_s")
        if not h.get("game_online") or (seen is not None and seen > HEARTBEAT_S):
            stuck("game", f"no heartbeat for {seen} s: the game is paused, frozen or gone (bridge {h})")
        t = tab(chat)
        feed = t.get("feed") or []
        kira = [e for e in feed if e.get("from") == "kira"]
        if state_now := t.get("state"):
            if state_now == "thinking" and picked is None:
                picked = now
        if len(kira) > before:
            return kira[-1], ((picked or now) - t0)
        mine = [e for e in feed if e.get("from") == "player"]
        if mine and not mine[-1].get("acked") and now - t0 > ACK_S:
            stuck("mod -> bridge", f"the bridge did not ack the question in {ACK_S:.0f} s (UDP ports, bridge down?)")

        state = t.get("state")
        slot = chat_slot(kc_home, h.get("run_id"), chat)
        path = transcript(kc_home, slot) if slot else None
        if path and path.exists() and chat_seen is None:
            chat_seen = now
        if picked is None and now - t0 > PICKUP_S:
            stuck("bridge -> dispatcher", f"not picked up in {PICKUP_S:.0f} s; bridge queue {h.get('queued_asks')}, "
                  f"leased {h.get('leased_asks')} (is the dispatcher cron running against this bridge?)")
        if picked is not None and chat_seen is None and now - picked > CHAT_S:
            stuck("dispatcher -> chat", f"picked up {now - picked:.0f} s ago but no chat transcript "
                  f"(slot {slot}, home {kc_home})")
        if path and path.exists():
            errs = [r for r in _lines(path) if r.get("role") == "error"]
            if len(errs) > errors_seen:
                new = errs[errors_seen:]
                # an error row written during this wait is a failed turn
                stuck("Kiro Crew turn", "the turn failed: " + "; ".join(_short(r.get("content")) for r in new), path)
        if t.get("note") == "no_reply" and state != "thinking" and picked is not None:
            stuck("agent turn", "the bridge gave up: 90 s without a reply or any sign of life", path)

        size = path.stat().st_size if path and path.exists() else -1
        sig = (state, t.get("steps"), len(t.get("trace") or []), size, len(feed))
        if sig != last_sig:
            last_sig, last_move = sig, now
        elif now - last_move > SILENCE_S:
            stage = "agent turn" if picked is not None else "waiting for pickup"
            stuck(stage, f"nothing moved for {SILENCE_S:.0f} s: steps {t.get('steps')}, "
                  f"activity {t.get('activity')!r}, transcript {size} bytes", path)
        if now - t0 > timeout:
            stuck("agent turn", f"still working after {timeout:.0f} s (steps {t.get('steps')}, "
                  f"activity {t.get('activity')!r})", path)
        time.sleep(1)


def errors_before(kc_home: Path, run_id: str | None, chat: str) -> int:
    """Error rows already in the tab's transcript, so a wait reports only new ones."""
    slot = chat_slot(kc_home, run_id, chat)
    if not slot:
        return 0
    return sum(1 for r in _lines(transcript(kc_home, slot)) if r.get("role") == "error")
