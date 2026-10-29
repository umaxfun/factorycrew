"""Atlas e2e with planted faults (docs/atlas.md, task 5), on the app's plain dev save: each case is
built by the test in an empty corner, so it needs no mods and no player save.

  scripts/test-app-e2e.sh -k planted

Every fault case has a control: the same build without the fault, the same question, and no false
cause in the answer. Entities are created with raise_built, as a player's build would raise it, so the
atlas sees the base changed.
"""
from __future__ import annotations

import re
import time

import pytest

from test_app_e2e import STAMP, Player, http, lua  # noqa: F401  (shared harness)
from test_uncoded import PRE, transcript

LONG = 360

MK = PRE + """
local function mk(name, x, y, dir)
  return s.create_entity{name = name, position = {x, y}, force = "player", direction = dir, raise_built = true}
end
local function wire_poles(a, b)
  local c = defines.wire_connector_id.pole_copper
  return a.get_wire_connector(c, true).connect_to(b.get_wire_connector(c, true), false)
end
local function infinite(x, y, item)
  local c = mk("infinity-chest", x, y)
  c.set_infinity_container_filter(1, {name = item, count = 100, mode = "at-least", index = 1})
  return c
end
"""

# Six gear assemblers in a row, each fed by its own inserter from an infinite iron chest above and
# emptied into a chest below. FAULTY: the input inserters of the first three are turned around.
ROW = MK + """
local X, Y, FAULTY = XX, YY, FF
clear(X - 12, Y - 8, X + 30, Y + 12)
local pole = grid(X - 8, Y + 6)
local s1, s2 = mk("substation", X + 5, Y + 6), mk("substation", X + 17, Y + 6)
wire_poles(pole, s1); wire_poles(s1, s2)
local ids = {}
for i = 0, 5 do
  local cx = X + 1.5 + 4 * i
  local m = mk("assembling-machine-2", cx, Y + 0.5)
  m.set_recipe("iron-gear-wheel")
  infinite(cx, Y - 2.5, "iron-plate")
  -- an inserter points at the tile it picks from: north = from the chest into the machine
  local turned = FAULTY and i < 3
  -- bulk inserters: a plain one moves less than the machine eats, and it would stand half the time
  mk("bulk-inserter", cx, Y - 1.5, turned and defines.direction.south or defines.direction.north)
  mk("bulk-inserter", cx, Y + 2.5, defines.direction.north)
  mk("wooden-chest", cx, Y + 3.5)
  ids[#ids + 1] = {cx, Y + 0.5}
end
return ids
"""

STATUS = """
local names = {}
for k, v in pairs(defines.entity_status) do names[v] = k end
local out = {}
for _, p in ipairs(PTS) do
  local e = game.surfaces[1].find_entity("assembling-machine-2", p)
  out[#out + 1] = e and names[e.status] or "gone"
end
return out
"""


@pytest.fixture(scope="module")
def kira():
    assert http("GET", "/health")["game_online"], "game not online on the app's bridge"
    return Player()


def build_row(x, y, faulty):
    pts = lua(ROW.replace("XX", str(x)).replace("YY", str(y)).replace("FF", "true" if faulty else "false"))
    time.sleep(12)  # let the good ones start
    st = lua(STATUS.replace("PTS", "{" + ",".join("{%s,%s}" % tuple(p) for p in pts) + "}"))
    return pts, st


def ask_with_box(kira, tab, box, question):
    kira.open_tab(tab)
    (x0, y0), (x1, y1) = box
    assert lua(f'return remote.call("kc", "point", {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}})')
    text, _, t_all = kira.ask_and_wait(tab, question, timeout=LONG)
    print(f"\nreply ({t_all:.0f}s): {text}")
    return text


TURNED = r"wrong way|backward|revers|rotat|turned|direction|facing|faces"


def test_planted_a_three_turned_inserters(kira):
    X, Y = 1300, 400
    pts, st = build_row(X, Y, faulty=True)
    assert all(s != "working" for s in st[:3]) and all(s == "working" for s in st[3:]), f"setup: {st}"
    text = ask_with_box(kira, f"Planted A {STAMP}", [[X - 0.5, Y - 1.5], [X + 11.5, Y + 2.5]],
                        "why don't these 3 machines @1 work?")
    assert "factorio_atlas" in transcript(f"Planted A {STAMP}")
    assert re.search(r"inserter", text, re.I) and re.search(TURNED, text, re.I), "the turned inserters are not named"
    assert re.search(r"other|rest|remaining|3 (of|more)|work", text, re.I), "does not say the rest of the block works"


def test_planted_a_control_no_false_cause(kira):
    X, Y = 1300, 440
    pts, st = build_row(X, Y, faulty=False)
    assert all(s == "working" for s in st), f"setup: {st}"
    text = ask_with_box(kira, f"Planted A0 {STAMP}", [[X - 0.5, Y - 1.5], [X + 11.5, Y + 2.5]],
                        "why don't these 3 machines @1 work?")
    assert not re.search(r"inserter[^.]*(" + TURNED + ")", text, re.I), "a cause that is not there"
    assert re.search(r"work|running|fine|no problem|nothing wrong", text, re.I)
