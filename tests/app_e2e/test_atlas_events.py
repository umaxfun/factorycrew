"""The atlas sees changes a player makes (docs/atlas.md, task 6), on the real 2.0.77 client with a real
player: rotating an inserter, pasting settings onto a machine, and changing a recipe in the machine's
window. Each must mark exactly its chunk, the next /atlas must read only that chunk, and the stored
structure must show the change.

The actions are made through the player's own API calls (rotate{by_player}, copy_settings(by_player),
player.opened), which raise the same events as the mouse; the clicks themselves are not driven.

  scripts/test-app-e2e.sh -k events
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from test_app_e2e import http, lua  # noqa: F401  (shared harness)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp"))
from kc import atlas as A  # noqa: E402

X, Y = 1400, 400  # one chunk: x 1376..1408, y 384..416

SETUP = f"""
local s, p = game.surfaces[1], game.get_player(1)
for _, r in pairs({{"iron-gear-wheel", "copper-cable", "assembling-machine-2", "inserter", "transport-belt"}}) do
  if p.force.recipes[r] then p.force.recipes[r].enabled = true end
end
s.request_to_generate_chunks({{{X}, {Y}}}, 1)
s.force_generate_chunk_requests()
for _, e in pairs(s.find_entities_filtered{{area = {{{{{X} - 20, {Y} - 14}}, {{{X} + 6, {Y} + 14}}}}}}) do
  if e.type ~= "character" then e.destroy() end
end
local function mk(name, x, y, dir)
  return s.create_entity{{name = name, position = {{x, y}}, force = "player", direction = dir, raise_built = true}}
end
local a = mk("assembling-machine-2", {X} - 10.5, {Y} + 0.5); a.set_recipe("iron-gear-wheel")
local b = mk("assembling-machine-2", {X} - 4.5, {Y} + 0.5); b.set_recipe("copper-cable")
-- iron from an infinite chest above, through the inserter, into the gear machine
local c = mk("infinity-chest", {X} - 10.5, {Y} - 2.5)
c.set_infinity_container_filter(1, {{name = "iron-plate", count = 100, mode = "at-least", index = 1}})
mk("inserter", {X} - 10.5, {Y} - 1.5, defines.direction.north)
-- a chain for the recipe test: iron chest -> C (gears) -> inserter -> D (belts, which take gears)
local c2 = mk("infinity-chest", {X} - 16.5, {Y} + 5.5)
c2.set_infinity_container_filter(1, {{name = "iron-plate", count = 100, mode = "at-least", index = 1}})
mk("inserter", {X} - 16.5, {Y} + 6.5, defines.direction.north)
local mc = mk("assembling-machine-2", {X} - 16.5, {Y} + 8.5); mc.set_recipe("iron-gear-wheel")
mk("inserter", {X} - 14.5, {Y} + 8.5, defines.direction.west)   -- picks from C, drops into D
local md = mk("assembling-machine-2", {X} - 12.5, {Y} + 8.5); md.set_recipe("transport-belt")
return p ~= nil
"""


def health():
    return http("GET", "/health")


def atlas(have=None):
    body = {} if have is None else {"have_version": have}
    r = http("POST", "/atlas", body, timeout=120)
    assert r.get("ok"), {k: v for k, v in r.items() if k != "text"}
    return r


def wait_change(v0, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = health()
        if h.get("atlas_version") != v0 and h.get("atlas_dirty"):
            return h
        time.sleep(0.5)
    raise AssertionError(f"no change reached the heartbeat: {health()}")


def entity_at(text, name, x, y):
    for e in A.parse(text)["entities"]:
        if e["name"] == name and e["x"] == x and e["y"] == y:
            return e
    raise AssertionError(f"no {name} at ({x}, {y}) in the stored structure")


@pytest.fixture(scope="module")
def base():
    assert health()["game_online"], "game not online on the app's bridge"
    assert lua(SETUP) is True, "no player in the game"
    time.sleep(6)  # the build reaches the heartbeat
    first = atlas()
    return {"version": first["version"], "text": first["text"]}


def check(base, action, expect, label):
    v0 = health()["atlas_version"]
    assert lua(action + "\nreturn true") is True
    h = wait_change(v0)
    r = atlas(have=base["version"])
    print(f"\n{label}: version {v0} -> {h['atlas_version']}, dirty {h['atlas_dirty']}, "
          f"read {r['how']} {r['chunks_read']} chunk(s) in {r['ms']} ms")
    assert r["how"] == "partial" and r["chunks_read"] == 1, {k: v for k, v in r.items() if k != "text"}
    expect(r["text"], base["text"])
    base["version"], base["text"] = r["version"], r["text"]


def iron_sources(text):
    """where the gear machine's iron comes from, by the atlas built from that structure"""
    a = A.Atlas(text)
    v = a.block_view(a.block_at(X - 10.5, Y + 0.5))
    return [s["from"] for s in v["inputs"].get("iron-plate", [])], v["no_source"]


