"""Level 3, automatic part: the full cycle through a real Kiro Crew gateway.

game (headless 2.1 in Docker, RCON plays the player) -> mod -> UDP -> bridge (same container)
-> HTTP -> dispatcher (script-cron in the factorycrew gateway) -> chat Factorio/<run>/<tab>
-> agent factorio-companion -> MCP factorio -> bridge -> game.

Needs: `scripts/kc.sh start`, the dispatcher cron registered, the stand container up with
bridge HTTP on 127.0.0.1:34198 and RCON on 127.0.0.1:27015 (scripts/test-e2e.sh does it).
Uses a real model: each test costs a few agent turns.
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
from conftest import Rcon, lua_str, wait_until  # noqa: E402

BRIDGE = "http://127.0.0.1:34198"
KC_HOME = Path(os.environ.get("KIROCREW_HOME", Path.home() / ".kirocrew-factorio"))
ANSWER_TIMEOUT = float(os.environ.get("FC_E2E_ANSWER_TIMEOUT", "180"))

from test_bridge import SETUP_CHESTS, TAGS  # noqa: E402  (same 20-chest scenario as level 2)


def http(path):
    with urllib.request.urlopen(BRIDGE + path, timeout=10) as r:
        return json.loads(r.read())


class Player:
    def __init__(self):
        self.rcon = Rcon()

    def lua(self, body):
        return self.rcon.lua(body)

    def state(self):
        return self.lua('return remote.call("kc", "state")')

    def tab(self, chat):
        return (self.state()["tabs"] or {}).get(chat) or {"feed": [], "state": "idle"}

    def ask(self, chat, text):
        return self.lua(f'return remote.call("kc", "ask", {lua_str(chat)}, {lua_str(text)})')

    def ask_and_wait(self, chat, text):
        """Ask from the game; return (kira reply text, seconds until thinking, seconds until reply)."""
        before = len([e for e in self.tab(chat)["feed"] if e.get("from") == "kira"])
        t0 = time.monotonic()
        self.ask(chat, text)
        wait_until(lambda: self.tab(chat)["state"] == "thinking", timeout=90, step=0.2,
                   what=f"dispatcher picked up {text!r}")
        t_pick = time.monotonic() - t0

        def answered():
            kira = [e for e in self.tab(chat)["feed"] if e.get("from") == "kira"]
            return kira[-1] if len(kira) > before else None
        reply = wait_until(answered, timeout=ANSWER_TIMEOUT, step=1, what=f"reply to {text!r}")
        return reply["text"], t_pick, time.monotonic() - t0

    def kc_tags(self):
        tags = self.lua(TAGS.replace("surface", "game.surfaces[1]"))
        kc = tags.get("kc") or []
        return [] if kc == {} else kc, tags.get("other") or []


@pytest.fixture(scope="module")
def player():
    assert http("/health")["game_online"], "stand is not up: run scripts/test-e2e.sh"
    p = Player()
    # a fresh stand is a new run; name it so its folder is easy to find in the sidebar
    p.lua(f'remote.call("kc", "set_run_name", {lua_str(time.strftime("e2e %m-%d %H:%M"))}) return true')
    made = p.lua(f"return (function() {SETUP_CHESTS} end)()")
    p.expected = {(round(c["x"], 2), round(c["y"], 2)) for c in made if not c["filtered"]}
    assert len(p.expected) == 7
    yield p
    p.rcon.close()


def key(c):
    return (round(c["x"], 2), round(c["y"], 2))


def cyrillic(text):
    return any("\u0400" <= ch <= "\u04ff" for ch in text)


def test_chests_without_filter_are_marked(player):
    text, t_pick, t_all = player.ask_and_wait(
        "Logistics", "find yellow chests without a filter and mark them on the map")
    print(f"\nreply ({t_pick:.1f}s to pickup, {t_all:.1f}s total): {text}")
    kc, other = player.kc_tags()
    # The agent may round positions; a mark counts if it is within a tile of the chest.
    near = lambda c, t: abs(c[0] - t["x"]) <= 1 and abs(c[1] - t["y"]) <= 1  # noqa: E731
    assert len(kc) == 7, kc
    assert all(any(near(c, t) for t in kc) for c in player.expected), kc
    assert "7" in text
    assert not cyrillic(text), "asked in English, answered in another language"
    assert [o["text"] for o in other] == ["my base"]


def test_follow_up_uses_chat_context(player):
    text, _, t_all = player.ask_and_wait("Logistics", "how many did you mark? answer with one number")
    print(f"\nreply ({t_all:.1f}s): {text}")
    assert "7" in text


def test_remove_marks(player):
    text, _, t_all = player.ask_and_wait("Logistics", "remove the marks")
    print(f"\nreply ({t_all:.1f}s): {text}")
    kc, other = player.kc_tags()
    assert kc == []
    assert [o["text"] for o in other] == ["my base"]


def test_other_tab_is_a_separate_chat(player):
    tab = time.strftime("Trains %H:%M:%S")  # a tab this chat history has never seen
    text, t_pick, t_all = player.ask_and_wait(
        tab, "what did I ask in this chat before this question? if nothing, answer with the single word NOTHING")
    print(f"\nnew tab reply ({t_pick:.1f}s to pickup, {t_all:.1f}s total): {text}")
    assert "nothing" in text.lower()
    assert "chest" not in text.lower()


def test_answers_in_the_players_language(player):
    tab = time.strftime("Склад %H:%M:%S")
    text, _, t_all = player.ask_and_wait(tab, "Сколько всего на карте жёлтых сундуков? Ответь одной короткой фразой.")
    print(f"\nreply in Russian ({t_all:.1f}s): {text}")
    assert cyrillic(text), "asked in Russian, answered in another language"
    assert "20" in text
