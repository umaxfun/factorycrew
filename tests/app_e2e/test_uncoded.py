"""Uncoded tasks: requests no skill solves directly. Kira has to compose the kc primitives
(kc.scan/proto, kc.circuits, kc.wires/cover/checks, belts, factorio_compute) herself.

Every check is made against the game, not the wording of the reply: ghosts are revived by the test
and must work (powered, carrying items), diagnoses must name the planted cause, and the world must
be unchanged where the player said "don't change anything". Runs on the real 2.0.77 client through
the app (scripts/test-app-e2e.sh -k uncoded). Real model.
"""
from __future__ import annotations

import json
import time

import pytest

from test_app_e2e import KC_HOME, STAMP, Player, http, lua  # noqa: F401  (shared harness)
from conftest import wait_until  # noqa: E402

LONG = 360  # seconds: diagnosis and building turns are long

PRE = """
local s, f = game.surfaces[1], game.forces.player
local W = defines.wire_connector_id
local function sig(t, n) return {type = t, name = n} end
local function wire(a, ca, b, cb) return a.get_wire_connector(ca, true).connect_to(b.get_wire_connector(cb, true), false) end
local function make(name, x, y, dir) return s.create_entity{name = name, position = {x, y}, force = "player", direction = dir} end
local function clear(x0, y0, x1, y1)
  s.request_to_generate_chunks({(x0 + x1) / 2, (y0 + y1) / 2}, math.ceil(math.max(x1 - x0, y1 - y0) / 64) + 1)
  s.force_generate_chunk_requests()
  local tiles = {}
  for x = x0, x1 - 1 do for y = y0, y1 - 1 do tiles[#tiles + 1] = {name = "grass-1", position = {x, y}} end end
  s.set_tiles(tiles)
  for _, e in pairs(s.find_entities_filtered{area = {{x0, y0}, {x1, y1}}}) do if e.type ~= "character" then e.destroy() end end
  -- biters: none anywhere near the test area (they ate the character once)
  s.request_to_generate_chunks({(x0 + x1) / 2, (y0 + y1) / 2}, 8)
  s.force_generate_chunk_requests()
  for _, e in pairs(s.find_entities_filtered{force = "enemy", area = {{x0 - 250, y0 - 250}, {x1 + 250, y1 + 250}}}) do e.destroy() end
  f.chart(s, {{x0, y0}, {x1, y1}})
end
s.peaceful_mode = true
game.map_settings.enemy_expansion.enabled = false
game.forces.enemy.kill_all_units()
-- the player's existing grid (test only): an energy source and a big pole
local function grid(x, y)
  local eei = make("electric-energy-interface", x, y)
  eei.electric_buffer_size = 1e10 eei.power_production = 1e8 / 60 eei.energy = 1e10
  -- right beside it, so the pole's supply area (2 tiles) overlaps the 2x2 interface; at x + 3 it
  -- only touched it and the "grid" had no energy at all
  return make("big-electric-pole", x + 2, y)
end
for _, r in pairs({"small-electric-pole", "medium-electric-pole", "big-electric-pole", "substation", "transport-belt",
                   "underground-belt", "inserter", "small-lamp", "assembling-machine-2"}) do
  if f.recipes[r] then f.recipes[r].enabled = true end
end
"""

REVIVE = """
local s = game.surfaces[1]
local failed = {}
local ghosts = s.find_entities_filtered{name = "entity-ghost", area = {{X0, Y0}, {X1, Y1}}}
table.sort(ghosts, function(a, b) return (a.ghost_type == "electric-pole" and 1 or 0) < (b.ghost_type == "electric-pole" and 1 or 0) end)
local n = 0
for _, g in pairs(ghosts) do
  if g.valid then
    local name, pos = g.ghost_name, g.position
    local _, e = g.revive()
    if e then n = n + 1 else failed[#failed + 1] = {name, pos.x, pos.y} end
  end
end
return {revived = n, failed = failed}
"""


def lua_points(ps):
    return "{" + ", ".join("{%s, %s}" % (p[0], p[1]) for p in ps) + "}"


def box_lua(body, **kw):
    for k, v in kw.items():
        body = body.replace(k, str(v))
    return body


