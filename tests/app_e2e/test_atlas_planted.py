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


# Case C: two gear assemblers fed from a train stop. A double-headed train runs between a loading stop
# at the west end and the unloading stop; FAULT: the loading stop's chests are empty, so the train
# waits there forever. The control fills those chests.
TRAIN = MK + """
local X, Y, TAG = XX, YY, "TT"
clear(X - 10, Y - 14, X + 80, Y + 16)
for x = X, X + 70, 2 do mk("straight-rail", x, Y, defines.direction.east) end
local load = mk("train-stop", X + 6, Y - 2, defines.direction.west)
load.backer_name = "Load " .. TAG
local unload = mk("train-stop", X + 60, Y + 2, defines.direction.east)
unload.backer_name = "Unload " .. TAG
local pole = grid(X + 28, Y + 10)
local b1, b2 = mk("big-electric-pole", X + 12, Y - 6), mk("big-electric-pole", X + 48, Y + 12)
wire_poles(pole, b1); wire_poles(pole, b2)
local s1, s2 = mk("substation", X + 10, Y - 8), mk("substation", X + 52, Y + 9)
wire_poles(b1, s1); wire_poles(b2, s2)
-- the train starts at Unload, running east: [west-facing loco, wagon, east-facing loco]
local le = mk("locomotive", X + 56, Y, defines.direction.east)
-- rolling stock snaps along the rail: place each car 7 tiles behind where the previous one landed
local wg = mk("cargo-wagon", le.position.x - 7, Y, defines.direction.east)
local lw = mk("locomotive", wg.position.x - 7, Y, defines.direction.west)
if #le.train.carriages < 3 then
  lw.connect_rolling_stock(defines.rail_direction.front)
  lw.connect_rolling_stock(defines.rail_direction.back)
end
le.insert{name = "coal", count = 50}; lw.insert{name = "coal", count = 50}
local c = wg.position
local ids = {}
for i, x in ipairs({math.floor(c.x) - 1.5, math.floor(c.x) + 1.5}) do
  mk("inserter", x, c.y + 1.5, defines.direction.north)      -- from the wagon into a chest
  mk("wooden-chest", x, c.y + 2.5)
  mk("bulk-inserter", x, c.y + 3.5, defines.direction.north) -- chest into the machine
  local m = mk("assembling-machine-2", x, c.y + 5.5)
  m.set_recipe("iron-gear-wheel")
  mk("bulk-inserter", x, c.y + 7.5, defines.direction.north)
  mk("wooden-chest", x, c.y + 8.5)
  ids[#ids + 1] = {x, c.y + 5.5}
end
local t = le.train
local sch = t.get_schedule()
sch.add_record{station = "Load " .. TAG, wait_conditions = {{type = "item_count", compare_type = "or",
  condition = {first_signal = {type = "item", name = "iron-plate"}, comparator = ">=", constant = 200}}}}
sch.add_record{station = "Unload " .. TAG, wait_conditions = {{type = "empty", compare_type = "or"}}}
sch.go_to_station(1)
t.manual_mode = false
return {machines = ids, train = t.id}
"""

AT_LOAD = """
local t = game.train_manager.get_train_by_id(TID)
local st = t.station
return st and st.backer_name or false
"""

# once the train stands at Load: chests and inserters beside its wagon (full in the control)
LOAD_SIDE = MK + """
local t = game.train_manager.get_train_by_id(TID)
local wg = t.cargo_wagons[1]
local c = wg.position
for _, x in ipairs({math.floor(c.x) - 1.5, math.floor(c.x) + 0.5, math.floor(c.x) + 2.5}) do
  local chest = mk("wooden-chest", x, c.y - 2.5)
  if FULL then chest.insert{name = "iron-plate", count = 800} end
  mk("bulk-inserter", x, c.y - 1.5, defines.direction.north)  -- from the chest onto the wagon
end
return true
"""

GEARS = """
local n = 0
for _, p in ipairs(PTS) do
  local c = game.surfaces[1].find_entity("wooden-chest", {p[1], p[2] + 3})
  n = n + c.get_item_count("iron-gear-wheel")
end
return n
"""


def build_train(x, y, tag, full):
    made = lua(TRAIN.replace("XX", str(x)).replace("YY", str(y)).replace("TT", tag))
    tid = made["train"]
    deadline = time.time() + 90
    while time.time() < deadline and lua(AT_LOAD.replace("TID", str(tid))) != f"Load {tag}":
        time.sleep(2)
    assert lua(AT_LOAD.replace("TID", str(tid))) == f"Load {tag}", "setup: the train never reached Load"
    lua(LOAD_SIDE.replace("TID", str(tid)).replace("FULL", "true" if full else "false"))
    pts = "{" + ",".join("{%s,%s}" % tuple(p) for p in made["machines"]) + "}"
    return made, pts


def test_planted_c_empty_loading_station(kira):
    X, Y = 1300, 600
    made, pts = build_train(X, Y, f"C{STAMP}", full=False)
    time.sleep(20)
    assert lua(AT_LOAD.replace("TID", str(made["train"]))) == f"Load C{STAMP}", "setup: the train left Load"
    assert lua(GEARS.replace("PTS", pts)) == 0, "setup: gears were made"
    (mx, my) = made["machines"][0]
    text = ask_with_box(kira, f"Planted C {STAMP}", [[mx - 1.5, my - 1.5], [mx + 1.5, my + 1.5]],
                        "why don't these machines @1 work?")
    assert "factorio_atlas" in transcript(f"Planted C {STAMP}")
    assert re.search(r"train", text, re.I), "the train is not mentioned"
    assert re.search(r"load", text, re.I) and re.search(r"empty|no iron|nothing", text, re.I), \
        "it does not follow the train to the empty loading stop"


def test_planted_c_control_full_loading_station(kira):
    X, Y = 1300, 650
    made, pts = build_train(X, Y, f"D{STAMP}", full=True)
    deadline = time.time() + 150
    while time.time() < deadline and lua(GEARS.replace("PTS", pts)) == 0:
        time.sleep(3)
    assert lua(GEARS.replace("PTS", pts)) > 0, "setup: the control never made gears"
    (mx, my) = made["machines"][0]
    text = ask_with_box(kira, f"Planted C0 {STAMP}", [[mx - 1.5, my - 1.5], [mx + 1.5, my + 1.5]],
                        "why don't these machines @1 work?")
    assert not re.search(r"load\\w*[^.]*(empty|no iron)|(empty|no iron)[^.]*load", text, re.I), \
        "an empty loading stop that is not there"
