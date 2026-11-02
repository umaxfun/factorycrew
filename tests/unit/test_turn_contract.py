"""The turn contract (docs/areas/bridge.md, "Turn contract"): Kira's agent-spec hooks end game
turns at the bridge, and a turn without factorio_reply is sent back to her once, then its text
goes to the tab. No game, no gateway; app/hooks/turn_hook.py against a fake bridge."""
from __future__ import annotations

import asyncio
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bridge"))
import factorio_bridge as fb  # noqa: E402

SLOT = "dashboard:factorio-abc"
HEADER = '[Factorio · run "Run 1" · tab "лубрикант" · tick 5 · reply in Russian or another Cyrillic language]\nну что там?'


def bridge_with_turn():
    b = fb.Bridge(fb.Settings())
    b.game.run_id = "r1"
    out = []
    b.deliver = lambda m: out.append(m) or True
    b.arm_idle = lambda key: None  # no event loop here
    b.set_thinking("r1", "лубрикант", "ask-1", 5)
    out.clear()
    return b, out


def test_a_turn_without_factorio_reply_goes_back_to_kira_once_then_its_text_to_the_tab():
    b, out = bridge_with_turn()
    assert b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})["turn"] is True
    assert b.slots["factorio-abc"] == ("r1", "лубрикант") and b.working[("r1", "лубрикант")]["hooked"]
    first = b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "Для 25k нужно 167 машин."})
    assert first["decision"] == "block" and 'chat="лубрикант"' in first["reason"] and out == []
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": "[Hook continuation — automatic]\n..."})
    second = b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "Для 25k нужно 167 машин."})
    assert "decision" not in second
    assert [m["type"] for m in out] == ["reply"] and out[0]["text"] == "Для 25k нужно 167 машин."
    assert ("r1", "лубрикант") not in b.working


def test_the_previous_turns_late_stop_is_not_the_new_questions():
    # full e2e, 2026-11-02: turn 1 answered, the follow-up was already thinking when turn 1's Stop
    # came; it was taken for the follow-up's, Kira was sent back and repeated turn 1's answer
    b, out = bridge_with_turn()  # the follow-up, tick 5, is thinking; its prompt has not come yet
    late = b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "7 chests marked."})
    assert late == {"ok": True, "turn": False} and out == []
    b.slots["factorio-abc"] = ("r1", "лубрикант")  # learnt from turn 1's own prompt
    old = HEADER.replace("tick 5", "tick 1")
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": old})
    assert b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "7"}) == {"ok": True, "turn": False}
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})  # now the follow-up's own
    assert b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "7"})["decision"] == "block"


def test_a_header_with_nothing_after_the_tick_still_binds():
    b, out = bridge_with_turn()  # the dispatcher writes "tick N]" when there is no player and no language
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": '[Factorio · run "R" · tab "лубрикант" · tick 5]\n?'})
    assert b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "x"})["decision"] == "block"


def test_a_turn_that_answered_is_left_alone():
    b, out = bridge_with_turn()
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})
    b.reply("лубрикант", "167 машин.")  # factorio_reply
    res = b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": "Done."})
    assert res == {"ok": True, "turn": False} and [m["type"] for m in out] == ["reply"]


def test_a_dashboard_turn_is_not_a_game_turn():
    b, out = bridge_with_turn()
    b.hook({"event": "UserPromptSubmit", "session_key": "dashboard:chat-1", "prompt": "hello from the dashboard"})
    assert b.hook({"event": "Stop", "session_key": "dashboard:chat-1", "assistant_text": "hi"}) == {"ok": True, "turn": False}
    assert ("r1", "лубрикант") in b.working and out == []


def test_an_empty_last_text_ends_the_turn_with_no_reply():
    b, out = bridge_with_turn()
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})
    b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": ""})
    b.hook({"event": "Stop", "session_key": SLOT, "assistant_text": ""})
    assert out[-1] == {"type": "status", "run_id": "r1", "chat": "лубрикант", "state": "idle", "note": "no_reply"}


def test_tool_calls_show_in_the_trace_from_the_hook():
    b, out = bridge_with_turn()
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})
    b.hook({"event": "PostToolUse", "session_key": SLOT, "tool_name": "@factorycrew:factorio/factorio_query"})
    b.hook({"event": "PostToolUse", "session_key": SLOT, "tool_name": "tool_load"})
    b.hook({"event": "PostToolUse", "session_key": SLOT, "tool_name": "@factorycrew:factorio/factorio_reply"})
    assert [m.get("text") for m in out] == ["running a Lua query"]


