"""Level 2: mod + bridge + MCP integration (SPEC.md, "Tests", level 2). Not e2e: Kiro Crew is not in the loop."""
from __future__ import annotations

import subprocess
import time

from conftest import DATA, ROOT, http, wait_until

CHAT = "Logistics"

SETUP_CHESTS = """
local s = game.surfaces[1]
s.request_to_generate_chunks({0, 0}, 3)
s.force_generate_chunk_requests()
local tiles = {}
for x = -2, 45 do for y = -3, 3 do tiles[#tiles + 1] = {name = "refined-concrete", position = {x, y}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{area = {{-5, -5}, {50, 6}}}) do e.destroy() end
for _, t in pairs(game.forces.player.find_chart_tags(s)) do t.destroy() end
local made = {}
for i = 1, 20 do
  local e = s.create_entity{name = "storage-chest", position = {i * 2, 0}, force = "player"}
  if i % 3 ~= 1 then e.storage_filter = "iron-plate" end -- i = 1,4,7,10,13,16,19 stay unfiltered
  made[#made + 1] = {x = e.position.x, y = e.position.y, filtered = e.storage_filter ~= nil}
end
game.forces.player.add_chart_tag(s, {position = {100, 100}, text = "my base"})
return made
"""

# What an agent would write for "find yellow chests without a filter".
AGENT_QUERY = """
local out = {}
for _, e in pairs(surface.find_entities_filtered{name = "storage-chest"}) do
  if not e.storage_filter then out[#out + 1] = {x = e.position.x, y = e.position.y} end
end
return out
"""

TAGS = """
local kc, other = {}, {}
for _, t in pairs(game.forces.player.find_chart_tags(surface)) do
  local p = {x = t.position.x, y = t.position.y, text = t.text}
  if string.sub(t.text, 1, 5) == "[KC] " then kc[#kc + 1] = p else other[#other + 1] = p end
end
return {kc = kc, other = other}
"""


def key(p):
    return (round(p["x"], 2), round(p["y"], 2))


def test_handshake(stack):
    h = http("GET", "/health")
    st = stack.mod_state()
    assert h["game_online"] and h["run_id"] == st["run_id"] and h["run_name"] == st["run_name"]
    # the version the game loaded, for the status page's "restart Factorio after an update"
    assert h["mod_version"] and h["mod_version"] == stack.rcon.lua('return script.active_mods["kc-companion"]'), h
    wait_until(lambda: stack.mod_state()["connected"], what="pong reaching the mod")


def test_ask_reaches_dispatcher_with_ack_and_thinking(stack):
    ask_id = stack.ask(CHAT, "find yellow chests without a filter")
    asks = http("GET", "/asks?wait=10")["asks"]
    assert [(a["id"], a["chat"], a["text"]) for a in asks] == [(ask_id, CHAT, "find yellow chests without a filter")]
    assert asks[0]["run_id"] == stack.mod_state()["run_id"]
    assert http("POST", "/asks/done", {"ids": [ask_id]})["confirmed"] == 1

    def acked_and_thinking():
        t = stack.tab(CHAT)
        mine = [e for e in t["feed"] if e.get("id") == ask_id]
        return mine and mine[0]["acked"] and t["state"] == "thinking"
    wait_until(acked_and_thinking, what="ack + thinking in the mod")