def test_rotating_an_inserter_is_seen(base):
    def expect(after, before):
        assert entity_at(after, "inserter", X - 10.5, Y - 1.5)["dir"] != 0, "still facing north"
        src0, missing0 = iron_sources(before)
        src1, missing1 = iron_sources(after)
        print(f"  iron before: from {src0}, no source {missing0}; after: from {src1}, no source {missing1}")
        assert src0 and "iron-plate" not in missing0, "setup: the chest did not feed the machine"
        assert not src1 and "iron-plate" in missing1, "the graph still has the iron edge"
    # a quarter turn: the inserter now picks from the side and drops beside the machine
    check(base, f"""
local i = game.surfaces[1].find_entity("inserter", {{{X} - 10.5, {Y} - 1.5}})
i.rotate{{by_player = game.get_player(1)}}""", expect, "rotate")


def test_pasting_settings_is_seen(base):
    def expect(t, _before):
        assert entity_at(t, "assembling-machine-2", X - 10.5, Y + 0.5)["r"] == "copper-cable"
    check(base, f"""
local s = game.surfaces[1]
local a = s.find_entity("assembling-machine-2", {{{X} - 10.5, {Y} + 0.5}})
local b = s.find_entity("assembling-machine-2", {{{X} - 4.5, {Y} + 0.5}})
a.copy_settings(b, game.get_player(1))""", expect, "paste")


def gear_sources_of_d(text):
    a = A.Atlas(text)
    v = a.block_view(a.block_at(X - 12.5, Y + 8.5))
    return [(s["from"], s.get("recipe")) for s in v["inputs"].get("iron-gear-wheel", [])], v["no_source"]


def test_changing_the_recipe_in_the_machine_window_is_seen(base):
    def expect(after, before):
        assert entity_at(after, "assembling-machine-2", X - 16.5, Y + 8.5)["r"] == "copper-cable"
        g0, missing0 = gear_sources_of_d(before)
        g1, missing1 = gear_sources_of_d(after)
        print(f"  D's gears before: from {g0}, no source {missing0}; after: from {g1}, no source {missing1}")
        assert g0 and g0[0][1] == "iron-gear-wheel", "setup: C did not feed D with gears"
        assert not g1 and "iron-gear-wheel" in missing1, "the graph still has the gear edge"
        a = A.Atlas(after)
        c = a.block_view(a.block_at(X - 16.5, Y + 8.5))
        assert c["recipe"] == "copper-cable" and "copper-plate" in c["no_source"], c
    # the way a player does it, one step per tick: walk up, open the machine, pick the recipe, close the
    # window (a machine out of reach does not open, and then closing raises nothing)
    lua(f'game.get_player(1).teleport({{{X} - 16.5, {Y} + 12.5}}) return true')
    opened = lua(f'local p = game.get_player(1) '
                 f'p.opened = game.surfaces[1].find_entity("assembling-machine-2", {{{X} - 16.5, {Y} + 8.5}}) '
                 'return p.opened ~= nil')
    assert opened, "the machine window did not open"
    lua(f'game.surfaces[1].find_entity("assembling-machine-2", {{{X} - 16.5, {Y} + 8.5}}).set_recipe("copper-cable") '
        'return true')
    check(base, "game.get_player(1).opened = nil", expect, "recipe in the GUI")


def test_opening_and_closing_the_inventory_changes_nothing(base):
    v0 = health()["atlas_version"]
    lua('local p = game.get_player(1) p.opened = defines.gui_type.controller p.opened = nil return true')
    time.sleep(6)
    assert health()["atlas_version"] == v0, "closing the player's own inventory marked the base changed"
