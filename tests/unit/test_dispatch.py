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


def test_pointers_go_as_lines_between_header_and_question():
    msg = d.render({"run_id": "r", "run_name": "R", "chat": "Build", "tick": 5, "text": "why is @1 slow? build like @2",
                    "player": {"name": "p", "surface": "nauvis", "x": 1, "y": 2},
                    "pointers": [
                        {"n": 1, "kind": "point", "surface": "nauvis", "x": 12.5, "y": -30.5, "tile": "grass-1",
                         "entity": {"name": "assembling-machine-2", "type": "assembling-machine", "x": 12.5,
                                    "y": -30.5, "recipe": "electronic-circuit", "status": "item_ingredient_shortage"},
                         "resource": "iron-ore"},
                        {"n": 2, "kind": "box", "surface": "nauvis", "area": [10, -40, 40, -20], "w": 30, "h": 20,
                         "count": 42, "counts": [{"name": "transport-belt", "n": 30}, {"name": "train-stop", "n": 1}],
                         "recipes": [{"name": "electronic-circuit", "n": 8}],
                         "resources": [{"name": "iron-ore", "amount": 120000.0}]},
                        {"n": 3, "kind": "point", "surface": "nauvis", "x": 0.0, "y": 3.0, "tile": "sand-1"},
                    ]})
    lines = msg.split("\n")
    assert lines[0].startswith("[Factorio · run")
    assert lines[1] == ("[@1: click at (12.5, -30.5) on nauvis; assembling-machine-2 (assembling-machine) at (12.5, -30.5), "
                        "recipe electronic-circuit, status item_ingredient_shortage, on iron-ore]")
    assert lines[2] == ("[@2: box (10, -40)-(40, -20), 30×20 on nauvis; 42 entities: 30 transport-belt, 1 train-stop; "
                        "recipes: 8 electronic-circuit; ore: iron-ore 120000]")
    assert lines[3] == "[@3: click at (0, 3) on nauvis; empty ground (sand-1)]"
    assert lines[4] == "why is @1 slow? build like @2"


def test_no_pointers_no_extra_lines():
    msg = d.render({"run_id": "r", "chat": "T", "tick": 1, "text": "hi"})
    assert msg.split("\n")[1] == "hi"


# ── a turn that ends without factorio_reply ─────────────────────────────

import json  # noqa: E402


def _turn(tmp_path, monkeypatch, rows):
    """Run the tracer over one turn's transcript rows; return what it posted to the bridge."""
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    posted = []
    monkeypatch.setattr(d, "bridge", lambda cfg, method, path, body=None, timeout=40.0: posted.append((path, body)) or {})
    t = d.Tracer({})
    t.start("s1", {"chat": "General", "run_id": "r"})
    with open(d.transcript_path("s1"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    t.poll()
    return posted, t


def test_forwards_the_last_text_when_the_turn_skipped_factorio_reply(tmp_path, monkeypatch):
    posted, t = _turn(tmp_path, monkeypatch, [
        {"role": "user", "content": "[Factorio ...]\nкак дела?"},
        {"role": "assistant", "content": "Всё отлично! Готова помочь.", "meta": {"turn_stats": {"credits": 0.16}}},
    ])
    assert ("/reply", {"chat": "General", "text": "Всё отлично! Готова помочь.", "fallback": True, "ask_id": None}) in posted
    assert t.watch == {}


def test_does_not_forward_when_factorio_reply_was_called(tmp_path, monkeypatch):
    posted, _ = _turn(tmp_path, monkeypatch, [
        {"role": "assistant", "content": "Looking."},
        {"role": "tool", "content": "🔧 @factorycrew:factorio/factorio_reply", "meta": {"mcp_server": "factorycrew:factorio"}},
        {"role": "assistant", "content": "Done, answered in the game.", "meta": {"turn_stats": {"credits": 0.8}}},
    ])
    assert [p for p, _ in posted if p == "/reply"] == []


def test_an_earlier_turn_met_again_after_a_rewrite_is_not_forwarded(tmp_path, monkeypatch):
    # The gateway rewrote the transcript when the question came, the old offset fell inside the
    # previous turn, and its English chat text went to the game as this turn's answer.
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    posted = []
    monkeypatch.setattr(d, "bridge", lambda cfg, method, path, body=None, timeout=40.0: posted.append((path, body)) or {})
    path = d.transcript_path("s1")
    t = d.Tracer({})
    t.start("s1", {"chat": "панелька", "run_id": "r", "id": "ask-2"}, since=1_795_000_000)  # 2026-11-18: between the two rows
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_type": "metadata", "title": "панелька"}) + "\n")
        f.write(json.dumps({"role": "assistant", "content": "I rewrote the panel.", "ts": "2026-10-29T21:12:09+00:00",
                            "meta": {"turn_stats": {"credits": 1}}}) + "\n")
    t.poll()
    assert [p for p, _ in posted if p == "/reply"] == [] and "s1" in t.watch
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "content": "[Factorio ...]\nсделай панельку", "ts": "2027-01-15T07:59:50+00:00"}) + "\n")
        f.write(json.dumps({"role": "assistant", "content": "Готово.", "ts": "2027-01-15T08:00:00+00:00",
                            "meta": {"turn_stats": {"credits": 1}}}) + "\n")
    t.poll()
    assert ("/reply", {"chat": "панелька", "text": "Готово.", "fallback": True, "ask_id": "ask-2"}) in posted