def test_unfiltered_chests_marked_and_unmarked(stack, mcp):
    made = mcp.tool("factorio_query", lua=SETUP_CHESTS)
    assert made["ok"], made
    expected = {key(p) for p in made["data"] if not p["filtered"]}
    assert len(made["data"]) == 20 and len(expected) == 7

    found = mcp.tool("factorio_query", lua=AGENT_QUERY)
    assert found["ok"] and isinstance(found["ms"], int)
    assert {key(p) for p in found["data"]} == expected

    marked = mcp.tool("factorio_mark", chat=CHAT,
                      marks=[{"surface": "nauvis", "position": p, "text": "chest without filter"} for p in found["data"]])
    assert marked["ok"] and len(marked["data"]["ids"]) == 7 and marked["data"]["failed"] == [], marked

    tags = mcp.tool("factorio_query", lua=TAGS)["data"]
    assert {key(p) for p in tags["kc"]} == expected
    assert all(p["text"] == "[KC] chest without filter" for p in tags["kc"])

    listed = mcp.tool("factorio_list_marks", chat=CHAT)
    assert sorted(m["id"] for m in listed["data"]) == sorted(marked["data"]["ids"])

    removed = mcp.tool("factorio_unmark", chat=CHAT, all_in_chat=True)
    assert removed["ok"] and removed["data"]["removed"] == 7
    tags = mcp.tool("factorio_query", lua=TAGS)["data"]
    assert tags.get("kc") in (None, [], {})  # table_to_json renders an empty table as {}
    assert [p["text"] for p in tags["other"]] == ["my base"]

    sent = mcp.tool("factorio_reply", chat=CHAT, text="7 chests without a filter, marked on the map.")
    assert sent["ok"] and sent["delivered"]
    wait_until(lambda: stack.tab(CHAT)["feed"][-1]["text"] == "7 chests without a filter, marked on the map."
               and stack.tab(CHAT)["state"] == "idle", what="reply in the tab")


def test_mark_failure_is_reported(mcp):
    r = mcp.tool("factorio_mark", chat=CHAT, marks=[
        {"surface": "no-such-planet", "position": {"x": 0, "y": 0}},
        {"position": {"x": 1, "y": 1}, "text": "ok"},
    ])
    assert len(r["data"]["ids"]) == 1 and r["data"]["failed"] == [{"index": 1, "reason": "no_such_surface"}]
    mcp.tool("factorio_unmark", chat=CHAT, all_in_chat=True)


def test_query_errors_and_large_results(mcp):
    r = mcp.tool("factorio_query", lua="return nosuch.x")
    assert not r["ok"] and r["error"] == "runtime_error"
    # ~40 KB result: chunked game -> bridge, Cyrillic on purpose so slices must not split UTF-8.
    r = mcp.tool("factorio_query", lua="local t = {} for i = 1, 2000 do t[i] = 'ящик-' .. i end return t")
    assert r["ok"] and len(r["data"]) == 2000 and r["data"][-1] == "ящик-2000"
    r = mcp.tool("factorio_query", lua="local t = {} for i = 1, 20000 do t[i] = 'xxxxx' end return t")
    assert r["error"] == "result_too_large"


def test_large_reply_reaches_the_tab(stack, mcp):
    text = "длинный ответ " * 1500  # Cyrillic on purpose: ~40 KB of UTF-8, chunked bridge -> game
    assert mcp.tool("factorio_reply", chat="big", text=text)["delivered"]
    wait_until(lambda: (stack.tab("big") or {}).get("feed", [{}])[-1].get("text") == text, what="large reply")


def test_bridge_packets_fit_the_games_initial_buffer():
    import sys
    sys.path.insert(0, str(ROOT / "bridge"))
    from factorio_bridge import PACKET_MAX, Reassembler, encode
    for text in ("ж" * 30000, '"\\' * 20000, "x" * 3900, "ok"):
        packets = encode({"type": "reply", "id": "p", "text": text})
        assert all(len(p) <= PACKET_MAX for p in packets), max(map(len, packets))
        r, msg = Reassembler(), None
        for p in reversed(packets):
            msg = r.feed(p) or msg
        assert msg["text"] == text


def test_idle_after_timeout_without_reply(stack):
    ask_id = stack.ask("Trains", "where is the jam?")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
    http("POST", "/asks/done", {"ids": [ask_id]})  # handed to the chat; the agent then never replies
    wait_until(lambda: stack.tab("Trains")["state"] == "thinking", what="thinking")
    t = wait_until(lambda: (lambda t: t if t["state"] == "idle" else None)(stack.tab("Trains")),
                   timeout=10, what="idle after timeout")
    assert t["note"] == "no_reply"


