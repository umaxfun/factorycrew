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

-- Fluid links are read on 2.0 (LuaEntity::fluidbox) and on 2.1, which removed it for LuaEntity
-- methods (get_fluid_segment_id, get_fluid_box_pipe_connections, ...).
local function dump_marked(s, list)
  atlas.take_dirty(s.name)
  for _, e in ipairs(list) do atlas.mark(e) end
  atlas.start({ id = "t", surface = s.name, chunks = "dirty", send_bytes = 1e9 })
  local text, data = {}, nil
  for _ = 1, 100 do
    for _, m in ipairs(atlas.step()) do
      if m.type == "atlas_part" then text[#text + 1] = m.text else data = m end
    end
    if data then break end
  end
  assert(data and data.ok, serpent.line(data))
  return table.concat(text, "\n")
end

local function line_of(text, name)
  local id = text:match("\nN|(%d+)|" .. name:gsub("%-", "%%-") .. "|") or text:match("^N|(%d+)|" .. name:gsub("%-", "%%-") .. "|")
  assert(id, "no name line for " .. name .. " in\n" .. text)
  for l in text:gmatch("[^\n]+") do
    if l:find("^E|" .. id .. "|") then return l end
  end
  error("no entity line for " .. name .. " in\n" .. text)
end

test("a machine's fluid boxes are dumped with their kind and segments", function()
  local s = game.surfaces[1]
  game.forces.player.recipes["sulfuric-acid"].enabled = true
  local plant = make(s, "chemical-plant", 2100.5, 2100.5)
  plant.set_recipe("sulfuric-acid")
  local l = line_of(dump_marked(s, { plant }), "chemical-plant")
  local f = l:match("|f=([^|]+)")
  assert(f, "no fluid field: " .. l)
  assert(f:find(":input:", 1, true) and f:find(":output:", 1, true), "fluid boxes without their kind: " .. f)
  -- An empty plant still names its recipe's fluids: through the box filters the recipe sets.
  assert(f:find(":input:water", 1, true), "input box without its fluid: " .. f)
  assert(f:find(":output:sulfuric-acid", 1, true), "output box without its fluid: " .. f)
  plant.destroy()
end)

test("a pump's input and output segments are dumped", function()
  local s = game.surfaces[1]
  local pump = s.create_entity{ name = "pump", position = { 2200.5, 2200 }, direction = defines.direction.north,
                                force = "player" }
  assert(pump, "could not create pump")
  local a = make(s, "pipe", 2200.5, 2201.5)
  local b = make(s, "pipe", 2200.5, 2198.5)
  local l = line_of(dump_marked(s, { pump }), "pump")
  local pi, po = l:match("|pi=([^|]*)"), l:match("|po=([^|]*)")
  assert(pi and pi:match("^%d+$") and po and po:match("^%d+$"), "pump segments not read: " .. l)
  assert(pi ~= po, "input and output are one segment: " .. l)
  pump.destroy(); a.destroy(); b.destroy()
end)
