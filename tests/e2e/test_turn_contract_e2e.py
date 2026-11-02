"""The turn contract with planted faults, a real gateway and a real model (docs/areas/bridge.md,
"Turn contract"): Kira's Stop hook, not the transcript, decides that a game turn ended."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from test_e2e import KC_HOME, Player, http  # noqa: E402
from watch import _lines, chat_slot, transcript  # noqa: E402


@pytest.fixture(scope="module")
def player():
    assert http("/health")["game_online"], "stand is not up: run scripts/test-e2e.sh"
    p = Player()
    yield p
    p.rcon.close()


def rows(chat):
    slot = chat_slot(KC_HOME, http("/health")["run_id"], chat)
    return list(_lines(transcript(KC_HOME, slot))) if slot else []


def test_a_turn_that_skips_factorio_reply_still_answers_in_the_game(player):
    # the planted fault: the model ends the turn in the chat only (gamer, 2026-11-01)
    text, _, t = player.ask_and_wait("NoReply", (
        "This is a test of the app. Answer only in the Kiro Crew chat with the single word pong, "
        "and do not call factorio_reply."))
    print(f"\nreply in the game ({t:.0f}s): {text}")
    assert "pong" in text.lower()
    # Kiro Crew writes the Stop hook's continuation as an `inject` row
    nudged = [r for r in rows("NoReply") if "Hook continuation" in str(r.get("content"))]
    assert nudged, "the Stop hook did not send the turn back to Kira"


def test_a_dispatcher_restart_mid_turn_does_not_lose_the_answer(player):
    # the planted fault: the dispatcher wake that sent the question exits while Kira works
    chat = "Restart"
    before = len([e for e in player.tab(chat)["feed"] if e.get("from") == "kira"])
    player.ask(chat, "Which surfaces does this map have? Read game.surfaces with a query first, then name them.")
    deadline = time.monotonic() + 60
    while player.tab(chat).get("state") != "thinking":
        assert time.monotonic() < deadline, "the question was not picked up"
        time.sleep(0.5)
    os.utime(KC_HOME / "crons" / "factorio_dispatch.py")  # the running wake sees a new script and exits
    deadline = time.monotonic() + 240
    while True:
        tab = player.tab(chat)
        kira = [e for e in tab["feed"] if e.get("from") == "kira"]
        if len(kira) > before:
            break
        assert time.monotonic() < deadline, f"no answer after the dispatcher restarted: {tab}"
        time.sleep(1)
    print(f"\nreply after a dispatcher restart: {kira[-1]['text']}")
    assert "nauvis" in kira[-1]["text"].lower()
    assert tab["state"] != "thinking"
