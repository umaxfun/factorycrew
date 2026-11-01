"""Bug reports through the whole chain (docs/sentry.md, T3, T4), with a real model.

Kira's factorio_feedback: a draft, the player's yes, then the send; nothing goes before the yes.
The window's "Report a problem": the real dispatcher adds the tab's chat. Both against a fake
Sentry on this machine, set for the stand's home in <KIROCREW_HOME>/factorio/report.json (the
gateway may not pass env to the dispatcher and the MCP server; the container's bridge has
FC_SENTRY_DSN empty).
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from fake_sentry import FakeSentry  # noqa: E402
from test_e2e import KC_HOME, Player, http  # noqa: E402
from watch import _lines, chat_slot, transcript  # noqa: E402

TAB = "Feedback"
BUG = ("Report a bug to the developers of this app: the Library button shows an empty list after I delete "
       "the last saved panel, even after reopening it. Don't fix anything, just report it.")


@pytest.fixture(scope="module")
def sentry():
    s = FakeSentry()
    setting = KC_HOME / "factorio" / "report.json"
    setting.parent.mkdir(parents=True, exist_ok=True)
    setting.write_text(json.dumps({"dsn": s.dsn}))
    yield s
    setting.write_text(json.dumps({"dsn": ""}))
    s.close()


@pytest.fixture(scope="module")
def player():
    assert http("/health")["game_online"], "stand is not up: run scripts/test-e2e.sh"
    p = Player()
    yield p
    p.rcon.close()


def feedbacks(sentry):
    return [(ev, files) for ev, files in sentry.events() if ev.get("contexts", {}).get("feedback")]


def kiras_feedback_calls(run_id, at_least=1):
    """factorio_feedback tool rows in the tab's transcript (what Kira actually called). The answer
    reaches the game before the gateway writes the turn's tool rows, so wait for them a little."""
    calls = []
    for _ in range(30):
        slot = chat_slot(KC_HOME, run_id, TAB)
        rows = list(_lines(transcript(KC_HOME, slot))) if slot else []
        calls = [r for r in rows if r.get("role") == "tool" and "factorio_feedback" in str(r.get("content"))]
        if len(calls) >= at_least:
            break
        time.sleep(1)
    for r in calls:
        print("  tool:", json.dumps(r.get("meta") or {}, ensure_ascii=False)[:400])
    return calls


def test_kira_drafts_a_bug_report_and_sends_it_only_after_yes(player, sentry):
    run_id = http("/health")["run_id"]
    text, _, t = player.ask_and_wait(TAB, BUG)
    print(f"\ndraft reply ({t:.0f}s): {text}")
    assert feedbacks(sentry) == [], "a report went out before the player said yes"
    assert kiras_feedback_calls(run_id), "Kira did not make a draft with factorio_feedback"
    assert "library" in text.lower(), text  # she shows the report's text before sending
    text, _, t = player.ask_and_wait(TAB, "yes, send it")
    print(f"sent reply ({t:.0f}s): {text}")
    got = feedbacks(sentry)
    for _ in range(20):
        if got:
            break
        time.sleep(1)
        got = feedbacks(sentry)
    assert len(got) == 1, got
    assert len(kiras_feedback_calls(run_id, at_least=2)) >= 2  # the draft, then the send
    ev, files = got[0]
    msg = ev["contexts"]["feedback"]["message"]
    assert msg.startswith("[bug]") and "library" in msg.lower() and ev["tags"]["by"] == "kira", msg
    assert ev["event_id"][:8] in text, "Kira did not tell the player the report id"
    z = zipfile.ZipFile(io.BytesIO(files["report.zip"]))
    names = set(z.namelist())
    assert b"Library button" in z.read("session/transcript.jsonl"), "the tab's chat is not in the report"
    assert {"session/kiro-messages.jsonl", "session/kiro-session.json"} <= names, names  # the raw session
    assert any(n.startswith("session/kiro-log-") for n in names), names
    print("  missing:", z.read("session/missing.txt").decode() if "session/missing.txt" in names else "none")


def test_the_windows_report_goes_with_the_tabs_chat(player, sentry):
    before = len(feedbacks(sentry))
    bug_id = player.lua(f'return remote.call("kc", "bug_report", "{TAB}", "e2e: the window\'s report")')
    res = None
    for _ in range(60):
        res = player.lua('return remote.call("kc", "last_bug_result")')
        if isinstance(res, dict) and res.get("id") == bug_id:
            break
        time.sleep(1)
    assert res and res["id"] == bug_id and res["ok"] and res["sent"], res
    ev, files = feedbacks(sentry)[before:][0]
    assert ev["contexts"]["feedback"]["message"] == "e2e: the window's report" and ev["tags"]["by"] == "dispatcher"
    z = zipfile.ZipFile(io.BytesIO(files["report.zip"]))
    names = set(z.namelist())
    assert {"session/transcript.jsonl", "session/kiro-messages.jsonl", "dispatcher.log", "bridge.log",
            "bridge-health.json", "report.json"} <= names, names
    assert b"Library button" in z.read("session/kiro-messages.jsonl"), "the kiro-cli conversation is not the tab's"
    assert "session/missing.txt" not in names, z.read("session/missing.txt")
    local = KC_HOME / "factorio" / "bug-reports" / f"{res['report_id']}.zip"
    assert local.exists(), "no local copy of the report"
