"""Each part reports its errors through report/kc_report.py (docs/sentry.md, T2): bridge,
dispatcher, MCP server, app backend. Against a fake Sentry; the mod's side is in
tests/integration/test_reports_game.py."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for d in ("report", "tests", "bridge", "dispatcher", "mcp", "app/backend"):
    sys.path.insert(0, str(ROOT / d))
import kc_report  # noqa: E402
from fake_sentry import FakeSentry  # noqa: E402

QUESTION = "where is my hidden copper"  # player text: never in an automatic error


@pytest.fixture
def sentry(tmp_path):
    s = FakeSentry()
    kc_report._REPORTER = kc_report.Reporter("test", root=ROOT, outbox=tmp_path / "outbox", dsn=s.dsn,
                                             environment="dev-test", settings_url=None, start=False)
    yield s
    kc_report._REPORTER = None
    s.close()


def sent(sentry):
    kc_report._REPORTER.flush()
    return sentry.events()


def test_bridge_reports_a_failed_packet_and_a_lua_error_from_the_game(sentry, monkeypatch):
    import factorio_bridge as fb
    assert fb.report is kc_report
    b = fb.Bridge(fb.Settings(app_version="9.9.9"))
    b.on_game_message({"type": "lua_error", "source": "panel", "name": "sci", "message": "boom", "lua": "error('boom')"})

    def broken(msg):
        raise KeyError("tabs")
    monkeypatch.setattr(b, "on_game_message", broken)
    b.datagram_received(json.dumps({"v": 1, "type": "ask", "text": QUESTION}).encode(), None)
    (lua, files), (exc, _) = sent(sentry)
    assert lua["tags"]["source"] == "panel" and lua["tags"]["app_version"] == "9.9.9" and files["code.lua"] == b"error('boom')"
    assert exc["tags"] == {"part": "bridge", "message_type": "ask"} and exc["exception"]["values"][-1]["type"] == "KeyError"
    assert all(QUESTION.encode() not in body for _, _, body in sentry.got)


def test_dispatcher_reports_a_dead_turn_as_a_warning_and_a_question_it_could_not_pass(sentry, tmp_path, monkeypatch):
    import factorio_dispatch as d
    assert d.report is kc_report
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(d, "bridge", lambda *a, **k: {})
    t = d.Tracer({})
    t.start("s1", {"chat": "General", "run_id": "r"})
    with open(d.transcript_path("s1"), "w") as f:
        f.write(json.dumps({"role": "user", "content": QUESTION}) + "\n")
        f.write(json.dumps({"role": "error", "content": "❌ {'errorType': 'UsageLimitReachedError'}"}) + "\n")
    t.poll()

    class Ctx:
        def list_session_folders(self):
            raise RuntimeError("session_control_disabled")
    st = {"slots": {}, "attempts": {"a1": 2}, "runs": {}, "gen": {}, "root_folder": ""}
    d.handle(Ctx(), dict(d.CONFIG), st, {"id": "a1", "run_id": "r", "chat": "General", "text": QUESTION})
    (turn, _), (fail, _) = sent(sentry)
    assert turn["level"] == "warning" and turn["tags"]["usage_limit"] == "True"
    assert turn["exception"]["values"][0]["type"] == "KiroCrewTurnError"
    assert fail["tags"]["where"] == "pass_question" and "session_control_disabled" in fail["exception"]["values"][-1]["value"]
    assert all(QUESTION.encode() not in body for _, _, body in sentry.got)
    assert any("failed" in line for line in d.RECENT_LOG)  # what a bug report sends


def test_mcp_reports_a_broken_tool_and_answers_instead_of_dying(sentry, monkeypatch):
    import factorio_mcp as m
    assert m.report is kc_report

    def broken(name, args):
        raise ZeroDivisionError("division by zero")
    monkeypatch.setattr(m, "call_tool", broken)
    out = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "factorio_atlas", "arguments": {"q": QUESTION}}})
    assert out["result"]["isError"] and "ZeroDivisionError" in out["result"]["content"][0]["text"]
    (ev, _), = sent(sentry)
    assert ev["tags"] == {"part": "mcp", "tool": "factorio_atlas"}


def test_mcp_reports_kiras_failing_python_with_its_code(sentry, monkeypatch):
    import factorio_mcp as m
    monkeypatch.setattr(m, "bridge_post", lambda *a, **k: {"ok": True})
    r = m.compute({"python": "result = 1 / 0"})
    assert r["error"] == "python_error"
    (ev, files), = sent(sentry)
    assert ev["exception"]["values"][0]["type"] == "ComputeError" and "ZeroDivisionError" in ev["exception"]["values"][0]["value"]
    assert files["main.py"] == b"result = 1 / 0" and b"ZeroDivisionError" in files["stderr.txt"]


def test_backend_reports_a_broken_status_check(sentry, monkeypatch):
    import server
    import status

    def broken(*a):
        raise ValueError("bad health")
    monkeypatch.setattr(status, "status", broken)

    async def go():
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        r, w = await asyncio.open_connection("127.0.0.1", srv.sockets[0].getsockname()[1])
        w.write(b"GET /api/status HTTP/1.1\r\n\r\n")
        await w.drain()
        await r.read()
        w.close()
        srv.close()
    asyncio.run(go())
    (ev, _), = sent(sentry)
    assert ev["tags"] == {"part": "backend", "where": "http"}


def test_uncaught_errors_in_threads_are_reported(sentry, monkeypatch):
    import threading
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # the "previous" hook: quiet
    kc_report.install_hooks()
    t = threading.Thread(target=lambda: 1 / 0)
    t.start()
    t.join()
    (ev, _), = sent(sentry)
    assert ev["tags"]["uncaught"] == "thread"