def test_a_turn_lost_in_a_bridge_restart_stops_thinking(stack):
    """gamer, 2026-11-01: an app update restarted the bridge mid-turn; the new bridge knew nothing
    of the turn and the tab said "thinking" for good. Its pong now lists live turns, and the mod
    drops a thinking tab that is not listed for 30 s of game time."""
    def real_bridge():  # the real 20 min idle backstop, so only the pong can end the turn
        log = open(DATA / "bridge.log", "a")
        stack.bridge = subprocess.Popen(["python3", str(ROOT / "bridge" / "factorio_bridge.py"), "--offline-after", "3",
                                         "--idle-after", "1200", "--request-timeout", "5"],
                                        stdout=log, stderr=subprocess.STDOUT)
        wait_until(lambda: http("GET", "/health")["ok"], timeout=10, what="bridge http")
        stack.wait_online()
    try:
        stack.stop_bridge()
        real_bridge()
        ask_id = stack.ask("Lost", "lubricant chain?")
        assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
        http("POST", "/asks/done", {"ids": [ask_id]})
        wait_until(lambda: stack.tab("Lost")["state"] == "thinking", what="thinking")
        time.sleep(3)
        assert stack.tab("Lost")["state"] == "thinking", "a live turn was dropped"
        stack.stop_bridge()
        real_bridge()  # a new bridge: it knows no turn
        t = wait_until(lambda: (lambda t: t if t["state"] == "idle" else None)(stack.tab("Lost")),
                       timeout=30, what="the lost turn to stop thinking")
        assert t["note"] == "lost", t
    finally:
        stack.stop_bridge()
        stack.start_bridge()
        stack.wait_online()


def test_turn_end_from_the_dispatcher_ends_thinking(stack):
    ask_id = stack.ask("Ended", "how are you?")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
    http("POST", "/asks/done", {"ids": [ask_id]})
    wait_until(lambda: stack.tab("Ended")["state"] == "thinking", what="thinking")
    # a copy of an earlier turn's end does not end this one
    assert http("POST", "/turn_end", {"chat": "Ended", "ask_id": "ask-older"})["ended"] is False
    assert http("POST", "/turn_end", {"chat": "Ended", "ask_id": ask_id})["ended"] is True
    t = wait_until(lambda: (lambda t: t if t["state"] == "idle" else None)(stack.tab("Ended")), what="idle")
    assert t["note"] == "no_reply"


def test_stop_from_the_window(stack, mcp):
    first = stack.ask("Stop", "plan the whole rail network")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [first]
    http("POST", "/asks/done", {"ids": [first]})
    wait_until(lambda: stack.tab("Stop")["state"] == "thinking", what="thinking")
    assert mcp.tool("factorio_query", lua="return 1")["ok"]  # one step
    later = stack.ask("Stop", "and the stations too")  # waits in the bridge's queue
    stop = stack.rcon.lua('return remote.call("kc", "stop", "Stop")')
    assert stop["type"] == "stop" and stop["chat"] == "Stop" and stack.tab("Stop")["stopping"]
    # the stop goes to the dispatcher first, and the tab's waiting question is dropped
    asks = wait_until(lambda: http("GET", "/asks?wait=2")["asks"], what="the stop at the bridge")
    assert [(a.get("kind"), a["id"]) for a in asks] == [("stop", stop["id"])], asks
    assert later not in [a["id"] for a in asks]
    http("POST", "/asks/done", {"ids": [stop["id"]]})
    assert stack.tab("Stop")["state"] == "thinking"  # a stop starts no turn of its own
    assert http("POST", "/stopped", {"chat": "Stop", "run_id": stop["run_id"], "ok": True})["ok"]
    t = wait_until(lambda: (lambda t: t if t["state"] == "idle" else None)(stack.tab("Stop")), what="idle after stop")
    assert t["note"] == "stopped" and not t.get("stopping") and t.get("trace") is None
    assert t["feed"][-1]["from"] == "stopped" and t["feed"][-1]["text"].startswith("1 steps"), t["feed"][-1]
    # the dispatcher's copy of the stopped turn's last text is no answer
    r = http("POST", "/reply", {"chat": "Stop", "text": "Half a plan", "fallback": True, "ask_id": first})
    assert r["skipped"] == "already_answered"


