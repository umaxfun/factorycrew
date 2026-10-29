"""The e2e watchdog (tests/e2e/watch.py) names the link that hung, fast, without a model."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
import watch  # noqa: E402

REAL_SLEEP = time.sleep


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(watch.time, "sleep", lambda s: REAL_SLEEP(0.01))
    for k in ("ACK_S", "PICKUP_S", "CHAT_S", "SILENCE_S"):
        monkeypatch.setattr(watch, k, 0.15)


def home(tmp_path, slot=None, rows=()):
    h = tmp_path / "kc"
    (h / "factorio").mkdir(parents=True)
    (h / "sessions").mkdir()
    (h / "factorio" / "dispatch-state.json").write_text(json.dumps({"slots": {"run\tT": slot} if slot else {}}))
    if slot:
        (h / "sessions" / f"dashboard_{slot}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return h


def health(**kw):
    h = {"game_online": True, "last_seen_s": 1.0, "run_id": "run", "queued_asks": 0, "leased_asks": 0}
    h.update(kw)
    return lambda: h


def tab(**kw):
    t = {"feed": [{"from": "player", "text": "q", "acked": True}], "state": "idle"}
    t.update(kw)
    return lambda chat: t


def wait(h, kc, t, timeout=5):
    return watch.wait_reply(health=h, tab=t, chat="T", before=0, kc_home=kc, timeout=timeout, text="q")


def test_reply_comes_back(tmp_path):
    feed = [{"from": "player", "text": "q", "acked": True}, {"from": "kira", "text": "a"}]
    entry, _ = wait(health(), home(tmp_path), tab(feed=feed, state="idle"))
    assert entry["text"] == "a"


def test_paused_game(tmp_path):
    with pytest.raises(watch.Stuck, match="stuck at: game"):
        wait(health(last_seen_s=40.0), home(tmp_path), tab())


def test_no_ack(tmp_path):
    with pytest.raises(watch.Stuck, match=r"(?s)mod -> bridge"):
        wait(health(), home(tmp_path), tab(feed=[{"from": "player", "text": "q", "acked": False}]))


def test_no_dispatcher(tmp_path):
    with pytest.raises(watch.Stuck, match=r"(?s)bridge -> dispatcher.*queue 1"):
        wait(health(queued_asks=1), home(tmp_path), tab())


def test_picked_up_but_no_chat(tmp_path):
    with pytest.raises(watch.Stuck, match=r"(?s)dispatcher -> chat"):
        wait(health(), home(tmp_path), tab(state="thinking"))


def test_failed_turn_is_reported_with_its_error(tmp_path):
    kc = home(tmp_path, "factorio-1", rows=[{"role": "user", "content": "q"}])
    t = tab(state="thinking")
    calls = {"n": 0}

    def later_error(chat):
        calls["n"] += 1
        if calls["n"] == 3:  # the turn fails after the wait started
            with (kc / "sessions" / "dashboard_factorio-1.jsonl").open("a") as f:
                f.write(json.dumps({"role": "error", "content": "memory_unavailable: Crew Member 'x' is unavailable"}) + "\n")
        return t("T")
    with pytest.raises(watch.Stuck, match=r"(?s)Kiro Crew turn.*Crew Member 'x' is unavailable"):
        wait(health(), kc, later_error)


def test_old_errors_do_not_count(tmp_path):
    kc = home(tmp_path, "factorio-1", rows=[{"role": "error", "content": "old failure"}])
    feed = [{"from": "player", "text": "q", "acked": True}, {"from": "kira", "text": "a"}]
    entry, _ = wait(health(), kc, tab(feed=feed, state="idle"))
    assert entry["text"] == "a"


def test_turn_that_stops_moving(tmp_path):
    kc = home(tmp_path, "factorio-1", rows=[{"role": "user", "content": "q"}])
    with pytest.raises(watch.Stuck, match=r"(?s)agent turn.*nothing moved"):
        wait(health(), kc, tab(state="thinking", steps=3, activity="placing ghosts"))


def test_moving_turn_is_not_stuck_until_timeout(tmp_path):
    kc = home(tmp_path, "factorio-1", rows=[{"role": "user", "content": "q"}])
    n = {"steps": 0}

    def busy(chat):
        n["steps"] += 1  # a new MCP step every poll
        return {"feed": [{"from": "player", "text": "q", "acked": True}], "state": "thinking", "steps": n["steps"]}
    t0 = time.monotonic()
    with pytest.raises(watch.Stuck, match=r"(?s)still working after"):
        wait(health(), kc, busy, timeout=0.6)
    assert time.monotonic() - t0 >= 0.6


def test_bridge_gave_up(tmp_path):
    kc = home(tmp_path, "factorio-1", rows=[{"role": "user", "content": "q"}])
    seq = iter([{"state": "thinking"}] * 2 + [{"state": "idle", "note": "no_reply"}] * 100)

    def t(chat):
        d = next(seq)
        return {"feed": [{"from": "player", "text": "q", "acked": True}], **d}
    with pytest.raises(watch.Stuck, match=r"(?s)bridge gave up"):
        wait(health(), kc, t)
