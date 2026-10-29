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

# Case B: copper cable and iron plate come on two feeder belts that side-load from both sides onto one
# yellow trunk, so each gets one lane (7.5/s). N circuit assemblers (am2: 4.5 cable/s each) pick from
# the trunk. With 3 the cable lane is short (13.5/s needed); with 1 (the control) it is enough.
LANES = MK + """
local X, Y, N = XX, YY, NN
f.recipes["electronic-circuit"].enabled = true  -- the dev map has nothing researched (Kira said so once)
clear(X - 16, Y - 14, X + 20, Y + 12)
local pole = grid(X - 14, Y - 6)
local s1, s2 = mk("substation", X - 8, Y - 6), mk("substation", X + 6, Y + 3)
wire_poles(pole, s1); wire_poles(s1, s2)
local E, S, W, Nn = defines.direction.east, defines.direction.south, defines.direction.west, defines.direction.north
for x = -4, 14 do mk("transport-belt", X + x + 0.5, Y - 3.5, E) end           -- trunk, east, from the junction
for y = -10, -5 do mk("transport-belt", X - 3.5, Y + y + 0.5, S) end            -- cable feeder from the north
for y = -3, 2 do mk("transport-belt", X - 3.5, Y + y + 0.5, Nn) end             -- iron feeder from the south
-- four bulk inserters per feeder from infinite chests, two on each side
for _, f in ipairs({{y = Y - 9.5, item = "copper-cable"}, {y = Y - 8.5, item = "copper-cable"},
                    {y = Y + 1.5, item = "iron-plate"}, {y = Y + 0.5, item = "iron-plate"}}) do
  infinite(X - 5.5, f.y, f.item); mk("bulk-inserter", X - 4.5, f.y, W)
  infinite(X - 1.5, f.y, f.item); mk("bulk-inserter", X - 2.5, f.y, E)
end
local ids = {}
for i = 0, N - 1 do
  local cx = X + 1.5 + 4 * i
  local m = mk("assembling-machine-2", cx, Y - 0.5)
  m.set_recipe("electronic-circuit")
  -- three inserters from the trunk: one bulk inserter off a belt moves ~3/s and was itself the limit
  for dx = -1, 1 do mk("bulk-inserter", cx + dx, Y - 2.5, Nn) end
  mk("bulk-inserter", cx, Y + 1.5, Nn)   -- out of the machine into the chest
  mk("wooden-chest", cx, Y + 2.5)
  ids[#ids + 1] = {cx, Y - 0.5}
end
return ids
"""


def circuits_per_s(x, y, n, secs=20):
    """circuits reaching the output chests per second: the block's real rate"""
    q = (f'local t = 0 for i = 0, {n - 1} do local c = game.surfaces[1].find_entity("wooden-chest", '
         f'{{{x} + 1.5 + 4 * i, {y} + 2.5}}) t = t + c.get_item_count("electronic-circuit") end return t')
    a = lua(q)
    time.sleep(secs)
    return (lua(q) - a) / secs


def test_planted_b_two_inputs_on_one_lane(kira):
    X, Y = 1300, 480
    pts = lua(LANES.replace("XX", str(X)).replace("YY", str(Y)).replace("NN", "3"))
    time.sleep(25)  # the trunk fills up
    rate = circuits_per_s(X, Y, 3)
    assert rate < 0.7 * 4.5, f"setup: the block is not starving: {rate:.2f}/s of 4.5"
    text = ask_with_box(kira, f"Planted B {STAMP}", [[X + 0.5, Y - 2], [X + 2.5, Y + 1]],
                        "why is this circuit block @1 slow?")
    log = transcript(f"Planted B {STAMP}")
    assert "factorio_atlas" in log
    assert re.search(r"lane|полос", text, re.I), "the one-lane limit is not named"
    assert re.search(r"7[.,]5", text) and re.search(r"13[.,]5|\b14\b", text), "no lane vs need numbers"
    assert re.search(r"all|whole|3 machines|every|block", text, re.I)


def test_planted_b_control_one_machine_has_enough(kira):
    X, Y = 1300, 520
    pts = lua(LANES.replace("XX", str(X)).replace("YY", str(Y)).replace("NN", "1"))
    time.sleep(25)
    rate = circuits_per_s(X, Y, 1)
    assert rate >= 0.85 * 1.5, f"setup: one machine should run near full speed: {rate:.2f}/s of 1.5"
    text = ask_with_box(kira, f"Planted B0 {STAMP}", [[X + 0.5, Y - 2], [X + 2.5, Y + 1]],
                        "is this circuit block @1 slow?")
    assert not re.search(r"(short|starv|not enough|too little)[^.]*lane|lane[^.]*(short|starv|not enough)", text, re.I), \
        "a lane shortage that is not there"
    assert re.search(r"work|running|fine|no problem|not slow|enough", text, re.I)


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
