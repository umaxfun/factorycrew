"""Level 2: factorio_atlas through the real mod, bridge and MCP server.

A small chain is built in an empty corner: two gear assemblers next to each other feed, by an
inserter straight into it, a belt assembler; a far-away gear assembler is another block. The dump
travels as atlas_part packets over UDP and the MCP server turns it into blocks and zones.
"""
from __future__ import annotations

X0, Y0 = 700, 700

SETUP = f"""
local s = game.surfaces[1]
s.request_to_generate_chunks({{{X0}, {Y0}}}, 3)
s.force_generate_chunk_requests()
local box = {{{{{X0 - 10}, {Y0 - 10}}}, {{{X0 + 60}, {Y0 + 20}}}}}
local tiles = {{}}
for x = {X0 - 10}, {X0 + 59} do for y = {Y0 - 10}, {Y0 + 19} do tiles[#tiles + 1] = {{name = "grass-1", position = {{x, y}}}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{{area = box}}) do if e.type ~= "character" then e.destroy() end end
local function am(x, y, r)
  local e = s.create_entity{{name = "assembling-machine-1", position = {{x, y}}, force = "player"}}
  e.set_recipe(r)
  return e
end
am({X0} + 1.5, {Y0} + 1.5, "iron-gear-wheel")
am({X0} + 1.5, {Y0} + 4.5, "iron-gear-wheel")
am({X0} + 5.5, {Y0} + 1.5, "transport-belt")
-- gears from the first assembler straight into the belt assembler (picks west, drops east)
s.create_entity{{name = "inserter", position = {{{X0} + 3.5, {Y0} + 1.5}}, direction = defines.direction.west, force = "player"}}
am({X0} + 50.5, {Y0} + 1.5, "iron-gear-wheel")
return true
"""


def test_atlas_finds_the_blocks_and_the_direct_feed(stack, mcp):
    assert mcp.tool("factorio_query", lua=SETUP)["ok"]
    z = mcp.tool("factorio_atlas", at={"x": X0 + 1.5, "y": Y0 + 1.5}, refresh=True)
    assert z["ok"], z
    recipes = {b["recipe"]: b["machines"] for b in z["block_list"]}
    assert recipes == {"iron-gear-wheel": 2, "transport-belt": 1}, z
    # statuses are live: nothing has ingredients
    assert sum(z["status"].values()) == 3 and "working" not in z["status"], z
    b = mcp.tool("factorio_atlas", level="block", at={"x": X0 + 5.5, "y": Y0 + 1.5})
    assert b["ok"], b
    gear = b["inputs"]["iron-gear-wheel"]
    assert [s["recipe"] for s in gear] == ["iron-gear-wheel"] and gear[0]["by"] == ["inserter"], b
    assert b["no_source"] == ["iron-plate"], b
    far = mcp.tool("factorio_atlas", at={"x": X0 + 50.5, "y": Y0 + 1.5})
    assert far["ok"] and far["zone"] != z["zone"] and far["machines"] == 1, far