def test_a_stop_that_failed_keeps_the_turn(stack, mcp):
    ask_id = stack.ask("NoStop", "plan the whole rail network")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
    http("POST", "/asks/done", {"ids": [ask_id]})
    wait_until(lambda: stack.tab("NoStop")["state"] == "thinking", what="thinking")
    stop = stack.rcon.lua('return remote.call("kc", "stop", "NoStop")')
    assert [a["id"] for a in http("GET", "/asks?wait=5")["asks"]] == [stop["id"]]
    http("POST", "/asks/done", {"ids": [stop["id"]]})
    http("POST", "/stopped", {"chat": "NoStop", "run_id": stop["run_id"], "ok": False, "error": "HTTP 404"})
    t = wait_until(lambda: (lambda t: t if not t.get("stopping") else None)(stack.tab("NoStop")), what="stop failed")
    assert t["state"] == "thinking" and t["activity"] == "could not stop: HTTP 404", t
    # her answer still lands
    assert mcp.tool("factorio_reply", chat="NoStop", text="the plan")["delivered"]
    wait_until(lambda: stack.tab("NoStop")["feed"][-1].get("text") == "the plan", what="the answer after a failed stop")


def test_long_turn_progress_reaches_the_tab(stack, mcp):
    ask_id = stack.ask("Logo", "draw a logo with lamps")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
    http("POST", "/asks/done", {"ids": [ask_id]})
    wait_until(lambda: stack.tab("Logo")["state"] == "thinking", what="thinking")
    # tool calls count as steps even without a chat (factorio_query has none)
    assert mcp.tool("factorio_query", lua="return 1")["ok"]
    assert mcp.tool("factorio_query", lua="return 2")["ok"]
    # ...but not someone else polling the game (this test, tools/ui_dev.py): no _agent flag
    assert http("POST", "/query", {"lua": "return 0"})["ok"]
    assert mcp.tool("factorio_progress", chat="Logo", text="placing 140 lamp ghosts")["updated"] == 1
    # the dispatcher's live trace (what Kira writes between tool calls) reaches the tab
    assert http("POST", "/trace", {"chat": "Logo", "kind": "text", "text": "Planning the K first."})["sent"]
    wait_until(lambda: any(s["text"] == "Planning the K first." for s in stack.tab("Logo").get("trace") or []),
               what="trace line in the tab")
    t = wait_until(lambda: (lambda t: t if t.get("activity") else None)(stack.tab("Logo")), what="activity")
    assert t["steps"] == 2 and t["activity"] == "placing 140 lamp ghosts" and t["thinking_since"]
    # a long turn that keeps working is not "did not answer": each step restarts the idle clock
    for _ in range(4):
        time.sleep(1.5)
        assert mcp.tool("factorio_query", lua="return 3")["ok"]
    assert stack.tab("Logo")["state"] == "thinking", stack.tab("Logo")
    assert mcp.tool("factorio_reply", chat="Logo", text="done")["delivered"]
    t = wait_until(lambda: (lambda t: t if t["state"] == "idle" else None)(stack.tab("Logo")), what="idle")
    assert t.get("steps") is None and t.get("note") is None and t.get("trace") is None
    assert t["feed"][-2]["from"] == "trace_summary" and t["feed"][-1]["text"] == "done", t["feed"][-2:]
    # after the answer, a late trace line is dropped instead of reopening the turn
    assert http("POST", "/trace", {"chat": "Logo", "kind": "text", "text": "late"})["sent"] is False


