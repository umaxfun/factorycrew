local protocol = require("scripts.protocol")

local POLE_RECIPES = { "small-electric-pole", "medium-electric-pole", "big-electric-pole", "substation" }

local function with_recipes(enabled, fn)
  local force = game.forces.player
  local saved = {}
  for _, r in ipairs(POLE_RECIPES) do
    saved[r] = force.recipes[r].enabled
    force.recipes[r].enabled = enabled[r] or false
  end
  local ok, err = pcall(fn)
  for r, v in pairs(saved) do force.recipes[r].enabled = v end
  if not ok then error(err, 0) end
end

local function q(lua)
  local r = protocol.run_query(lua)
  assert(r.ok, r.message)
  return r.data
end

describe("kc.poles (query helper)", function()
  it("picks the small pole when only it is researched", function()
    with_recipes({ ["small-electric-pole"] = true }, function()
      local p = q("return kc.poles()")
      assert(p.cover == "small-electric-pole" and p.reach == "small-electric-pole", serpent.line(p))
    end)
  end)

  it("prefers the larger supply area for cover and the longer wire for reach", function()
    with_recipes({ ["small-electric-pole"] = true, ["medium-electric-pole"] = true, ["big-electric-pole"] = true },
      function()
        local p = q("return kc.poles()")
        assert(p.cover == "medium-electric-pole" and p.reach == "big-electric-pole", serpent.line(p))
      end)
  end)

  it("takes substations for cover once they are researched", function()
    with_recipes({ ["small-electric-pole"] = true, ["medium-electric-pole"] = true, ["big-electric-pole"] = true,
                   substation = true }, function()
      local p = q("return kc.poles()")
      assert(p.cover == "substation" and p.reach == "big-electric-pole", serpent.line(p))
      local sub = p.poles[1]
      assert(sub.name == "substation" and sub.supply == 9 and sub.size == 2 and sub.craftable, serpent.line(sub))
    end)
  end)

  it("ignores poles that are not researched", function()
    with_recipes({ ["small-electric-pole"] = true }, function()
      for _, p in ipairs(q("return kc.poles()").poles) do
        assert(p.name ~= "substation" or p.have > 0, serpent.line(p))
      end
    end)
  end)
end)

