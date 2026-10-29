-- Level 1 tests of the atlas dump (scripts/atlas.lua): what changed is kept in storage (so it
-- survives save and load), a partial dump reads only those chunks, and a dump never raises.
local atlas = require("scripts.atlas")

local function make(s, name, x, y)
  local e = s.create_entity{ name = name, position = { x, y }, force = "player" }
  assert(e, "could not create " .. name)
  return e
end

test("a marked entity's chunk is kept in storage until a dump takes it", function()
  local s = game.surfaces[1]
  atlas.take_dirty(s.name)
  local e = make(s, "assembling-machine-1", 1000.5, 1000.5)
  atlas.mark(e)
  local d = storage.kc.atlas_dirty[s.name]
  assert(d.chunks["31,31"], "chunk 31,31 not marked")
  assert(atlas.dirty_count(s.name) == 1)
  local taken = atlas.take_dirty(s.name)
  assert(taken.chunks["31,31"] and atlas.dirty_count(s.name) == 0)
  e.destroy()
end)

test("an entity across a chunk border marks both chunks", function()
  local s = game.surfaces[1]
  atlas.take_dirty(s.name)
  local e = make(s, "assembling-machine-1", 1024, 1000.5) -- 3x3 centered on the border x = 1024
  atlas.mark(e)
  local d = storage.kc.atlas_dirty[s.name]
  assert(d.chunks["31,31"] and d.chunks["32,31"], serpent.line(d.chunks))
  atlas.take_dirty(s.name)
  e.destroy()
end)

test("a partial dump reads only the dirty chunks", function()
  local s = game.surfaces[1]
  atlas.take_dirty(s.name)
  local a = make(s, "assembling-machine-1", 1100.5, 1100.5)
  make(s, "assembling-machine-1", 1300.5, 1300.5) -- built, never marked
  atlas.mark(a)
  atlas.start({ id = "t", surface = s.name, chunks = "dirty", send_bytes = 1e9 })
  local text, data = {}, nil
  for _ = 1, 100 do
    for _, m in ipairs(atlas.step()) do
      if m.type == "atlas_part" then text[#text + 1] = m.text else data = m end
    end
    if data then break end
  end
  assert(data and data.ok and data.data.partial, serpent.line(data))
  local t = table.concat(text, "\n")
  assert(t:find("C|34|34", 1, true), "the dirty chunk is not listed")
  assert(data.data.entities == 1, "read " .. tostring(data.data.entities) .. " entities, want 1")
end)

test("mod changes make the next dump a full one", function()
  local s = game.surfaces[1]
  atlas.mark_all()
  assert(atlas.dirty_count(s.name) == -1)
  atlas.take_dirty(s.name)
end)
