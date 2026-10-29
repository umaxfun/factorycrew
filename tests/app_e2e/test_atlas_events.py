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
for _, r in pairs({{"iron-gear-wheel", "copper-cable", "assembling-machine-2", "inserter"}}) do
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
mk("inserter", {X} - 10.5, {Y} - 1.5, defines.direction.north)
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
    return {"version": first["version"]}


def check(base, action, expect, label):
    v0 = health()["atlas_version"]
    assert lua(action + "\nreturn true") is True
    h = wait_change(v0)
    r = atlas(have=base["version"])
    print(f"\n{label}: version {v0} -> {h['atlas_version']}, dirty {h['atlas_dirty']}, "
          f"read {r['how']} {r['chunks_read']} chunk(s) in {r['ms']} ms")
    assert r["how"] == "partial" and r["chunks_read"] == 1, {k: v for k, v in r.items() if k != "text"}
    expect(r["text"])
    base["version"] = r["version"]


def test_rotating_an_inserter_is_seen(base):
    check(base, f"""
local i = game.surfaces[1].find_entity("inserter", {{{X} - 10.5, {Y} - 1.5}})
i.rotate{{by_player = game.get_player(1)}}""",
          lambda t: entity_at(t, "inserter", X - 10.5, Y - 1.5)["dir"] != 0 or pytest.fail("still facing north"),
          "rotate")


def test_pasting_settings_is_seen(base):
    def expect(t):
        assert entity_at(t, "assembling-machine-2", X - 10.5, Y + 0.5)["r"] == "copper-cable"
    check(base, f"""
local s = game.surfaces[1]
local a = s.find_entity("assembling-machine-2", {{{X} - 10.5, {Y} + 0.5}})
local b = s.find_entity("assembling-machine-2", {{{X} - 4.5, {Y} + 0.5}})
a.copy_settings(b, game.get_player(1))""", expect, "paste")


def test_changing_the_recipe_in_the_machine_window_is_seen(base):
    def expect(t):
        assert entity_at(t, "assembling-machine-2", X - 10.5, Y + 0.5)["r"] == "iron-gear-wheel"
    # the way a player does it, one step per tick: walk up, open the machine, pick the recipe, close the
    # window (a machine out of reach does not open, and then closing raises nothing)
    lua(f'game.get_player(1).teleport({{{X} - 10.5, {Y} + 4.5}}) return true')
    opened = lua(f'local p = game.get_player(1) '
                 f'p.opened = game.surfaces[1].find_entity("assembling-machine-2", {{{X} - 10.5, {Y} + 0.5}}) '
                 'return p.opened ~= nil')
    assert opened, "the machine window did not open"
    lua(f'game.surfaces[1].find_entity("assembling-machine-2", {{{X} - 10.5, {Y} + 0.5}}).set_recipe("iron-gear-wheel") '
        'return true')
    check(base, "game.get_player(1).opened = nil", expect, "recipe in the GUI")


def test_opening_and_closing_the_inventory_changes_nothing(base):
    v0 = health()["atlas_version"]
    lua('local p = game.get_player(1) p.opened = defines.gui_type.controller p.opened = nil return true')
    time.sleep(6)
    assert health()["atlas_version"] == v0, "closing the player's own inventory marked the base changed"