def revive(x0, y0, x1, y1):
    r = lua(box_lua(REVIVE, X0=x0, Y0=y0, X1=x1, Y1=y1))
    return r["revived"], ([] if r["failed"] == {} else r["failed"])


def transcript(tab):
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slots = [v for k, v in st["slots"].items() if k.endswith(f"\t{tab}")]
    assert slots, f"no chat for tab {tab}"
    return (KC_HOME / "sessions" / f"dashboard_{slots[-1]}.jsonl").read_text(encoding="utf-8")


def teleport(x, y):
    """Move the player there (after the area was cleared of biters); bring the character back if it died."""
    ok = lua(f"""local p = game.get_player(1)
if not p.character then
  if p.ticks_to_respawn then p.ticks_to_respawn = nil end
  if not p.character then p.create_character() end
end
p.teleport({{{x}, {y}}}, game.surfaces[1])
return p.character ~= nil and p.character.valid""")
    assert ok, "no character to stand next to the task"


def assert_grid(pos):
    """The test's own grid really has a power source (it once did not, and Kira wired to a dead pole)."""
    g = lua("return kc.grid_power(game.surfaces[1].find_entity('big-electric-pole', {%s, %s}))" % tuple(pos))
    assert g["sources"] >= 1, f"test setup: the grid pole has no power source: {g}"


@pytest.fixture(scope="module")
def kira():
    assert http("GET", "/health")["game_online"], "game not online on the app's bridge"
    return Player()


# ── 1. circuit diagnosis: a wire colour mismatch ─────────────────────────

LAMP_BUG = PRE + """
local X, Y = 1100, 400
clear(X - 6, Y - 6, X + 20, Y + 12)
local pole = grid(X - 3, Y + 8)
make("substation", X + 3, Y + 4)
local chest = make("wooden-chest", X + 0.5, Y + 0.5)
chest.insert{name = "iron-plate", count = 500}
local d = make("decider-combinator", X + 3, Y + 0.5, defines.direction.east)
d.get_or_create_control_behavior().parameters = {
  conditions = {{first_signal = sig("item", "iron-plate"), comparator = ">", constant = 100,
                 first_signal_networks = {red = false, green = true}}},
  outputs = {{signal = sig("virtual", "signal-L"), copy_count_from_input = false, constant = 1}},
}
local lamp = make("small-lamp", X + 6.5, Y + 0.5)
local lcb = lamp.get_or_create_control_behavior()
lcb.circuit_enable_disable = true
lcb.circuit_condition = {first_signal = sig("virtual", "signal-L"), comparator = ">", constant = 0}
wire(chest, W.circuit_red, d, W.combinator_input_red)
wire(d, W.combinator_output_red, lamp, W.circuit_red)
return {decider = {d.position.x, d.position.y}}
"""

DECIDER_NETS = """
local d = game.surfaces[1].find_entity("decider-combinator", ID)
local c = d.get_control_behavior().parameters.conditions[1]
return {red = c.first_signal_networks.red, green = c.first_signal_networks.green, constant = c.constant}
"""


def test_explains_why_a_lamp_never_lights(kira):
    ids = lua(LAMP_BUG)
    teleport(1095, 400)
    tab = f"Lamp {STAMP}"
    text, _, t = kira.ask_and_wait(tab, (
        "The lamp at (1106.5, 400.5) should turn on when the chest beside it has more than 100 iron plates. "
        "The chest has 500 and the lamp stays off. Why? Don't change anything, just tell me."), timeout=LONG)
    print(f"\nlamp diagnosis ({t:.0f}s): {text}")
    low = text.lower()
    assert "green" in low and "red" in low, "the cause is the decider reading only the green wire"
    after = lua(DECIDER_NETS.replace("ID", "{%s, %s}" % tuple(ids["decider"])))
    assert after == {"red": False, "green": True, "constant": 100}, f"changed without consent: {after}"
    assert "kc.circuits" in transcript(tab), "expected the diagnosis to read the network with kc.circuits"


# ── 2. the player's real bug: trains leave Unload with cargo ─────────────