def _quick_follow_up(tmp_path, monkeypatch, second_turn):
    """Turn 1 answered with factorio_reply; the player asked again before the gateway wrote turn
    1's closing row. File order, as in a real transcript (app e2e, consent test, 2026-10-29):
    turn 1's rows, [the dispatcher sends question 2], turn 1's closing row, question 2's user row,
    turn 2's rows."""
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    posted = []
    monkeypatch.setattr(d, "bridge", lambda cfg, method, path, body=None, timeout=40.0: posted.append((path, body)) or {})
    path = d.transcript_path("s1")
    with open(path, "w", encoding="utf-8") as f:
        for r in [{"role": "user", "content": "[Factorio ...]\nremove the last 3 chests", "ts": "2026-10-29T23:33:30+00:00"},
                  {"role": "tool", "content": "🔧 @factorycrew:factorio/factorio_reply", "ts": "2026-10-29T23:34:05+00:00"}]:
            f.write(json.dumps(r) + "\n")
    t = d.Tracer({})
    pos = t.offset("s1")  # taken before the question is sent
    t.start("s1", {"chat": "Consent", "run_id": "r", "id": "ask-2"}, since=1_793_316_846, pos=pos)  # 23:34:06
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "assistant", "content": "I found 1 row of 20 chests. Say yes first.",
                            "ts": "2026-10-29T23:34:07.85+00:00", "meta": {"turn_stats": {"credits": 0.1}}}) + "\n")
        f.write(json.dumps({"role": "user", "content": "[Factorio ...]\nyes", "ts": "2026-10-29T23:34:07.86+00:00"}) + "\n")
        for r in second_turn:
            f.write(json.dumps(r) + "\n")
    t.poll()
    return [b for p, b in posted if p == "/reply"]


def test_a_quick_follow_up_does_not_get_the_previous_turns_text(tmp_path, monkeypatch):
    # The window showed turn 1's chat text 1.5 s after "yes", before Kira had acted on it.
    replies = _quick_follow_up(tmp_path, monkeypatch, [
        {"role": "tool", "content": "🔧 @factorycrew:factorio/factorio_reply", "ts": "2026-10-29T23:34:13+00:00"},
        {"role": "assistant", "content": "I marked the 3 chests.", "ts": "2026-10-29T23:34:15+00:00",
         "meta": {"turn_stats": {"credits": 0.1}}}])
    assert replies == []


def test_a_quick_follow_up_that_skips_factorio_reply_forwards_its_own_text(tmp_path, monkeypatch):
    replies = _quick_follow_up(tmp_path, monkeypatch, [
        {"role": "assistant", "content": "Marked them.", "ts": "2026-10-29T23:34:15+00:00",
         "meta": {"turn_stats": {"credits": 0.1}}}])
    assert replies == [{"chat": "Consent", "text": "Marked them.", "fallback": True, "ask_id": "ask-2"}]


def test_forwards_earlier_text_when_the_last_message_is_empty(tmp_path, monkeypatch):
    posted, _ = _turn(tmp_path, monkeypatch, [
        {"role": "user", "content": "[Factorio ...]\nwhy is the furnace idle?"},
        {"role": "assistant", "content": "The furnace needs coal."},
        {"role": "assistant", "content": "", "meta": {"turn_stats": {"credits": 0.2}}},
    ])
    assert ("/reply", {"chat": "General", "text": "The furnace needs coal.", "fallback": True, "ask_id": None}) in posted


def test_keeps_watching_while_the_turn_runs(tmp_path, monkeypatch):
    posted, t = _turn(tmp_path, monkeypatch, [{"role": "user", "content": "[Factorio ...]\ncheck my belts"},
                                              {"role": "assistant", "content": "Checking the belts."}])
    assert [p for p, _ in posted if p == "/reply"] == [] and "s1" in t.watch


def test_trace_lines_carry_kiras_text_and_tool_labels():
    """What the window's live trace shows for each transcript row. The app e2e only sees text lines
    when the model happens to write between tool calls, so the mapping is pinned here."""
    long = "checking the belts " * 30
    (kind, text, done), = d.trace_lines({"role": "assistant", "content": "  Reading the\nstations  "})
    assert (kind, text, done) == ("text", "Reading the stations", False)
    (kind, text, _), = d.trace_lines({"role": "assistant", "content": long})
    assert kind == "text" and len(text) == d.TRACE_MAX and text.endswith("…")
    assert d.trace_lines({"role": "tool", "content": "🔧 @factorycrew:factorio/factorio_reply"}) == [("", "", True)]
    assert d.trace_lines({"role": "tool", "content": "🔧 @factorycrew:factorio/factorio_progress"}) == []
    (kind, label, _), = d.trace_lines({"role": "tool", "content": "🔧 @factorycrew:factorio/factorio_atlas"})
    assert kind == "tool" and label
    assert d.trace_lines({"role": "assistant", "content": "   "}) == []