describe("kc.place_ghosts (query helper)", function()
  it("places ghosts and refuses overlaps with entities and with ghosts of the same call", function()
    local s = game.surfaces[1]
    local tiles = {}
    for x = 400, 420 do for y = 400, 420 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
    s.set_tiles(tiles)
    local chest = s.create_entity{ name = "wooden-chest", position = { 415.5, 410.5 }, force = "player" }
    local well = s.create_entity{ name = "crude-oil", position = { 405.5, 405.5 }, amount = 1000 } -- a pumpjack needs one
    local r = q([[return kc.place_ghosts(surface, {
      {name = "pumpjack", x = 405.5, y = 405.5, direction = defines.direction.west},
      {name = "pipe", x = 405.5, y = 406.5},            -- inside the pumpjack ghost just placed
      {name = "pipe", x = 403.5, y = 406.5},            -- free
      {name = "pipe", x = 415.5, y = 410.5},            -- on the chest
      {name = "no-such-thing", x = 1, y = 1},
    })]])
    local ghosts = s.find_entities_filtered{ name = "entity-ghost", area = { { 400, 400 }, { 420, 420 } } }
    for _, g in pairs(ghosts) do g.destroy() end
    chest.destroy()
    well.destroy()
    assert(r.placed == 2 and #ghosts == 2, serpent.line(r))
    local idx = {}
    for _, f in ipairs(r.failed) do idx[#idx + 1] = f.index end
    assert(serpent.line(idx) == serpent.line({ 2, 4, 5 }), serpent.line(r.failed))
    assert(r.unpowered == 1 and r.hint and r.hint:find("Power them now", 1, true), serpent.line(r)) -- the pumpjack
  end)

  it("stops reporting unpowered once a pole covers the design", function()
    local s = game.surfaces[1]
    local tiles = {}
    for x = 430, 450 do for y = 430, 450 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
    s.set_tiles(tiles)
    local first = q([[return kc.place_ghosts(surface, {{name = "small-lamp", x = 440.5, y = 440.5}})]])
    local second = q([[return kc.place_ghosts(surface, {{name = "medium-electric-pole", x = 442.5, y = 440.5}})]])
    local check = q([[return kc.unpowered(surface, {{name = "small-lamp", x = 440.5, y = 440.5}})]])
    for _, g in pairs(s.find_entities_filtered{ name = "entity-ghost", area = { { 430, 430 }, { 450, 450 } } }) do
      g.destroy()
    end
    assert(first.unpowered == 1 and second.unpowered == nil and check == 0, serpent.line({ first, second, check }))
  end)
end)

describe("kc.nearest_pole (query helper)", function()
  it("finds the closest real pole and ignores ghosts", function()
    local s = game.surfaces[1]
    local far = s.create_entity{ name = "small-electric-pole", position = { 300, 300 }, force = "player" }
    local near = s.create_entity{ name = "medium-electric-pole", position = { 290, 300 }, force = "player" }
    local ghost = s.create_entity{ name = "entity-ghost", inner_name = "small-electric-pole", position = { 281, 300 },
                                   force = "player" }
    local r = q("return kc.nearest_pole(surface, {x = 280, y = 300}, 50)")
    far.destroy(); near.destroy(); ghost.destroy()
    assert(r and r.name == "medium-electric-pole" and r.position.x == 290.5, serpent.line(r))
    assert(q("return kc.nearest_pole(surface, {x = 280, y = 300}, 50)") == nil)
  end)
end)

-- ── world reading: kc.scan, kc.proto, kc.resources, kc.best ──────────────────
-- Each test also writes its result to script-output/kc-world/*.json: the recorded fixtures the
-- Python tests of kc.world read (copied into tests/unit/fixtures/kc_world/).

local function record(name, data)
  local ok = pcall(helpers.write_file, "kc-world/" .. name .. ".json", helpers.table_to_json(data), false)
  return ok
end

local function clear(s, x0, y0, x1, y1)
  for _, e in pairs(s.find_entities_filtered{ area = { { x0, y0 }, { x1, y1 } } }) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  local tiles = {}
  for x = x0, x1 - 1 do for y = y0, y1 - 1 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
  s.set_tiles(tiles)
end

local function find(list, name)
  for _, r in ipairs(list) do if r.name == name then return r end end
end

local function with_enabled(names, enabled, fn)
  local force = game.forces.player
  local saved = {}
  for _, r in ipairs(names) do
    saved[r] = force.recipes[r].enabled
    force.recipes[r].enabled = enabled[r] or false
  end
  local ok, err = pcall(fn)
  for r, v in pairs(saved) do force.recipes[r].enabled = v end
  if not ok then error(err, 0) end
end

describe("kc.scan (query helper)", function()
  it("lists entities and ghosts with rotated sizes, fields, and terrain as row runs", function()
    local s = game.surfaces[1]
    clear(s, 500, 500, 540, 540)
    local water = {}
    for x = 530, 534 do for y = 500, 501 do water[#water + 1] = { name = "water", position = { x, y } } end end
    s.set_tiles(water)
    local made = {}
    local function mk(t) local e = s.create_entity(t); assert(e, serpent.line(t)); made[#made + 1] = e; return e end
    local am = mk{ name = "assembling-machine-2", position = { 505.5, 505.5 }, force = "player" }
    am.set_recipe("iron-gear-wheel")
    mk{ name = "inserter", position = { 507.5, 505.5 }, direction = defines.direction.east, force = "player" }
    mk{ name = "underground-belt", position = { 510.5, 510.5 }, direction = defines.direction.east, type = "input",
        force = "player" }
    mk{ name = "underground-belt", position = { 513.5, 510.5 }, direction = defines.direction.east, type = "output",
        force = "player" }
    local pipe = mk{ name = "pipe", position = { 515.5, 505.5 }, force = "player" }
    pipe.insert_fluid{ name = "water", amount = 50 }
    mk{ name = "steam-engine", position = { 525.5, 510.5 }, direction = defines.direction.east, force = "player" }
    mk{ name = "entity-ghost", inner_name = "substation", position = { 520, 520 }, force = "player" }
    mk{ name = "entity-ghost", inner_name = "underground-belt", position = { 510.5, 515.5 }, type = "output",
        direction = defines.direction.south, force = "player" }
    local cliff = s.create_entity{ name = "cliff", position = { 520, 532 }, cliff_orientation = "west-to-east" }

    local r = q("return kc.scan(surface, {{500, 500}, {540, 540}}, {fields = true})")
    local small = q("return kc.scan(surface, {500, 500, 540, 540}, {limit = 2})")
    record("scan", r)
    for _, e in ipairs(made) do if e.valid then e.destroy() end end
    if cliff and cliff.valid then cliff.destroy() end

    assert(r.truncated == false and r.count == #r.entities and r.count == 8, serpent.line(r))
    assert(serpent.line(r.area) == serpent.line({ 500, 500, 540, 540 }), serpent.line(r.area))
    local a = find(r.entities, "assembling-machine-2")
    assert(a.x == 505.5 and a.w == 3 and a.h == 3 and a.recipe == "iron-gear-wheel" and a.force == "player"
           and a.unit and not a.ghost, serpent.line(a))
    local se = find(r.entities, "steam-engine")
    assert(se.w == 5 and se.h == 3 and se.direction == 4, serpent.line(se)) -- 3x5, rotated east
    local p = find(r.entities, "pipe")
    assert(p.fluid == "water", serpent.line(p)) -- 2.1 reads it through get_fluid_contents
    local ins = find(r.entities, "inserter")
    assert(ins.direction == 4 and ins.type == "inserter", serpent.line(ins))
    local ug_in, ug_ghost
    for _, e in ipairs(r.entities) do
      if e.name == "underground-belt" and e.io == "input" then ug_in = e end
      if e.name == "underground-belt" and e.ghost then ug_ghost = e end
    end
    assert(ug_in and ug_in.pair and ug_in.pair[1] == 513.5, serpent.line(ug_in))
    assert(ug_ghost and ug_ghost.io == "output" and ug_ghost.direction == 8, serpent.line(ug_ghost))
    local sub = find(r.entities, "substation")
    assert(sub.ghost and sub.w == 2 and sub.x == 520 and sub.type == "electric-pole", serpent.line(sub))
    local wt = 0
    for _, run in ipairs(r.tiles.water) do wt = wt + run[3] - run[2] + 1 end
    assert(wt == 10 and r.tiles.water[1][1] == 500 and r.tiles.water[1][2] == 530, serpent.line(r.tiles.water))
    assert(cliff == nil or #r.tiles.cliff > 0, serpent.line(r.tiles.cliff))
    assert(#r.tiles.void == 0, serpent.line(r.tiles.void))
    -- sorted by y then x
    for i = 2, #r.entities do
      local u, v = r.entities[i - 1], r.entities[i]
      assert(u.y < v.y or (u.y == v.y and u.x <= v.x), serpent.line({ u, v }))
    end
    assert(small.truncated and #small.entities == 2 and small.count == 8 and small.counts.pipe == 1,
           serpent.line(small))
    assert(small.entities[1].recipe == nil, "fields only when asked")
  end)

  it("cuts to the JSON budget and says so", function()
    local s = game.surfaces[1]
    clear(s, 560, 500, 600, 540)
    local made = {}
    for x = 560, 599 do for y = 500, 519 do
      made[#made + 1] = s.create_entity{ name = "wooden-chest", position = { x + 0.5, y + 0.5 }, force = "player" }
    end end
    local r = q("return kc.scan(surface, {{560, 500}, {600, 540}}, {limit = 5000, max_bytes = 20000})")
    for _, e in ipairs(made) do e.destroy() end
    assert(r.truncated and r.count == 800 and #r.entities < 800 and r.counts["wooden-chest"] == 800,
           serpent.line({ r.count, #r.entities }))
    assert(#helpers.table_to_json(r) <= 20000, #helpers.table_to_json(r))
  end)
end)

describe("kc.proto (query helper)", function()
  local names = { "pipe", "pipe-to-ground", "pumpjack", "electric-mining-drill", "burner-mining-drill",
                  "small-electric-pole", "substation", "transport-belt", "underground-belt", "fast-underground-belt",
                  "assembling-machine-2", "beacon", "inserter", "steam-engine", "offshore-pump", "no-such-thing" }

  it("gives the geometry and stats of each prototype", function()
    local p = q("return kc.proto(" .. serpent.line(names) .. ")")
    record("proto", p)
    assert(p["no-such-thing"] == false, serpent.line(p["no-such-thing"]))
    local sp, sub = p["small-electric-pole"], p.substation
    assert(sp.supply_area_distance == 2.5 and sp.max_wire_distance == 7.5 and sp.w == 1, serpent.line(sp))
    assert(sub.supply_area_distance == 9 and sub.max_wire_distance == 18 and sub.w == 2 and sub.item == "substation",
           serpent.line(sub))
    local pipe = p.pipe.fluid_boxes[1].ports
    assert(#pipe.north == 4 and pipe.north[1].x == 0 and pipe.north[1].kind == "normal", serpent.line(pipe.north))
    -- every port of the pumpjack sits inside its box and points out of it, for every facing
    local pj = p.pumpjack
    assert(pj.w == 3 and pj.energy == "electric", serpent.line(pj))
    local out
    for _, fb in ipairs(pj.fluid_boxes) do if fb.production == "output" then out = fb end end
    for _, d in ipairs({ "north", "east", "south", "west" }) do
      local port = out.ports[d][1]
      assert(math.abs(port.x) < 1.5 and math.abs(port.y) < 1.5, serpent.line(port))
      assert(math.abs(port.tx) > 1.5 or math.abs(port.ty) > 1.5, serpent.line(port))
    end
    assert(out.ports.north[1].direction == 0 and out.ports.east[1].direction == 4, serpent.line(out.ports))
    local ptg = p["pipe-to-ground"]
    local under
    for _, port in ipairs(ptg.fluid_boxes[1].ports.north) do if port.kind == "underground" then under = port end end
    assert(under and under.max and ptg.max_underground_distance == under.max and under.max >= 10, serpent.line(ptg))
    local dr = p["electric-mining-drill"]
    assert(dr.mining_radius == 2.49 and dr.mining_area[1] == -2.49 and dr.w == 3, serpent.line(dr))
    assert(dr.output.north[2] < -1.5 and dr.output.east[1] > 1.5 and dr.output.south[2] > 1.5
           and dr.output.west[1] < -1.5, serpent.line(dr.output))
    assert(dr.resource_categories[1] == "basic-solid" and dr.mining_speed == 0.5, serpent.line(dr))
    local tb = p["transport-belt"]
    assert(tb.belt_speed == 0.03125 and tb.items_per_second == 15 and tb.underground == "underground-belt",
           serpent.line(tb))
    assert(p["underground-belt"].max_underground_distance == 5 and p["fast-underground-belt"].max_underground_distance > 5,
           serpent.line(p["underground-belt"]))
    local am = p["assembling-machine-2"]
    assert(am.crafting_speed == 0.75 and am.module_slots == 2 and am.energy == "electric" and am.energy_usage > 0,
           serpent.line(am))
    local has
    for _, c in ipairs(am.crafting_categories) do if c == "crafting" then has = true end end
    assert(has, serpent.line(am.crafting_categories))
    local b = p.beacon
    assert(b.supply_area_distance == 3 and b.distribution_effectivity == 1.5 and b.w == 3 and b.module_slots == 2,
           serpent.line(b))
    assert(p.inserter.pickup and p.inserter.drop and p.inserter.drop[2] ~= p.inserter.pickup[2], serpent.line(p.inserter))
    local st = p["steam-engine"]
    assert(st.w == 3 and st.h == 5 and st.collision_box[2] < -2, serpent.line(st))
    assert(p["burner-mining-drill"].energy == "burner", serpent.line(p["burner-mining-drill"]))
  end)

  it("takes one name as a string", function()
    local p = q('return kc.proto("pipe")')
    assert(p.pipe and p.pipe.type == "pipe", serpent.line(p))
  end)
end)

describe("kc.resources (query helper)", function()
  it("groups resource tiles by name with totals, sorted tiles and a budget", function()
    local s = game.surfaces[1]
    clear(s, 620, 500, 660, 540)
    local made = {}
    for x = 630, 632 do for y = 510, 512 do
      made[#made + 1] = s.create_entity{ name = "iron-ore", position = { x + 0.5, y + 0.5 }, amount = 100 + x - 630 }
    end end
    made[#made + 1] = s.create_entity{ name = "copper-ore", position = { 634.5, 510.5 }, amount = 50 }
    made[#made + 1] = s.create_entity{ name = "copper-ore", position = { 634.5, 511.5 }, amount = 70 }
    made[#made + 1] = s.create_entity{ name = "crude-oil", position = { 638.5, 516.5 }, amount = 300000 }
    local r = q("return kc.resources(surface, {x = 632, y = 512}, 12)")
    local only = q('return kc.resources(surface, {{620, 500}, {660, 540}}, nil, {"copper-ore"})')
    local cut = q("return kc.resources(surface, {{620, 500}, {660, 540}}, nil, nil, {limit = 6})")
    record("resources", r)
    for _, e in ipairs(made) do e.destroy() end
    local fe = r["iron-ore"]
    assert(fe.count == 9 and fe.amount == 3 * (100 + 101 + 102) and not fe.truncated, serpent.line(fe))
    assert(serpent.line(fe.bbox) == serpent.line({ 630, 510, 633, 513 }), serpent.line(fe.bbox))
    assert(fe.tiles[1][1] == 630.5 and fe.tiles[1][2] == 510.5 and fe.tiles[1][3] == 100, serpent.line(fe.tiles[1]))
    assert(fe.tiles[2][1] == 631.5 and fe.tiles[4][2] == 511.5, serpent.line(fe.tiles))
    assert(fe.category == "basic-solid" and fe.products[1] == "iron-ore", serpent.line(fe))
    assert(r["crude-oil"].infinite and r["crude-oil"].products[1] == "crude-oil", serpent.line(r["crude-oil"]))
    assert(r["copper-ore"].amount == 120, serpent.line(r["copper-ore"]))
    assert(only["copper-ore"] and only["iron-ore"] == nil, serpent.line(only))
    assert(cut["iron-ore"].truncated and #cut["iron-ore"].tiles == 2 and cut["iron-ore"].count == 9
           and not cut["copper-ore"].truncated, serpent.line(cut))
  end)
end)

describe("kc.best (query helper)", function()
  local BELTS = { "transport-belt", "fast-transport-belt", "express-transport-belt" }

  it("ranks the researched entities of a type by a key", function()
    with_enabled(BELTS, { ["transport-belt"] = true, ["fast-transport-belt"] = true }, function()
      local r = q('return kc.best("transport-belt", "belt_speed")')
      record("best_belt", r)
      assert(r.name == "fast-transport-belt" and r.value == 0.0625 and #r.candidates == 2 and r.craftable,
             serpent.line(r))
    end)
    with_enabled(BELTS, {}, function()
      local r = q('return kc.best("transport-belt", "belt_speed")')
      assert(r.name == nil and #r.candidates == 0, serpent.line(r))
    end)
  end)

  it("matches kc.poles for supply and filters by category", function()
    with_enabled(POLE_RECIPES, { ["small-electric-pole"] = true, ["medium-electric-pole"] = true }, function()
      local b, p = q('return kc.best("electric-pole", "supply")'), q("return kc.poles()")
      assert(b.name == p.cover and b.name == "medium-electric-pole", serpent.line({ b, p }))
      assert(q('return kc.best("electric-pole", "reach")').name == p.reach)
    end)
    local am = q('return kc.best("assembling-machine", "crafting_speed", {category = "crafting", all = true})')
    assert(am.name == "assembling-machine-3", serpent.line(am))
    local dr = q('return kc.best("mining-drill", "mining_radius", {category = "basic-solid", all = true})')
    assert(dr.name == "electric-mining-drill", serpent.line(dr))
    local f = q('return kc.best("transport-belt", function(p) return -p.belt_speed end, {all = true})')
    assert(f.name == "transport-belt", serpent.line(f))
    local bad = protocol.run_query('return kc.best("transport-belt", "colour")')
    assert(not bad.ok and bad.message:find("unknown key", 1, true), serpent.line(bad))
  end)
end)