TRAIN_BUG = PRE + """
local X, Y = 1000, 400
clear(X - 10, Y - 12, X + 80, Y + 14)
for x = X, X + 70, 2 do make("straight-rail", x, Y, defines.direction.east) end
local load = make("train-stop", X + 6, Y - 2, defines.direction.west)   -- other side of the track
if load then load.backer_name = "Load" end
local unload = make("train-stop", X + 60, Y + 2, defines.direction.east)
unload.backer_name = "Unload"
local pole = grid(X + 40, Y + 10)
make("substation", X + 52, Y + 6)
-- the train parked at Unload (front at the stop, wagon beside the unload inserters), still loaded;
-- manual mode keeps it there while Kira looks
local loco = make("locomotive", X + 56, Y, defines.direction.east)
local wagon = make("cargo-wagon", X + 49, Y, defines.direction.east)
loco.insert{name = "coal", count = 50}
wagon.get_inventory(defines.inventory.cargo_wagon).insert{name = "iron-plate", count = 2000}
-- unloading: two inserters from the wagon into chests, in the row right below it; each inserter
-- only runs while its chest holds less than 400 plates (the chests are full now)
-- bounding_box of rolling stock is the north-facing box even when it runs east: turn it
local b0, c = wagon.bounding_box, wagon.position
local hl, hw = (b0.right_bottom.y - b0.left_top.y) / 2, (b0.right_bottom.x - b0.left_top.x) / 2
local bb = {left_top = {x = c.x - hl, y = c.y - hw}, right_bottom = {x = c.x + hl, y = c.y + hw}}
local row = c.y + 1.5  -- the tile row right beside the 2-tile rail
local out = {inserters = {}, chests = {}}
for i, x in ipairs({math.floor(wagon.position.x) - 1.5, math.floor(wagon.position.x) + 1.5}) do
  local chest = make("wooden-chest", x, row + 1)
  chest.insert{name = "iron-plate", count = 400}
  local ins = make("inserter", x, row, defines.direction.north)  -- faces the wagon it picks from
  local p = ins.pickup_position
  if not (p.x > bb.left_top.x and p.x < bb.right_bottom.x and p.y > bb.left_top.y and p.y < bb.right_bottom.y) then
    return {setup_error = "unload inserter does not reach the wagon", pickup = {p.x, p.y}, wagon = bb}
  end
  wire(chest, W.circuit_red, ins, W.circuit_red)
  local cb = ins.get_or_create_control_behavior()
  cb.circuit_enable_disable = true
  cb.circuit_condition = {first_signal = sig("item", "iron-plate"), comparator = "<", constant = 400}
  out.inserters[i] = {ins.position.x, ins.position.y}
end
local t = loco.train
local sch = t.get_schedule()
sch.add_record{station = "Load", wait_conditions = {{type = "full", compare_type = "or"}}}
sch.add_record{station = "Unload", wait_conditions = {{type = "inactivity", compare_type = "or", ticks = 300}}}
sch.go_to_station(1)
t.manual_mode = true
out.train = t.id
return out
"""

TRAIN_STATE = """
local t = game.train_manager.get_train_by_id(TID)
local w = t.get_schedule().get_wait_conditions({schedule_index = 2})
local conds = {}
for i, id in ipairs(INS) do
  local cb = game.surfaces[1].find_entity("inserter", id).get_control_behavior()
  conds[i] = {cb.circuit_condition.comparator, cb.circuit_condition.constant}
end
return {wait = w[1].type, ticks = w[1].ticks, conds = conds}
"""


def test_finds_why_trains_leave_with_cargo(kira):
    ids = lua(TRAIN_BUG)
    assert "setup_error" not in ids, ids
    teleport(1050, 404)
    before = lua(TRAIN_STATE.replace("TID", str(ids["train"])).replace("INS", lua_points(ids["inserters"])))
    tab = f"Trains {STAMP}"
    text, _, t = kira.ask_and_wait(tab, (
        "Trains leave my Unload station with iron plates still in the wagon. Find out why. "
        "Don't change anything, just tell me the cause and the fix."), timeout=LONG)
    print(f"\ntrain diagnosis ({t:.0f}s): {text}")
    low = text.lower()
    assert "inactiv" in low, "cause 1: the Unload wait condition is inactivity, not empty cargo"
    assert "400" in low, "cause 2: the unload inserters stop at 400 plates in the chests"
    after = lua(TRAIN_STATE.replace("TID", str(ids["train"])).replace("INS", lua_points(ids["inserters"])))
    assert after == before, f"changed without consent: {before} -> {after}"
    log = transcript(tab)
    assert "kc.circuits" in log and ("get_schedule" in log or "schedule" in log), "expected network and schedule reads"


