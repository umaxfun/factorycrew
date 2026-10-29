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


def test_atlas_reads_again_after_something_is_built(stack, mcp):
    from conftest import wait_until
    z = mcp.tool("factorio_atlas", at={"x": X0 + 1.5, "y": Y0 + 1.5})
    assert z["ok"], z
    before = {b["recipe"]: b["machines"] for b in z["block_list"]}
    import json, urllib.request
    from conftest import HTTP

    def version():
        with urllib.request.urlopen(HTTP + "/health", timeout=5) as r:
            return json.loads(r.read()).get("atlas_version")
    start = version()
    assert mcp.tool("factorio_query", lua=f"""
local e = game.surfaces[1].create_entity{{name = "assembling-machine-1", position = {{{X0} + 1.5, {Y0} + 7.5}},
  force = "player", raise_built = true}}
e.set_recipe("iron-gear-wheel")
return true""")["ok"]
    wait_until(lambda: version() != start, timeout=20, step=0.5, what="atlas_version bumped by the build")
    z2 = mcp.tool("factorio_atlas", at={"x": X0 + 1.5, "y": Y0 + 1.5})
    assert z2["ok"], z2
    after = {b["recipe"]: b["machines"] for b in z2["block_list"]}
    assert after["iron-gear-wheel"] == before["iron-gear-wheel"] + 1, (before, after)


def test_only_the_changed_chunks_are_read_again(stack, mcp):
    """docs/atlas.md task 6: after a build, the bridge asks the mod for the dirty chunks only."""
    import json
    import urllib.request
    from conftest import HTTP, wait_until

    def post(body):
        req = urllib.request.Request(HTTP + "/atlas", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())

    def health():
        with urllib.request.urlopen(HTTP + "/health", timeout=5) as r:
            return json.loads(r.read())
    first = post({})
    assert first["ok"], first
    again = post({"have_version": first["version"]})
    assert again["ok"] and again.get("unchanged") and again["how"] == "cached", again
    v = health()["atlas_version"]
    # one machine in one chunk; a second one far away in another chunk
    assert mcp.tool("factorio_query", lua=f"""
local s = game.surfaces[1]
local e = s.create_entity{{name = "assembling-machine-1", position = {{{X0} + 20.5, {Y0} + 1.5}}, force = "player", raise_built = true}}
e.set_recipe("iron-gear-wheel")  -- a machine without a recipe is no block
return true""")["ok"]
    wait_until(lambda: health()["atlas_version"] != v and health()["atlas_dirty"], timeout=20, step=0.5,
               what="the build reaching the heartbeat")
    r = post({"have_version": first["version"]})
    assert r["ok"] and r["how"] == "partial" and r["chunks_read"] == 1, {k: x for k, x in r.items() if k != "text"}
    assert "assembling-machine-1" in r["text"]
    z = mcp.tool("factorio_atlas", at={"x": X0 + 20.5, "y": Y0 + 1.5})
    assert z["ok"] and z["machines"] == 1, z


def test_second_call_is_fast_and_a_removal_is_seen(stack, mcp):
    """docs/atlas.md task 6: nothing changed -> the answer comes from the cached structure (<200 ms with
    the live statuses); a removal (script_raised_destroy) marks its chunk and the block disappears."""
    import json
    import time
    import urllib.request
    from conftest import HTTP, wait_until

    def health():
        with urllib.request.urlopen(HTTP + "/health", timeout=5) as r:
            return json.loads(r.read())
    at = {"x": X0 + 50.5, "y": Y0 + 1.5}
    first = mcp.tool("factorio_atlas", at=at)
    assert first["ok"], first
    t = time.monotonic()
    again = mcp.tool("factorio_atlas", at=at)
    ms = (time.monotonic() - t) * 1000
    print(f"\nsecond call {ms:.0f} ms")
    assert again["ok"] and ms < 200, (ms, again)
    v = health()["atlas_version"]
    assert mcp.tool("factorio_query", lua=f"""
local e = game.surfaces[1].find_entity("assembling-machine-1", {{{X0} + 50.5, {Y0} + 1.5}})
e.destroy{{raise_destroy = true}}
return true""")["ok"]
    wait_until(lambda: health()["atlas_version"] != v, timeout=20, step=0.5, what="the removal reaching the heartbeat")
    gone = mcp.tool("factorio_atlas", at=at)
    assert not gone["ok"] and gone["error"] == "not_found", gone
