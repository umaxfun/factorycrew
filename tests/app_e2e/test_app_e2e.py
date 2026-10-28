"""App e2e: the packaged Kiro Crew app, end to end, on a real Factorio 2.0.77 client.

real client (tools/ui_dev.py, mod from dist/factorycrew) -> window handlers -> UDP -> bridge run
by the app backend -> dispatcher (the app's cron) -> chat in the dev gateway -> the app's agent
-> the app's MCP server -> bridge -> game.

Questions go in through the window's own event handlers (remote kc.ui_confirm fires
on_gui_confirmed), not RCON. scripts/test-app-e2e.sh prepares everything. Real model.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from conftest import lua_str, wait_until  # noqa: E402
from test_bridge import SETUP_CHESTS, TAGS  # noqa: E402

BRIDGE = os.environ.get("FC_APP_BRIDGE", "http://127.0.0.1:34298")
KC_HOME = Path(os.environ.get("FC_APP_HOME", Path.home() / ".kirocrew-factorio-app"))
ANSWER_TIMEOUT = float(os.environ.get("FC_E2E_ANSWER_TIMEOUT", "180"))
STAMP = time.strftime("%H%M%S")
# The dev save keeps its run_id between runs, so tab names are unique per run: fresh chats.
LOGI, RU = f"Logistics {STAMP}", f"Склад {STAMP}"

# The real client has a character near the origin; the shared scenario clears that area.
SETUP = SETUP_CHESTS.replace("e.destroy() end", 'if e.type ~= "character" then e.destroy() end end')


def http(method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BRIDGE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def lua(body):
    r = http("POST", "/query", {"lua": body, "timeout": 15})
    assert r.get("ok"), r
    return r.get("data")


def cyrillic(text):
    return any("\u0400" <= ch <= "\u04ff" for ch in text)


class Player:
    def ui(self, fn, *args):
        return lua(f'return remote.call({", ".join(lua_str(a) for a in ("kc", fn, *args))})')

    def tab(self, chat):
        return (lua('return remote.call("kc", "state")')["tabs"] or {}).get(chat) or {"feed": [], "state": "idle"}

    def open_tab(self, chat):
        self.ui("ui_open")
        if chat in (lua('return remote.call("kc", "state")')["tabs"] or {}):
            assert self.ui("ui_click", f"kc_tab:{chat}")
            return
        assert self.ui("ui_click", "kc_tab_add")
        assert self.ui("ui_confirm", "kc_new_tab_input", chat)

    def ask_and_wait(self, chat, text):
        self.open_tab(chat)
        before = len([e for e in self.tab(chat)["feed"] if e.get("from") == "kira"])
        t0 = time.monotonic()
        assert self.ui("ui_confirm", "kc_input", text), "no input field"
        wait_until(lambda: self.tab(chat)["state"] == "thinking", timeout=90, step=0.3,
                   what=f"dispatcher picked up {text!r}")
        t_pick = time.monotonic() - t0

        def answered():
            kira = [e for e in self.tab(chat)["feed"] if e.get("from") == "kira"]
            return kira[-1] if len(kira) > before else None
        reply = wait_until(answered, timeout=ANSWER_TIMEOUT, step=1, what=f"reply to {text!r}")
        return reply["text"], t_pick, time.monotonic() - t0

    def kc_tags(self):
        tags = lua(TAGS.replace("surface", "game.surfaces[1]"))
        kc = tags.get("kc") or []
        return [] if kc == {} else kc, tags.get("other") or []


@pytest.fixture(scope="module")
def player():
    assert http("GET", "/health")["game_online"], "game not online on the app's bridge"
    made = lua(f"return (function() {SETUP} end)()")
    p = Player()
    p.expected = [{"x": c["x"], "y": c["y"]} for c in made if not c["filtered"]]
    assert len(p.expected) == 7
    yield p


def near(a, b):
    return abs(a["x"] - b["x"]) <= 1 and abs(a["y"] - b["y"]) <= 1


def test_chests_marked_through_the_app(player):
    text, t_pick, t_all = player.ask_and_wait(LOGI, "find yellow chests without a filter and mark them on the map")
    print(f"\nreply ({t_pick:.1f}s to pickup, {t_all:.1f}s total): {text}")
    kc, other = player.kc_tags()
    assert len(kc) == 7, kc
    assert all(any(near(c, t) for t in kc) for c in player.expected), kc
    assert "7" in text and not cyrillic(text)
    assert [o["text"] for o in other] == ["my base"]


def test_follow_up_uses_chat_context(player):
    text, _, t_all = player.ask_and_wait(LOGI, "how many did you mark? answer with one number")
    print(f"\nreply ({t_all:.1f}s): {text}")
    assert "7" in text


def test_trash_button_removes_marks(player):
    player.open_tab(LOGI)
    assert player.ui("ui_click", "kc_mark_clear")
    wait_until(lambda: player.kc_tags()[0] == [], timeout=10, step=0.5, what="marks removed by the button")
    assert [o["text"] for o in player.kc_tags()[1]] == ["my base"]


def test_answers_in_the_players_language(player):
    text, _, t_all = player.ask_and_wait(RU, "Сколько всего на карте жёлтых сундуков? Ответь одной короткой фразой.")
    print(f"\nreply in Russian ({t_all:.1f}s): {text}")
    assert cyrillic(text) and "20" in text


def test_tab_from_an_earlier_install_still_answers(player):
    """'General' keeps its chat across runs, and every run reinstalls the app (a new cron id):
    the dispatcher must replace the chat the old job opened instead of failing with not_creator."""
    text, _, t_all = player.ask_and_wait("General", "say hi in one word")
    print(f"\nGeneral reply ({t_all:.1f}s): {text}")
    assert "not_creator" not in text and "Could not pass" not in text, text


def test_long_build_shows_progress(player):
    """A minute-long build: the window must show time, steps and what Kira does while she works."""
    tab = f"Build {STAMP}"
    me = lua('local p = game.get_player(1) return {x = p.position.x, y = p.position.y}')
    player.open_tab(tab)
    t0 = time.monotonic()
    assert player.ui("ui_confirm", "kc_input", "draw kirocrew ghost logo with colored lamps at about 1 chunk of size")
    seen, shot = {"steps": 0, "activity": set(), "secs": 0}, False
    while time.monotonic() - t0 < 300:
        t = player.tab(tab)
        if t.get("steps"):
            seen["steps"] = max(seen["steps"], t["steps"])
        if t.get("activity"):
            seen["activity"].add(t["activity"])
        for s in t.get("trace") or []:
            seen.setdefault("trace", {})[s["text"]] = s["kind"]
        if t["state"] == "thinking" and t.get("thinking_since"):
            seen["secs"] = int(time.monotonic() - t0)
            if not shot and seen["activity"] and seen["secs"] > 20:
                lua('game.take_screenshot{player = game.get_player(1), show_gui = true, resolution = {1600, 1000}, '
                    'path = "kc/app-e2e-progress.png"} return true')
                shot = True
        kira = [e for e in t["feed"] if e.get("from") == "kira"]
        if kira:
            break
        time.sleep(1.5)
    total = time.monotonic() - t0
    print(f"\nbuild reply ({total:.0f}s, max step {seen['steps']}, activity {sorted(seen['activity'])}): "
          f"{kira[-1]['text'] if kira else None}")
    assert kira, "no reply in 300 s"
    assert seen["steps"] >= 2 and seen["activity"], seen
    # Kira's own words between tool calls reached the tab WHILE she worked, not only at the end
    trace = seen.get("trace") or {}
    print(f"trace lines seen live: {len(trace)}; e.g. {list(trace)[:3]}")
    assert any(k == "text" for k in trace.values()) and any(k == "tool" for k in trace.values()), trace
    assert t["feed"][-2]["from"] == "trace_summary", t["feed"][-2:]
    ghosts = lua(f'return #game.surfaces[1].find_entities_filtered{{name = "entity-ghost", ghost_name = "small-lamp", '
                 f'position = {{{me["x"]}, {me["y"]}}}, radius = 96}}')
    print(f"lamp ghosts within 96 tiles of the player: {ghosts}")
    assert ghosts >= 20


def test_it_was_the_apps_agent(player):
    """The chats live in the dev gateway and the turns used the app's own MCP server."""
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slots = [v for k, v in st["slots"].items() if k.endswith(f"\t{LOGI}")]
    assert len(slots) == 1, st["slots"]
    transcript = (KC_HOME / "sessions" / f"dashboard_{slots[0]}.jsonl").read_text(encoding="utf-8")
    assert "@factorycrew:factorio/factorio_mark" in transcript
    assert "@factorycrew:factorio/factorio_reply" in transcript
