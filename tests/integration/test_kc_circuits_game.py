"""Level 2: the circuit simulator against the real game.

A circuit is built in the game, read with the mod's kc.circuits, simulated in Python for exactly
the number of ticks the game ran between two reads, and the simulated network values must equal
what the game shows. Covers a clock (state that changes every tick, with the one-tick delay) and a
chest-driven lamp condition with a "what if the chest were empty" scenario.
"""
from __future__ import annotations

import sys

from conftest import ROOT, wait_until

sys.path.insert(0, str(ROOT / "mcp"))
import kc.circuits as circuits  # noqa: E402

X0, Y0 = 800, 600

HELP = """
local s = game.surfaces[1]
local W = defines.wire_connector_id
local function sig(t, n) return {type = t, name = n} end
local function wire(a, ca, b, cb) return a.get_wire_connector(ca, true).connect_to(b.get_wire_connector(cb, true), false) end
local function make(name, x, y, dir) return s.create_entity{name = name, position = {x, y}, force = "player", direction = dir} end
-- combinators need power: an electric-energy-interface (test only) and a substation over the area
local function power(x, y)
  local eei = s.create_entity{name = "electric-energy-interface", position = {x, y}, force = "player"}
  eei.electric_buffer_size = 1e9 eei.power_production = 1e7 / 60 eei.energy = 1e9
  s.create_entity{name = "substation", position = {x + 3, y}, force = "player"}
end
local function clear(x0, y0, x1, y1)
  for _, e in pairs(s.find_entities_filtered{area = {{x0, y0}, {x1, y1}}}) do if e.type ~= "character" then e.destroy() end end
end
"""

CLOCK = HELP + """
clear(X0 - 2, Y0 - 2, X0 + 10, Y0 + 4)
local c = make("constant-combinator", X0 + 0.5, Y0 + 0.5)
local sec = c.get_or_create_control_behavior().get_section(1) or c.get_or_create_control_behavior().add_section()
sec.set_slot(1, {value = {type = "virtual", name = "signal-T", quality = "normal"}, min = 1})
local d = make("decider-combinator", X0 + 3, Y0 + 0.5, defines.direction.east)
d.get_or_create_control_behavior().parameters = {
  conditions = {{first_signal = sig("virtual", "signal-T"), comparator = "<", constant = 60}},
  outputs = {{signal = sig("virtual", "signal-T"), copy_count_from_input = true}},
}
wire(c, W.circuit_red, d, W.combinator_input_red)
wire(d, W.combinator_output_red, d, W.combinator_input_red)   -- feedback: the counter
power(X0 + 1, Y0 - 3)
return true
"""

LAMP = HELP + """
clear(X0 - 2, Y0 + 8, X0 + 12, Y0 + 14)
local chest = make("wooden-chest", X0 + 0.5, Y0 + 10.5)
chest.insert{name = "iron-plate", count = 150}
chest.get_or_create_control_behavior()
local d = make("decider-combinator", X0 + 3, Y0 + 10.5, defines.direction.east)
d.get_or_create_control_behavior().parameters = {
  conditions = {{first_signal = sig("item", "iron-plate"), comparator = ">", constant = 100}},
  outputs = {{signal = sig("virtual", "signal-L"), copy_count_from_input = false, constant = 1}},
}
local lamp = make("small-lamp", X0 + 6.5, Y0 + 10.5)
lamp.get_or_create_control_behavior().circuit_condition = {first_signal = sig("virtual", "signal-L"), comparator = ">", constant = 0}
wire(chest, W.circuit_red, d, W.combinator_input_red)
wire(d, W.combinator_output_green, lamp, W.circuit_green)
power(X0 + 1, Y0 + 13)
return {chest = chest.unit_number, lamp = lamp.unit_number}
"""

READ = "return kc.circuits(surface, {area = {{AX0, AY0}, {AX1, AY1}}})"


def read(mcp, box):
    lua = READ
    for k, v in zip(("AX0", "AY0", "AX1", "AY1"), box):
        lua = lua.replace(k, str(v))
    r = mcp.tool("factorio_query", lua=lua)
    assert r["ok"], r
    return r["data"]


def lua(mcp, body):
    r = mcp.tool("factorio_query", lua=body.replace("X0", str(X0)).replace("Y0", str(Y0)))
    assert r["ok"], r
    return r["data"]


def signal_on(snap, sig):
    """{network id: count of sig} over the snapshot's networks."""
    return {n: v["signals"].get(sig, 0) for n, v in snap["networks"].items()}


def test_clock_simulation_matches_the_game_tick_for_tick(stack, mcp):
    lua(mcp, CLOCK)
    box = (X0 - 2, Y0 - 2, X0 + 10, Y0 + 4)
    wait_until(lambda: read(mcp, box)["tick"] > 0, what="game ticking")
    checked = 0
    for _ in range(5):
        a = read(mcp, box)
        b = read(mcp, box)
        k = b["tick"] - a["tick"]
        assert k > 0, (a["tick"], b["tick"])
        c = circuits.load(a)
        c.run(k, until_stable=False)
        want = {n: v["signals"] for n, v in b["networks"].items()}
        got = {n: {s: x for s, x in c.values.get(n, {}).items() if x} for n in want}
        assert got == want, (k, a["tick"], got, want)
        checked += 1
    assert checked == 5
    # and the clock really cycles: period 60 (T = 1..60; at 60 the decider passes nothing, T falls to 1)
    res = circuits.load(read(mcp, box)).run(200)
    assert res["cycle"] and res["cycle"]["period"] == 60, res["cycle"]


def test_what_if_scenario_matches_the_game(stack, mcp):
    ids = lua(mcp, LAMP)
    box = (X0 - 2, Y0 + 8, X0 + 12, Y0 + 14)
    wait_until(lambda: any(v["signals"].get("virtual/signal-L") for v in read(mcp, box)["networks"].values()),
               what="lamp signal on")
    snap = read(mcp, box)
    c = circuits.load(snap)
    # prediction: with the chest empty, L disappears after the decider's tick of delay
    future = c.with_inputs({ids["chest"]: {}}).run(5, until_stable=False)["final"]
    assert not any(v.get("virtual/signal-L") for v in future.values()), future
    # what explain() says names the condition
    text = circuits.explain(c)
    assert "iron-plate" in text and ">" in text and "100" in text, text
    # the game agrees once the chest is actually emptied
    lua(mcp, f'local e = game.surfaces[1].find_entities_filtered{{name = "wooden-chest", position = {{X0 + 0.5, Y0 + 10.5}}, radius = 0.5}}[1] e.clear_items_inside() return true')
    wait_until(lambda: not any(v["signals"].get("virtual/signal-L") for v in read(mcp, box)["networks"].values()),
               timeout=10, what="lamp signal off in the game")
