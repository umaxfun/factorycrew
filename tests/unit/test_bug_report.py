"""Bug reports the player sends (docs/sentry.md, T3): one report.zip as a Sentry `feedback`, a
local copy, the dispatcher's and the bridge's parts, the status page's copy. Against a fake
Sentry; the window is in mod/kc-companion/tests/test-gui.lua, the whole path in
tests/integration/test_reports_game.py and tests/e2e/test_reports_e2e.py."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for d in ("report", "tests", "bridge", "dispatcher", "app/backend"):
    sys.path.insert(0, str(ROOT / d))
import kc_report  # noqa: E402
from fake_sentry import KEY, FakeSentry  # noqa: E402


@pytest.fixture
def sentry(tmp_path):
    s = FakeSentry()
    kc_report._REPORTER = kc_report.Reporter("test", root=ROOT, outbox=tmp_path / "factorio" / "outbox", dsn=s.dsn,
                                             environment="dev-test", settings_url=None, start=False)
    yield s
    kc_report._REPORTER = None
    s.close()


def unzip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_a_bug_report_goes_as_one_zip_and_a_copy_stays(sentry, tmp_path):
    res = kc_report.bug_report("it hangs", {"a.json": b"{}", "chat.jsonl": b'{"role":"user"}\n', "none": None},
                               "button", {"by": "test"}, wait=3)
    assert res["ok"] and res["sent"] and len(res["report_id"]) == 8
    (ev, files), = sentry.events()
    assert ev["contexts"]["feedback"] == {"message": "it hangs", "source": "button"} and ev["tags"]["by"] == "test"
    assert set(unzip(files["report.zip"])) == {"comment.txt", "a.json", "chat.jsonl"}
    copy = Path(res["path"])
    assert copy == tmp_path / "factorio" / "bug-reports" / f"{res['report_id']}.zip" and copy.read_bytes() == files["report.zip"]


def test_offline_the_report_is_kept_and_says_so(tmp_path):
    rp = kc_report.Reporter("test", root=ROOT, outbox=tmp_path / "outbox", dsn=f"http://{KEY}@127.0.0.1:9/7",
                            environment="dev-test", settings_url=None, start=False)
    res = rp.bug_report("offline", {"x": b"1"}, "button", wait=0.5)
    assert res["ok"] and res["sent"] is False and Path(res["path"]).exists() and len(rp.files()) == 1


def test_a_report_too_large_is_refused(sentry, monkeypatch):
    monkeypatch.setattr(kc_report, "BUG_REPORT_MAX", 100)
    import os
    res = kc_report.bug_report("big", {"x": os.urandom(1000)}, "button", wait=0)
    assert res["ok"] is False and "over" in res["error"] and sentry.got == []


def test_the_screenshot_is_read_only_from_kc_bug(tmp_path, monkeypatch):
    monkeypatch.setenv("FC_FACTORIO_SCRIPT_OUTPUT", str(tmp_path))
    (tmp_path / "kc-bug").mkdir()
    (tmp_path / "kc-bug" / "bug-1.png").write_bytes(b"png")
    assert kc_report.read_screenshot("kc-bug/bug-1.png", wait=2) == b"png"
    assert kc_report.read_screenshot("../secret.png", wait=0) is None
    assert kc_report.read_screenshot("kc-bug/missing.png", wait=0.6) is None


ASK = {"kind": "bug_report", "id": "bug-1", "chat": "General", "comment": "the panel froze", "run_id": "r1",
       "run_name": "Run 1", "mod_version": "0.3.8", "factorio_version": "2.0.77", "tick": 5,
       "health": {"game_online": True}, "bridge_log": "12:00 INFO ask bug-1"}


def test_the_dispatcher_adds_the_tabs_chat_and_its_own_log(sentry, tmp_path, monkeypatch):
    import factorio_dispatch as d
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    d.transcript_path("slot-a").write_text('{"role": "user", "content": "the panel froze"}\n')
    posted = []
    monkeypatch.setattr(d, "bridge", lambda cfg, method, path, body=None, timeout=40.0: posted.append((path, body)) or {})
    d.note("wake started")
    res = d.bug_report({}, {"slots": {"r1\tGeneral": "slot-a"}}, ASK)
    assert res["ok"] and res["sent"]
    (path, body), = posted
    assert path == "/bug_report_result" and body["id"] == "bug-1" and body["report_id"] == res["report_id"]
    (ev, files), = sentry.events()
    z = unzip(files["report.zip"])
    assert z["session/transcript.jsonl"].startswith(b'{"role": "user"') and b"wake started" in z["dispatcher.log"]
    assert json.loads(z["bridge-health.json"]) == {"game_online": True} and z["bridge.log"] == b"12:00 INFO ask bug-1"
    assert json.loads(z["report.json"])["factorio_version"] == "2.0.77" and ev["tags"]["by"] == "dispatcher"


def test_the_bridge_queues_a_report_with_its_health_and_log_and_sends_it_when_no_dispatcher_takes_it(sentry):
    import factorio_bridge as fb

    async def go():
        b = fb.Bridge(fb.Settings())
        sent_to_game = []
        b.deliver = lambda m: sent_to_game.append(m) or True
        fb.RECENT.lines.append("12:01 INFO something before")
        b.on_game_message({"type": "bug_report", **{k: v for k, v in ASK.items() if k not in ("kind", "health", "bridge_log")}})
        ask = b.asks[-1]
        assert ask["kind"] == "bug_report" and "something before" in ask["bridge_log"] and "game_online" in ask["health"]
        taken = await b.take_asks(0, None)
        assert taken == [ask] and not b.working  # a report starts no "thinking"
        b.bug_fallback(ask)  # taken by a dispatcher: the bridge leaves it
        assert sentry.got == []
        b.asks.append(ask)
        b.bug_fallback(ask)
        for _ in range(100):
            if sent_to_game:
                break
            await asyncio.sleep(0.05)
        return sent_to_game
    msg, = asyncio.run(go())
    assert msg["type"] == "bug_report_result" and msg["ok"] and msg["sent"] and msg["id"] == "bug-1"
    (ev, files), = sentry.events()
    z = unzip(files["report.zip"])
    assert "session/transcript.jsonl" not in z and b"without the tab's chat" in z["report.json"] and ev["tags"]["by"] == "bridge"


def test_the_status_page_sends_a_report_without_the_game(sentry, tmp_path, monkeypatch):
    import server
    import status
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(status, "status", lambda *a: {"state": "fail", "checks": [{"id": "game", "state": "fail"}]})
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "dashboard_factorio-aaa.jsonl").write_text('{"role": "user", "content": "q"}\n')
    monkeypatch.setattr(server, "kirocrew_home", lambda: tmp_path)
    res = server.app_bug_report({"comment": "  "})  # one click: no comment needed
    assert res["ok"]
    res = server.app_bug_report({"comment": "the bridge is down"})
    assert res["ok"] and res["sent"]
    (_, empty), (ev, files) = sentry.events()
    assert unzip(empty["report.zip"])["comment.txt"] == b"(no comment)"
    z = unzip(files["report.zip"])
    assert json.loads(z["status.json"])["state"] == "fail" and z["comment.txt"] == b"the bridge is down"
    assert z["chat1-factorio-aaa/transcript.jsonl"] == b'{"role": "user", "content": "q"}\n'
    assert ev["contexts"]["feedback"]["source"] == "app"


# ── Kira's factorio_feedback (T4) ──

def test_kiras_feedback_sends_nothing_until_the_draft_is_confirmed(sentry, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "mcp"))
    import factorio_mcp as m
    home = tmp_path / "home"
    (home / "factorio").mkdir(parents=True)
    (home / "sessions").mkdir()
    (home / "factorio" / "dispatch-state.json").write_text(json.dumps({"slots": {"r1\tGeneral": "slot-a"}}))
    (home / "sessions" / "dashboard_slot-a.jsonl").write_text('{"role": "user", "content": "the panel froze"}\n')
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    monkeypatch.setattr(m, "current_run_id", lambda: "r1")
    assert m.call_tool("factorio_feedback", {"kind": "bug", "chat": "General"})["error"] == "bad_request"
    d = m.call_tool("factorio_feedback", {"kind": "bug", "text": "The science panel stops updating.", "chat": "General"})
    assert d["ok"] and d["sent"] is False and "this tab's whole session" in d["goes"] and sentry.got == []
    assert m.call_tool("factorio_feedback", {"send": "nosuchdraft"})["error"] == "no_draft"
    res = m.call_tool("factorio_feedback", {"send": d["draft"]})
    assert res["ok"] and res["sent"] and len(res["report_id"]) == 8
    (ev, files), = sentry.events()
    assert ev["contexts"]["feedback"]["message"] == "[bug] The science panel stops updating."
    assert ev["contexts"]["feedback"]["source"] == "kira" and ev["tags"]["kind"] == "bug"
    z = unzip(files["report.zip"])
    assert z["session/transcript.jsonl"].startswith(b'{"role": "user"') and json.loads(z["report.json"])["chat"] == "General"
    assert m.call_tool("factorio_feedback", {"send": d["draft"]})["error"] == "no_draft"  # sent once
    assert Path(res["path"]) == tmp_path / "factorio" / "bug-reports" / f"{res['report_id']}.zip"  # beside the outbox


def test_the_feedback_tool_is_listed_with_the_two_step_rule():
    sys.path.insert(0, str(ROOT / "mcp"))
    import factorio_mcp as m
    tool = next(t for t in m.TOOLS if t["name"] == "factorio_feedback")
    assert "sends NOTHING" in tool["description"] and "after the player said yes" in tool["description"]


def test_a_raw_session_holds_the_transcript_the_kiro_conversation_and_its_logs(tmp_path):
    """The layout of a dev gateway (kiro-cli home under the crew home), as on 2026-11-01."""
    home, sid, slot = tmp_path, "sess_e90c", "factorio-cf37"
    (home / "sessions").mkdir()
    (home / "sessions" / f"dashboard_{slot}.jsonl").write_text('{"role": "error", "content": "x"}\n')
    (home / "session_map.json").write_text(json.dumps({f"dashboard:{slot}": {"sid": sid}}))
    conv = home / "kiro" / "sessions" / "22ef" / sid
    conv.mkdir(parents=True)
    (conv / "messages.jsonl").write_text('{"payload": "the model said"}\n')
    (conv / "session.json").write_text("{}")
    logs = home / "kiro" / "logs" / "20261101T0941"
    logs.mkdir(parents=True)
    (logs / "kiro.log").write_text("\n".join([
        '{"level":"info","message":"ACP session/new sessionId=sess_e90c"}',
        '{"level":"info","message":"another session, nothing to do with it"}',
        '{"level":"error","message":"stream ended early"}']))
    (logs / "mcp.log").write_text('{"level":"error","serverName":"factorio","message":"Failed to connect"}')
    (home / "gateway.log").write_text(f"10:41 WARNING slot={slot} turn cut\n10:42 INFO other\n")
    f = kc_report.session_files(home, slot)
    assert f["session/transcript.jsonl"].startswith(b'{"role": "error"')
    assert f["session/kiro-messages.jsonl"] == b'{"payload": "the model said"}\n' and "session/kiro-session.json" in f
    log = f["session/kiro-log-20261101T0941.jsonl"]
    assert b"sessionId=sess_e90c" in log and b"stream ended early" in log and b"another session" not in log
    assert b"Failed to connect" in f["session/mcp-log-20261101T0941.jsonl"]
    assert f["session/gateway.log"] == f"10:41 WARNING slot={slot} turn cut".encode()
    assert "session/missing.txt" not in f
    assert b"session_map.json" in kc_report.session_files(tmp_path / "nowhere", slot)["session/missing.txt"]


def test_a_report_over_the_limit_keeps_the_end_of_the_biggest_file(sentry, monkeypatch):
    import os
    monkeypatch.setattr(kc_report, "BUG_REPORT_MAX", 600 * 1024)
    big = os.urandom(2 * 1024 * 1024) + b"THE END"
    res = kc_report.bug_report("big", {"session/kiro-messages.jsonl": big, "small": b"x"}, "button", wait=3)
    assert res["ok"]
    z = unzip(sentry.events()[0][1]["report.zip"])
    assert z["session/kiro-messages.jsonl"].endswith(b"THE END") and z["small"] == b"x"
