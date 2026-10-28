"""Dispatcher pieces that need no gateway."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dispatcher"))
import factorio_dispatch as d  # noqa: E402


def test_reply_language_from_the_question_text():
    assert d.reply_language("draw kirocrew ghost logo with colored lamps") == "English"
    assert d.reply_language("Сколько всего жёлтых сундуков?") == "Russian or another Cyrillic language"
    assert d.reply_language("find the storage-chest near (12, -4)") == "English"
    assert d.reply_language("12345 ?!") is None
    assert d.reply_language("où est le dépôt") is None  # Latin with accents: let the model decide


def test_header_says_the_language_and_not_the_run_name():
    msg = d.render({"run_id": "r", "run_name": "Ран 1", "chat": "Build", "tick": 5, "text": "draw a logo",
                    "player": {"name": "p", "surface": "nauvis", "x": 1, "y": 2}})
    assert msg.startswith('[Factorio · run "Ран 1" · tab "Build" · tick 5 · player p at (1, 2) on nauvis · reply in English]')