def test_dispatchers_fallback_copy_is_dropped_after_a_real_answer(stack, mcp):
    # The dispatcher forwards the turn's last text when it sees no factorio_reply in the
    # transcript; it once did that after a real answer and the tab got both (Russian, then the
    # dashboard's English with [OPTIONS]). The bridge knows the turn answered and drops the copy.
    ask_id = stack.ask("Dup", "first question")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
    http("POST", "/asks/done", {"ids": [ask_id]})
    assert mcp.tool("factorio_reply", chat="Dup", text="Ответ")["delivered"]
    r = http("POST", "/reply", {"chat": "Dup", "text": "Answer (dashboard copy)", "fallback": True})
    assert r["skipped"] == "already_answered" and not r["delivered"]
    first = ask_id
    # the next question has no answer yet: its fallback is delivered, but a late copy of the
    # FIRST turn (what the tab got after a transcript rewrite) is not
    ask_id = stack.ask("Dup", "second question")
    assert [a["id"] for a in http("GET", "/asks?wait=10")["asks"]] == [ask_id]
    http("POST", "/asks/done", {"ids": [ask_id]})
    r = http("POST", "/reply", {"chat": "Dup", "text": "I rewrote the panel.", "fallback": True, "ask_id": first})
    assert r["skipped"] == "stale" and not r["delivered"]
    assert http("POST", "/reply", {"chat": "Dup", "text": "Всё отлично", "fallback": True, "ask_id": ask_id})["delivered"]
    t = wait_until(lambda: (lambda t: t if t["feed"][-1].get("text") == "Всё отлично" else None)(stack.tab("Dup")),
                   what="fallback answer")
    assert [e["text"] for e in t["feed"] if e["from"] == "kira"] == ["Ответ", "Всё отлично"]


def test_asks_wait_while_dispatcher_is_away(stack):
    a = stack.ask(CHAT, "first")
    b = stack.ask("Power", "second")
    wait_until(lambda: http("GET", "/health")["queued_asks"] == 2, what="two queued asks")
    # a dispatcher takes them and dies before confirming: they come back after the lease
    first = http("GET", "/asks?wait=5&lease=1")["asks"]
    assert [x["id"] for x in first] == [a, b]
    assert http("GET", "/asks?wait=0")["asks"] == []
    again = http("GET", "/asks?wait=5")["asks"]
    assert sorted(x["id"] for x in again) == sorted([a, b])
    http("POST", "/asks/done", {"ids": [a, b]})
    assert http("GET", "/health")["leased_asks"] == 0


def test_game_offline_and_reply_queue(stack, mcp):
    stack.pause_game()
    try:
        wait_until(lambda: not http("GET", "/health")["game_online"], timeout=10, what="bridge sees the game offline")
        r = mcp.tool("factorio_query", lua="return 1")
        assert r["error"] == "game_offline"
        q = mcp.tool("factorio_reply", chat=CHAT, text="an answer while the game is away")
        assert q["ok"] and q["queued"] and not q["delivered"]
        assert http("GET", "/health")["outbox"] >= 1
    finally:
        stack.resume_game()
    stack.wait_online()
    wait_until(lambda: stack.tab(CHAT)["feed"][-1]["text"] == "an answer while the game is away", what="queued reply delivered")
    assert http("GET", "/health")["outbox"] == 0


def test_no_pong_when_bridge_is_down_then_recovers(stack):
    wait_until(lambda: stack.mod_state()["connected"], what="connected before")
    stack.stop_bridge()
    t0 = time.monotonic()
    wait_until(lambda: not stack.mod_state()["connected"], timeout=15, what="mod notices missing pong")
    assert time.monotonic() - t0 > 1  # it was not already disconnected
    stack.start_bridge()
    stack.wait_online()
    wait_until(lambda: stack.mod_state()["connected"], timeout=15, what="mod reconnects by itself")