def test_with_hooks_the_dispatchers_copy_yields_but_a_killed_turn_still_reports():
    b, out = bridge_with_turn()
    b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})
    assert b.reply("лубрикант", "copy", fallback=True, ask_id="ask-1")["skipped"] == "hooks"
    assert b.turn_end("лубрикант", "ask-1")["ended"] is False
    r = b.reply("лубрикант", "Kiro has no credits left", fallback=True, ask_id="ask-1", failure=True)
    assert r["delivered"] and out[-1]["text"] == "Kiro has no credits left"


def test_without_hooks_the_dispatcher_still_ends_turns():
    b, out = bridge_with_turn()  # a Kiro Crew that does not run the spec's hooks
    assert b.reply("лубрикант", "copy", fallback=True, ask_id="ask-1")["delivered"]


def test_the_transcript_tool_lines_yield_to_the_hook_lines():
    b, out = bridge_with_turn()

    async def go():
        before = await b.route("POST", "/trace", {}, {"chat": "лубрикант", "kind": "tool", "text": "x"})
        b.hook({"event": "UserPromptSubmit", "session_key": SLOT, "prompt": HEADER})
        after = await b.route("POST", "/trace", {}, {"chat": "лубрикант", "kind": "tool", "text": "x"})
        text = await b.route("POST", "/trace", {}, {"chat": "лубрикант", "kind": "text", "text": "Считаю."})
        return before, after, text
    before, after, text = asyncio.run(go())
    assert before[1]["sent"] and not after[1]["sent"] and text[1]["sent"]


# ── app/hooks/turn_hook.py, the command Kiro Crew runs ──

def fake_bridge(answer):
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def run_hook(stdin, port):
    return subprocess.run(["/usr/bin/python3", str(ROOT / "app" / "hooks" / "turn_hook.py"), "--bridge",
                           f"http://127.0.0.1:{port}"], input=json.dumps(stdin), capture_output=True, text=True,
                          timeout=20)


def test_the_hook_passes_the_event_on_and_prints_the_block():
    srv, got = fake_bridge({"ok": True, "decision": "block", "reason": "Call factorio_reply now."})
    p = run_hook({"hook_event_name": "Stop", "session_key": SLOT, "assistant_text": "167 машин",
                  "hook_continuation_count": "0"}, srv.server_port)
    srv.shutdown()
    assert p.returncode == 0 and json.loads(p.stdout) == {"decision": "block", "reason": "Call factorio_reply now."}
    assert got == [("/hook", {"event": "Stop", "session_key": SLOT, "assistant_text": "167 машин", "prompt": "",
                               "tool_name": ""})]


def test_the_hook_prints_nothing_but_on_stop_and_survives_no_bridge():
    srv, _ = fake_bridge({"ok": True, "decision": "block", "reason": "x"})
    p = run_hook({"hook_event_name": "PostToolUse", "session_key": SLOT, "tool_name": "t"}, srv.server_port)
    srv.shutdown()
    assert p.returncode == 0 and p.stdout == ""
    p = run_hook({"hook_event_name": "Stop", "session_key": SLOT}, 9)  # nothing listens
    assert p.returncode == 0 and p.stdout == ""


def test_the_app_and_the_stand_agent_carry_the_same_three_hooks():
    spec = json.loads((ROOT / "agent" / "factorio-companion.json").read_text())
    assert set(spec["hooks"]) == {"userPromptSubmit", "postToolUse", "stop"}
    assert all("app/hooks/turn_hook.py" in h["command"] for hs in spec["hooks"].values() for h in hs)
    subprocess.run([str(ROOT / "scripts" / "build-app.sh")], check=True, capture_output=True)
    built = json.loads((ROOT / "dist" / "factorycrew" / "agents" / "factorio-companion.json").read_text())
    assert set(built["hooks"]) == set(spec["hooks"])
    assert all('$KIROCREW_HOME/apps/factorycrew/hooks/turn_hook.py' in h["command"]
               for hs in built["hooks"].values() for h in hs)
    assert (ROOT / "dist" / "factorycrew" / "hooks" / "turn_hook.py").exists()
