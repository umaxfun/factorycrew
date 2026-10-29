-- Level 1 tests of kc.trial: a plan of ghosts is built on a hidden copy of its area and run, and
-- the report says whether it works. Called through the query environment, the way Kira calls it.
local protocol = require("scripts.protocol")

local function q(lua)
  local r = protocol.run_query(lua)
  assert(r.ok, r.message)
  return r.data
end

local function clear(s, x0, y0, x1, y1)
  for _, e in pairs(s.find_entities_filtered{ area = { { x0, y0 }, { x1, y1 } } }) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  local tiles = {}
  for x = x0, x1 - 1 do for y = y0, y1 - 1 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
  s.set_tiles(tiles)
end

local function make(s, name, x, y)
  local e = s.create_entity{ name = name, position = { x, y }, force = "player" }
  assert(e, "could not create " .. name)
  return e
end

--- The player's grid: a real substation inside the design's area, fed from an energy source
--- outside it (so the trial has to stand in for it). powered = false leaves out the source.
local function grid(s, y, powered)
  if powered ~= false then
    local ei = make(s, "electric-energy-interface", 522, y - 3)
    ei.electric_buffer_size = 1e10
    ei.power_production = 1e8 / 60
    ei.energy = 1e10
    make(s, "substation", 525, y - 3)
  end
  return make(s, "substation", 508, y - 3)
end

--- chest A -> inserter -> belt (11 tiles) -> inserter -> chest B, as ghosts. `dir` is the
--- inserters' direction: west is right (an inserter faces the tile it picks from).
local function plan(y, dir)
  return string.format([[
local items = {{name = "inserter", x = 501.5, y = %d.5, direction = %d},
               {name = "inserter", x = 513.5, y = %d.5, direction = %d}}
for x = 502, 512 do items[#items + 1] = {name = "transport-belt", x = x + 0.5, y = %d.5, direction = defines.direction.east} end
return kc.place_ghosts(surface, items)]], y, dir, y, dir, y)
end

local AREA = "{{498, %d}, {518, %d}}"

local function setup(s, y, dir, powered)
  clear(s, 495, y - 8, 530, y + 4)
  local a = make(s, "wooden-chest", 500.5, y + 0.5)
  a.insert{ name = "iron-plate", count = 50 }
  make(s, "wooden-chest", 514.5, y + 0.5)
  local pole = grid(s, y, powered)
  local placed = q(plan(y, dir))
  assert(placed.placed == 13, serpent.line(placed))
  return pole
end

local function change(report, name, x)
  for _, c in ipairs(report.changes) do
    if c.name == name and c.x == x then return c.items["iron-plate"] or 0 end
  end
  return 0
end

describe("kc.grid_power", function()
  it("tells a producing grid from a pole with nothing behind it", function()
    local s = game.surfaces[1]
    clear(s, 495, 470, 530, 482)
    local fed = grid(s, 478)
    make(s, "radar", 513.5, 477.5) -- a steady load (a lamp draws nothing by day)
    local lone = make(s, "medium-electric-pole", 500.5, 480.5)
    after_ticks(330, function() -- a full 5 s window of statistics
      local at = function(e) return string.format("{%s, %s}", e.position.x, e.position.y) end
      local g = q("return kc.grid_power(surface.find_entity('substation', " .. at(fed) .. "))")
      local n = q("return kc.grid_power(surface.find_entity('medium-electric-pole', " .. at(lone) .. "))")
      local near = q("return kc.nearest_pole(surface, {x = 500.5, y = 481}, 3)")
      local st = fed.electric_network_statistics
      local dbg = serpent.line({ g, st.input_counts, st.output_counts })
      clear(s, 495, 470, 530, 482)
      assert(g.sources == 1 and g.producing, dbg)
      -- a vanilla radar draws 300 kW
      assert(g.production_w > 200000 and g.production_w < 400000, dbg)
      assert(g.consumption_w > 200000 and g.consumption_w < 400000, dbg)
      assert(n.sources == 0 and not n.producing, serpent.line(n))
      assert(near.sources == 0, serpent.line(near))
    end)
  end)
end)

describe("kc.trial", function()
  it("runs a working belt plan on a hidden copy and sees the plates arrive", function()
    local s = game.surfaces[1]
    setup(s, 500, defines.direction.west)
    local st = q("return kc.trial_start(surface, " .. AREA:format(494, 504) .. ")")
    assert(st.revived == 13 and #st.failed == 0, serpent.line(st))
    assert(#st.power == 1 and st.power[1].stand_in == true, serpent.line(st))
    local hidden = game.surfaces[st.surface]
    assert(hidden and game.forces.player.get_surface_hidden(hidden), "trial surface must be hidden")
    assert(s.count_entities_filtered{ area = { { 498, 494 }, { 518, 504 } }, name = "entity-ghost" } == 13,
           "the player's ghosts stay ghosts")
    after_ticks(900, function()
      local r = q("return kc.trial_report{finish = true}")
      clear(s, 495, 492, 530, 504)
      assert(r.ticks >= 900 and r.finished, serpent.line(r))
      assert(change(r, "wooden-chest", 514.5) > 0, "plates must reach chest B: " .. serpent.line(r))
      assert(change(r, "wooden-chest", 500.5) < 0, serpent.line(r))
    end)
  end)

  it("shows nothing arriving when the inserters face the wrong way", function()
    local s = game.surfaces[1]
    setup(s, 520, defines.direction.east)
    q("return kc.trial_start(surface, " .. AREA:format(514, 524) .. ")")
    after_ticks(600, function()
      local r = q("return kc.trial_report{finish = true}")
      clear(s, 495, 512, 530, 524)
      assert(change(r, "wooden-chest", 514.5) == 0, serpent.line(r))
      local stuck = 0
      for _, e in ipairs(r.entities) do if e.name == "inserter" then stuck = stuck + 1 end end
      assert(stuck == 2, "both inserters should be reported as not working: " .. serpent.line(r))
    end)
  end)

  it("says the grid produces nothing when it has no source", function()
    local s = game.surfaces[1]
    setup(s, 540, defines.direction.west, false)
    local st = q("return kc.trial_start(surface, " .. AREA:format(534, 544) .. ")")
    after_ticks(120, function()
      local r = q("return kc.trial_report{finish = true}")
      clear(s, 495, 532, 530, 544)
      local note = table.concat(st.notes, " | ")
      assert(note:find("has no power source", 1, true), note)
      local no_power = 0
      for _, e in ipairs(r.entities) do if e.status == "no_power" then no_power = no_power + 1 end end
      assert(no_power == 2, serpent.line(r))
    end)
  end)
end)