# ── 3. power scattered lamps from the existing grid ──────────────────────

LAMPS = PRE + """
local X, Y = 1200, 400
clear(X - 50, Y - 20, X + 40, Y + 40)
local pole = grid(X - 45, Y)
local out = {lamps = {}, grid = {pole.position.x, pole.position.y}}
for i, p in ipairs({{3, 2}, {15, 4}, {8, 12}, {22, 18}, {4, 24}, {18, 27}}) do
  local l = make("small-lamp", X + p[1] + 0.5, Y + p[2] + 0.5)
  out.lamps[i] = {l.position.x, l.position.y}
end
return out
"""

LAMP_NETS = """
local out = {}
local s = game.surfaces[1]
for i, p in ipairs(LAMPS) do out[i] = s.find_entity("small-lamp", p).electric_network_id end
return {lamps = out, grid = s.find_entity("big-electric-pole", GRID).electric_network_id}
"""


def test_powers_scattered_lamps_from_the_grid(kira):
    ids = lua(LAMPS)
    assert_grid(ids["grid"])
    teleport(1212, 414)
    tab = f"Power {STAMP}"
    text, _, t = kira.ask_and_wait(tab, "Power the 6 lamps around me from my power grid.", timeout=LONG)
    print(f"\npower reply ({t:.0f}s): {text}")
    n, failed = revive(1150, 380, 1240, 440)
    assert failed == [], f"ghosts overlapping something: {failed}"
    nets = lua(LAMP_NETS.replace("LAMPS", lua_points(ids["lamps"])).replace("GRID", "{%s, %s}" % tuple(ids["grid"])))
    print(f"revived {n}; networks {nets}")
    assert nets["grid"] and all(x == nets["grid"] for x in nets["lamps"]), nets
    assert "kc.trial_start" in transcript(tab), "expected Kira to try the build before replying"


# ── 4. a rate question answered from the save's own data ─────────────────

def test_rate_question_uses_the_real_numbers(kira):
    tab = f"Rates {STAMP}"
    text, _, t = kira.ask_and_wait(tab, (
        "With 3 assembling machine 2 making electronic circuits, fed with enough cable and plates, "
        "how many circuits per second do I get? One number."), timeout=LONG)
    print(f"\nrate reply ({t:.0f}s): {text}")
    assert "4.5" in text, "3 machines x 0.75 speed / 0.5 s per circuit = 4.5/s"


# ── 5. move items between two chests by belt, around a wall ──────────────

BELT_TASK = PRE + """
local X, Y = 1300, 400
clear(X - 12, Y - 18, X + 36, Y + 22)
local pole = grid(X + 4, Y - 14)
local a = make("wooden-chest", X + 0.5, Y + 0.5)
a.insert{name = "iron-plate", count = 200}
local b = make("wooden-chest", X + 20.5, Y + 6.5)
for y = Y - 16, Y + 20 do make("stone-wall", X + 10.5, y + 0.5) end
return {a = {a.position.x, a.position.y}, b = {b.position.x, b.position.y}, grid = {pole.position.x, pole.position.y}}
"""


def test_moves_items_by_belt_around_a_wall(kira):
    ids = lua(BELT_TASK)
    assert_grid(ids["grid"])
    teleport(1306, 396)
    tab = f"Belt {STAMP}"
    text, _, t = kira.ask_and_wait(tab, (
        "Move the iron plates from the chest at (1300.5, 400.5) to the chest at (1320.5, 406.5) by belt, "
        "with inserters at both ends."), timeout=LONG)
    print(f"\nbelt reply ({t:.0f}s): {text}")
    n, failed = revive(1288, 382, 1336, 422)
    assert failed == [], f"ghosts overlapping something: {failed}"
    print(f"revived {n}")
    got = lambda: lua(f"return game.surfaces[1].find_entity('wooden-chest', {{{ids['b'][0]}, {ids['b'][1]}}}).get_item_count('iron-plate')")  # noqa: E731
    wait_until(lambda: got() > 0, timeout=120, step=2, what="iron plates arriving in the target chest")
    assert "kc.trial_start" in transcript(tab), "expected Kira to try the build before replying"
