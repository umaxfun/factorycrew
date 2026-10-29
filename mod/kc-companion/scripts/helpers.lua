-- Helpers for agent queries, available as `kc` in factorio_query's environment.
-- They live in the mod (not in the agent's prompt) so level 1 tests run them on the real API.
local M = {}

--- Whether the force can make `item` now: some enabled recipe produces it.
local function craftable(force, item)
  local recipes = prototypes.get_recipe_filtered{
    { filter = "has-product-item", elem_filters = { { filter = "name", name = item } } },
  }
  for name in pairs(recipes) do
    local r = force.recipes[name]
    if r and r.enabled then return true end
  end
  return false
end

--- Electric poles this force can build now (an enabled recipe makes one, or the player carries some).
--- Returns {cover = name with the largest supply area, reach = name with the longest wire reach,
--- poles = {{name, item, supply, reach, size, craftable, have}, ...} best cover first}.
--- Works for modded poles too: it reads the prototypes, not a list of vanilla names.
function M.poles(force, player)
  force = force or game.forces.player
  player = player or game.connected_players[1]
  local list = {}
  for name, proto in pairs(prototypes.get_entity_filtered{ { filter = "type", type = "electric-pole" } }) do
    local place = proto.items_to_place_this
    local item = place and place[1] and place[1].name
    if item and not proto.hidden then
      local can = craftable(force, item)
      local have = (player and player.valid) and player.get_item_count(item) or 0
      if can or have > 0 then
        list[#list + 1] = {
          name = name, item = item, supply = proto.get_supply_area_distance(), reach = proto.get_max_wire_distance(),
          size = proto.tile_width, craftable = can, have = have,
        }
      end
    end
  end
  table.sort(list, function(a, b)
    if a.supply ~= b.supply then return a.supply > b.supply end
    if a.reach ~= b.reach then return a.reach > b.reach end
    return a.name < b.name
  end)
  local reach
  for _, p in ipairs(list) do
    if not reach or p.reach > reach.reach or (p.reach == reach.reach and p.supply > reach.supply) then reach = p end
  end
  return { cover = list[1] and list[1].name, reach = reach and reach.name, poles = list }
end

--- Place ghosts the way the player's ghost cursor would: an item is refused if it overlaps an
--- entity, another ghost (including one placed earlier in this same call) or unbuildable ground.
--- items: {{name, x, y, direction?}, ...} (position = entity center). Nothing is placed for refused
--- items. Returns {placed = n, failed = {{index, name, x, y, reason}, ...}}.
function M.place_ghosts(surface, items, force)
  force = force or game.forces.player
  local placed, failed = 0, {}
  for i, it in ipairs(items or {}) do
    local reason
    if not (it.name and prototypes.entity[it.name]) then
      reason = "unknown entity " .. tostring(it.name)
    else
      local pos = { x = it.x or (it.position and it.position.x), y = it.y or (it.position and it.position.y) }
      -- snap to the entity's grid as the game does: odd sizes sit on tile centers (x.5),
      -- even sizes (2x2 substation) on tile corners (integers). 298.0 for a pipe means 298.5.
      local proto = prototypes.entity[it.name]
      local function snap(v, size) return (size % 2 == 1) and (math.floor(v) + 0.5) or math.floor(v + 0.5) end
      if pos.x and pos.y then
        local w, h = proto.tile_width, proto.tile_height
        local d = it.direction or 0
        if d == defines.direction.east or d == defines.direction.west then w, h = h, w end
        pos = { x = snap(pos.x, w), y = snap(pos.y, h) }
      end
      local args = { name = it.name, position = pos, direction = it.direction or defines.direction.north,
                     force = force, build_check_type = defines.build_check_type.manual_ghost }
      if not surface.can_place_entity(args) then
        reason = "blocked (overlaps an entity or ghost, or bad ground)"
      else
        -- `type` = "input"/"output" for underground belts (one half of a pair), as in blueprints
        local g = surface.create_entity{ name = "entity-ghost", inner_name = it.name, position = pos,
                                         direction = args.direction, force = force, type = it.type }
        if g then placed = placed + 1 else reason = "create_entity refused" end
      end
    end
    if reason then failed[#failed + 1] = { index = i, name = it.name, x = it.x, y = it.y, reason = reason } end
  end
  local out = { placed = placed, failed = failed }
  -- Power check on what was just placed: the hint lands in the tool result, where the agent
  -- reads it, instead of relying on it remembering the "power by default" rule.
  local unpowered = M.unpowered(surface, items, force)
  if unpowered > 0 then
    out.unpowered = unpowered
    out.hint = unpowered .. " electric ghosts are outside every pole's supply area. Power them now, in this turn: " ..
      "kc.poles() for the best pole, kc.nearest_pole() for the grid, place poles with kc.place_ghosts."
  end
  if #failed > 0 then
    out.failed_hint = "Some items were refused; move them (recompute the layout) and place them, do not leave the design partial."
  end
  return out
end

--- How many of `items` (electric entities only) sit outside the supply area of every pole and
--- pole ghost of the force. Supply area is a square of half-size supply_area_distance.
function M.unpowered(surface, items, force)
  force = force or game.forces.player
  local n = 0
  for _, it in ipairs(items or {}) do
    local proto = it.name and prototypes.entity[it.name]
    if proto and proto.type ~= "electric-pole" and proto.electric_energy_source_prototype then
      local x = it.x or (it.position and it.position.x)
      local y = it.y or (it.position and it.position.y)
      local covered = false
      for _, e in pairs(surface.find_entities_filtered{ position = { x, y }, radius = 64, force = force,
                                                         type = { "electric-pole", "entity-ghost" } }) do
        local pname = e.type == "entity-ghost" and e.ghost_name or e.name
        local pp = prototypes.entity[pname]
        if pp.type == "electric-pole" then
          local d = pp.get_supply_area_distance()
          local hw, hh = (proto.tile_width or 1) / 2, (proto.tile_height or 1) / 2
          if math.abs(e.position.x - x) <= d + hw and math.abs(e.position.y - y) <= d + hh then
            covered = true
            break
          end
        end
      end
      if not covered then n = n + 1 end
    end
  end
  return n
end

--- The nearest existing pole of the force within `radius` of `position` (real poles only, not ghosts):
--- {name, position, distance, network_id} or nil. Chain new pole ghosts from here.
function M.nearest_pole(surface, position, radius, force)
  force = force or game.forces.player
  local best, best_d
  for _, e in pairs(surface.find_entities_filtered{
    type = "electric-pole", force = force, position = position, radius = radius or 200,
  }) do
    local dx, dy = e.position.x - position.x, e.position.y - position.y
    local d = math.sqrt(dx * dx + dy * dy)
    if not best_d or d < best_d then best, best_d = e, d end
  end
  if not best then return nil end
  return {
    name = best.name, position = { x = best.position.x, y = best.position.y },
    distance = math.floor(best_d * 10 + 0.5) / 10, network_id = best.electric_network_id,
  }
end

-- ── world reading: kc.scan, kc.proto, kc.resources, kc.best ─────────────────
-- Plain JSON-safe tables only. Every API read goes through `get`, which survives a property that
-- does not exist on this version (2.0 vs 2.1) or on this prototype type, and calls methods.

local DIRS = { 0, 4, 8, 12 }
local DIR_WORD = { [0] = "north", [4] = "east", [8] = "south", [12] = "west" }
local STEP = { [0] = { 0, -1 }, [4] = { 1, 0 }, [8] = { 0, 1 }, [12] = { -1, 0 } }

--- obj[key], or obj[key](...) when it is a method; nil when missing or when the read raises.
local function get(obj, key, ...)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if not ok or v == nil then return nil end
  if type(v) == "function" then
    local args = { ... }
    local ok2, r = pcall(function() return v(table.unpack(args)) end)
    if ok2 then return r end
    return nil
  end
  return v
end

--- First non-nil of several keys.
local function first(obj, ...)
  for _, k in ipairs({ ... }) do
    local v = get(obj, k)
    if v ~= nil then return v end
  end
  return nil
end

local function round(v, n)
  if type(v) ~= "number" then return v end
  return tonumber(string.format("%." .. (n or 4) .. "f", v)) -- 0.49, not 0.49000000000000005
end

local function xy_of(p)
  if type(p) ~= "table" then return nil end
  local x, y = p.x or p[1], p.y or p[2]
  if type(x) ~= "number" or type(y) ~= "number" then return nil end
  return x, y
end

--- {x0, y0, x1, y1} from {{x0,y0},{x1,y1}}, {left_top=, right_bottom=} or {x0, y0, x1, y1}.
local function box_of(a)
  if type(a) ~= "table" then return nil end
  if a.left_top then
    local x0, y0 = xy_of(a.left_top)
    local x1, y1 = xy_of(a.right_bottom)
    if x0 and x1 then return { x0, y0, x1, y1 } end
    return nil
  end
  if type(a[1]) == "number" and type(a[4]) == "number" then return { a[1], a[2], a[3], a[4] } end
  local x0, y0 = xy_of(a[1])
  local x1, y1 = xy_of(a[2])
  if x0 and x1 then return { math.min(x0, x1), math.min(y0, y1), math.max(x0, x1), math.max(y0, y1) } end
  return nil
end

local function sorted_keys(t)
  local out = {}
  for k, v in pairs(t or {}) do if v then out[#out + 1] = k end end
  table.sort(out)
  return out
end

local function by_yx(a, b)
  if a.y ~= b.y then return a.y < b.y end
  if a.x ~= b.x then return a.x < b.x end
  return (a.name or "") < (b.name or "")
end

--- Size in tiles as placed: tile_width x tile_height, swapped when facing east or west.
local function oriented(proto, direction)
  local w, h = get(proto, "tile_width") or 1, get(proto, "tile_height") or 1
  if direction == 4 or direction == 12 then w, h = h, w end
  return w, h
end

-- Tile classes, built once from the tile prototypes: water (any liquid layer), void (out of map).
local tile_class_cache
local function tile_classes()
  if tile_class_cache then return tile_class_cache end
  local classes = { water = {}, void = {} }
  for name, t in pairs(prototypes.tile) do
    local mask = get(t, "collision_mask") or {}
    local layers = mask.layers or mask
    if name == "out-of-map" or layers.empty_space or layers["out-of-map"] then
      classes.void[#classes.void + 1] = name
    elseif layers.water_tile or layers.lava_tile then
      classes.water[#classes.water + 1] = name
    end
  end
  table.sort(classes.water)
  table.sort(classes.void)
  tile_class_cache = classes
  return classes
end

--- Row runs {y, x_from, x_to} (integer tile indices; tile center = index + 0.5) from {[y] = {[x] = true}}.
local function runs_of(rows)
  local ys = {}
  for y in pairs(rows) do ys[#ys + 1] = y end
  table.sort(ys)
  local out = {}
  for _, y in ipairs(ys) do
    local xs = {}
    for x in pairs(rows[y]) do xs[#xs + 1] = x end
    table.sort(xs)
    local s, p = xs[1], xs[1]
    for i = 2, #xs + 1 do
      local x = xs[i]
      if x ~= p + 1 then
        out[#out + 1] = { y, s, p }
        s = x
      end
      p = x
    end
  end
  return out
end

local function add_tile(rows, x, y)
  rows[y] = rows[y] or {}
  rows[y][x] = true
end

local SCAN_SKIP = {
  resource = true, character = true, corpse = true, ["character-corpse"] = true, ["item-entity"] = true,
  explosion = true, ["particle-source"] = true, projectile = true, ["smoke-with-trigger"] = true, sticker = true,
  ["highlight-box"] = true, ["speech-bubble"] = true, beam = true, stream = true, fire = true,
  ["tile-ghost"] = true, ["deconstructible-tile-proxy"] = true, ["item-request-proxy"] = true, cliff = true,
}

local FIELDS = { "recipe", "filters", "fluid", "io", "pair" }

local function item_name(f)
  if type(f) == "table" then return f.name and (type(f.name) == "string" and f.name or get(f.name, "name")) end
  if type(f) == "string" then return f end
  return nil
end

local function entity_fields(e, rec, want)
  local t = rec.type
  if want.recipe and (t == "assembling-machine" or t == "furnace" or t == "rocket-silo") then
    local r = get(e, "get_recipe")
    if r == nil and t == "furnace" then r = get(e, "previous_recipe") end
    local rn = r and (get(r, "name") or (type(r) == "table" and r.name and get(r.name, "name")))
    if type(rn) == "string" then rec.recipe = rn end
  end
  if want.filters then
    local list = {}
    if t == "splitter" then
      local f = item_name(get(e, "splitter_filter"))
      if f then list[1] = f end
    elseif t == "inserter" or t == "loader" or t == "loader-1x1" then
      local n = get(e, "filter_slot_count") or 0
      for i = 1, n do
        local f = item_name(get(e, "get_filter", i))
        if f then list[#list + 1] = f end
      end
    end
    if #list > 0 then rec.filters = list end
  end
  if want.fluid and not rec.ghost then
    local fb = get(e, "fluidbox")
    local okn, n = pcall(function() return #fb end)
    for i = 1, (okn and n or 0) do
      local ok, f = pcall(function() return fb[i] end)
      if ok and f and f.name then rec.fluid = f.name; break end
    end
    if not rec.fluid then -- whole-entity contents: {[name] = amount} (2.0) or a list of {name, amount}
      local c = get(e, "get_fluid_contents")
      if type(c) == "table" then
        local names = {}
        for k, v in pairs(c) do
          if type(k) == "string" then names[#names + 1] = k
          elseif type(v) == "table" and v.name then names[#names + 1] = v.name end
        end
        table.sort(names)
        rec.fluid = names[1]
      end
    end
  end
  if want.io then
    if t == "underground-belt" then rec.io = get(e, "belt_to_ground_type")
    elseif t == "loader" or t == "loader-1x1" then rec.io = get(e, "loader_type") end
  end
  if want.pair and t == "underground-belt" and not rec.ghost then
    -- 2.0: `neighbours` is the paired underground; 2.1 renames it
    local n = first(e, "underground_belt_neighbour", "neighbours")
    if type(n) == "table" and not get(n, "position") then n = n[1] end
    local p = get(n, "position")
    if p then rec.pair = { p.x, p.y } end
  end
end

local function encoded_size(t)
  local ok, s = pcall(helpers.table_to_json, t)
  return ok and type(s) == "string" and #s or math.huge
end

--- Standard snapshot of everything in a box.
--- surface: LuaSurface (nil = player's / first). area: {{x0,y0},{x1,y1}}, {left_top, right_bottom} or {x0,y0,x1,y1}.
--- opts: {fields = true | {"recipe","filters","fluid","io","pair"}, types = {...}, names = {...},
---        skip_types = {[type] = true} (replaces the default skip set), limit = 400 (entities),
---        max_bytes = 48000 (JSON budget for the whole result), tiles = true}.
--- Returns {area = {x0,y0,x1,y1}, entities = {{name, type, x, y, w, h, direction, ghost?, force, unit?, ...fields}},
---          tiles = {water = runs, cliff = runs, void = runs}, count = entities found, truncated = bool}.
--- w/h are the size as placed (rotated). Runs are {y, x_from, x_to} of integer tile indices.
--- Resources and characters are left out (kc.resources reads ore); cliffs are tiles, not entities.
function M.scan(surface, area, opts)
  opts = opts or {}
  surface = surface or (game.connected_players[1] and game.connected_players[1].surface) or game.surfaces[1]
  local box = box_of(area)
  if not box then error("kc.scan: area must be {{x0,y0},{x1,y1}} or {x0,y0,x1,y1}") end
  local fa = { { box[1], box[2] }, { box[3], box[4] } }
  local want = {}
  if opts.fields == true then for _, f in ipairs(FIELDS) do want[f] = true end
  elseif type(opts.fields) == "table" then for _, f in ipairs(opts.fields) do want[f] = true end end
  local skip = opts.skip_types or SCAN_SKIP

  local filter = { area = fa }
  if opts.types then filter.type = opts.types end
  if opts.names then filter.name = opts.names end
  local list, cliffs = {}, {}
  for _, e in pairs(surface.find_entities_filtered(filter)) do
    if e.valid then
      local et = e.type
      if et == "cliff" then
        cliffs[#cliffs + 1] = e
      else
        local ghost = et == "entity-ghost"
        local proto = ghost and e.ghost_prototype or e.prototype
        local ptype = ghost and e.ghost_type or et
        if not skip[ptype] then
          local d = e.direction or 0
          local w, h = oriented(proto, d)
          local rec = { name = ghost and e.ghost_name or e.name, type = ptype, x = e.position.x, y = e.position.y,
                        w = w, h = h, direction = d, force = e.force and e.force.name, unit = get(e, "unit_number") }
          if ghost then
            rec.ghost = true
            if ptype == "underground-belt" and want.io then rec.io = get(e, "belt_to_ground_type") end
          end
          if next(want) then entity_fields(e, rec, want) end
          list[#list + 1] = rec
        end
      end
    end
  end
  table.sort(list, by_yx)

  local tiles = {}
  if opts.tiles ~= false then
    local classes = tile_classes()
    for cls, names in pairs(classes) do
      local rows = {}
      if #names > 0 then
        for _, t in pairs(surface.find_tiles_filtered{ area = fa, name = names }) do
          add_tile(rows, math.floor(t.position.x), math.floor(t.position.y))
        end
      end
      tiles[cls] = runs_of(rows)
    end
    local rows = {}
    for _, c in ipairs(cliffs) do
      local bb = c.bounding_box
      for x = math.floor(bb.left_top.x), math.ceil(bb.right_bottom.x) - 1 do
        for y = math.floor(bb.left_top.y), math.ceil(bb.right_bottom.y) - 1 do
          if x + 0.5 > bb.left_top.x and x + 0.5 < bb.right_bottom.x and y + 0.5 > bb.left_top.y
             and y + 0.5 < bb.right_bottom.y and x >= box[1] and x < box[3] and y >= box[2] and y < box[4] then
            add_tile(rows, x, y)
          end
        end
      end
    end
    tiles.cliff = runs_of(rows)
  end

  local limit = opts.limit or 400
  local truncated = false
  local keep = list
  if #list > limit then
    keep = {}
    for i = 1, limit do keep[i] = list[i] end
    truncated = true
  end
  local out = { area = box, entities = keep, tiles = tiles, count = #list, truncated = truncated }
  local function add_counts()
    if out.counts then return end
    local counts = {}
    for _, r in ipairs(list) do counts[r.name] = (counts[r.name] or 0) + 1 end
    out.counts = counts -- every entity found, by name, so a cut snapshot still says what is there
  end
  if truncated then add_counts() end
  -- stay under the query result cap: cut entities, then tile runs, until the JSON fits
  local budget = opts.max_bytes or 48000
  while encoded_size(out) > budget do
    add_counts()
    local cut = false
    if #out.entities > 0 then
      local n = math.floor(#out.entities * 3 / 4)
      local k = {}
      for i = 1, n do k[i] = out.entities[i] end
      out.entities = k
      cut = true
    else
      for cls, r in pairs(out.tiles) do
        if #r > 0 then
          local k = {}
          for i = 1, math.floor(#r / 2) do k[i] = r[i] end
          out.tiles[cls] = k
          out.tiles_truncated = true
          cut = true
        end
      end
    end
    out.truncated = true
    if not cut then break end
  end
  return out
end

local function vec(v)
  local x, y = xy_of(v)
  if not x then return nil end
  return { round(x), round(y) }
end

local function rot(x, y, d) -- clockwise by d (0/4/8/12), screen coordinates
  for _ = 1, d / 4 do x, y = -y, x end
  return x, y
end

local function pipe_ports(fb)
  local ports = {}
  for _, d in ipairs(DIRS) do ports[DIR_WORD[d]] = {} end
  for _, c in ipairs(get(fb, "pipe_connections") or {}) do
    local cd = c.direction or 0
    local kind = c.connection_type or "normal"
    for i, d in ipairs(DIRS) do
      local x, y
      if c.positions and c.positions[i] then
        x, y = xy_of(c.positions[i])
      elseif c.position then
        x, y = xy_of(c.position)
        if x then x, y = rot(x, y, d) end
      end
      if x then
        local od = (cd + d) % 16
        local s = STEP[od]
        local port = { x = round(x), y = round(y), direction = od, flow = c.flow_direction or c.type, kind = kind }
        if s then port.tx, port.ty = round(x + s[1]), round(y + s[2]) end
        if c.max_underground_distance then port.max = c.max_underground_distance end
        if c.connection_category then port.category = c.connection_category end
        local list = ports[DIR_WORD[d]]
        list[#list + 1] = port
      end
    end
  end
  return ports
end

local function energy_of(p)
  local kind
  if get(p, "electric_energy_source_prototype") then kind = "electric"
  elseif get(p, "burner_prototype") then kind = "burner"
  elseif get(p, "heat_energy_source_prototype") then kind = "heat"
  elseif get(p, "fluid_energy_source_prototype") then kind = "fluid"
  elseif get(p, "void_energy_source_prototype") then kind = "void" end
  return kind
end

--- Per-prototype geometry and stats planners need. names: one name or a list.
--- Returns {[name] = spec or false (unknown)}. spec: {name, type, w, h, collision_box = {x0,y0,x1,y1}, item?,
---   energy? ("electric"|"burner"|"heat"|"fluid"|"void"), energy_usage? (W), fluid_boxes? = {{production,
---   volume?, ports = {north|east|south|west = {{x, y, tx, ty, direction, flow, kind, max?}}}}},
---   supply_area_distance?, max_wire_distance?, mining_radius?, mining_area? = {x0,y0,x1,y1}, mining_speed?,
---   resource_categories?, output? = {north|east|south|west = {x, y}}, belt_speed?, items_per_second?,
---   max_underground_distance?, underground?, crafting_speed?, crafting_categories?, module_slots?,
---   allowed_effects?, distribution_effectivity?, profile?, pickup?, drop?}.
--- Positions are relative to the entity center; `ports[dir]` are the connections when the entity faces dir:
--- (x, y) the tile inside the entity, (tx, ty) the tile a pipe must be on to connect.
function M.proto(names)
  if type(names) == "string" then names = { names } end
  local out = {}
  for _, name in ipairs(names or {}) do
    local p = prototypes.entity[name]
    if not p then
      out[name] = false
    else
      local cb = get(p, "collision_box")
      local s = { name = name, type = p.type, w = get(p, "tile_width"), h = get(p, "tile_height"),
                  collision_box = cb and { round(cb.left_top.x), round(cb.left_top.y), round(cb.right_bottom.x),
                                           round(cb.right_bottom.y) } }
      local place = get(p, "items_to_place_this")
      if place and place[1] then s.item = item_name(place[1]) end
      s.energy = energy_of(p)
      local use = get(p, "get_max_energy_usage") or get(p, "max_energy_usage") or get(p, "energy_usage")
      if type(use) == "number" and use > 0 then s.energy_usage = round(use * 60, 2) end

      local fbs = {}
      for _, fb in ipairs(get(p, "fluidbox_prototypes") or {}) do
        fbs[#fbs + 1] = { production = get(fb, "production_type"), volume = first(fb, "volume", "get_volume"),
                          ports = pipe_ports(fb) }
      end
      if #fbs > 0 then s.fluid_boxes = fbs end

      local t = p.type
      if t == "electric-pole" then
        s.supply_area_distance = round(first(p, "get_supply_area_distance", "supply_area_distance"))
        s.max_wire_distance = round(first(p, "get_max_wire_distance", "max_wire_distance"))
      end
      if t == "beacon" then
        s.supply_area_distance = round(first(p, "get_supply_area_distance", "supply_area_distance"))
        s.distribution_effectivity = get(p, "distribution_effectivity")
        local prof = get(p, "profile")
        if type(prof) == "table" then
          s.profile = {}
          for i, v in ipairs(prof) do s.profile[i] = round(v) end
        end
      end
      if t == "mining-drill" then
        local r = get(p, "mining_drill_radius")
        s.mining_radius = round(r)
        if r then s.mining_area = { round(-r), round(-r), round(r), round(r) } end
        s.mining_speed = get(p, "mining_speed")
        s.resource_categories = sorted_keys(get(p, "resource_categories"))
        local v = get(p, "vector_to_place_result")
        local output = {}
        if type(v) == "table" and v.north then -- per-direction form
          for _, d in ipairs(DIRS) do output[DIR_WORD[d]] = vec(v[DIR_WORD[d]]) end
        else
          local x, y = xy_of(v)
          if x then for _, d in ipairs(DIRS) do local rx, ry = rot(x, y, d); output[DIR_WORD[d]] = { round(rx), round(ry) } end end
        end
        if next(output) then s.output = output end
      end
      local bs = get(p, "belt_speed")
      if bs then
        s.belt_speed = bs
        s.items_per_second = round(bs * 480, 3)
      end
      if t == "underground-belt" then s.max_underground_distance = get(p, "max_underground_distance") end
      if t == "transport-belt" then
        local u = get(p, "related_underground_belt")
        if u then s.underground = u.name end
      end
      if t == "pipe-to-ground" and fbs[1] then
        for _, ps in pairs(fbs[1].ports) do
          for _, port in ipairs(ps) do if port.max then s.max_underground_distance = port.max end end
        end
      end
      local cs = first(p, "get_crafting_speed", "crafting_speed")
      local cc = get(p, "crafting_categories")
      if cs and cc then
        s.crafting_speed = cs
        s.crafting_categories = sorted_keys(cc)
      end
      local slots = get(p, "module_inventory_size")
      if slots and slots > 0 then
        s.module_slots = slots
        local ae = get(p, "allowed_effects")
        if type(ae) == "table" then s.allowed_effects = sorted_keys(ae) end
      end
      if t == "inserter" then
        s.pickup = vec(get(p, "inserter_pickup_position"))
        s.drop = vec(get(p, "inserter_drop_position"))
      end
      out[name] = s
    end
  end
  return out
end

--- Resources near a point or in a box, grouped by name.
--- area_or_center: a box (as kc.scan) or a point {x, y}; radius: used with a point (circle).
--- names: optional list of resource names. opts: {limit = 2000 (tiles listed in total)}.
--- Returns {[name] = {name, count, amount, bbox = {x0,y0,x1,y1}, category, products = {...}, infinite?,
---          tiles = {{x, y, amount}, ...} sorted by y then x, truncated}}.
--- amount and bbox cover every tile even when `tiles` is cut.
function M.resources(surface, area_or_center, radius, names, opts)
  opts = opts or {}
  surface = surface or (game.connected_players[1] and game.connected_players[1].surface) or game.surfaces[1]
  local filter = { type = "resource" }
  local box = box_of(area_or_center)
  if box then
    filter.area = { { box[1], box[2] }, { box[3], box[4] } }
  else
    local x, y = xy_of(area_or_center)
    if not x then error("kc.resources: give a box or a point") end
    filter.position = { x, y }
    filter.radius = radius or 32
  end
  if type(names) == "string" then names = { names } end
  if names then filter.name = names end
  local groups = {}
  for _, e in pairs(surface.find_entities_filtered(filter)) do
    local g = groups[e.name]
    if not g then
      local p = e.prototype
      local products = {}
      local mp = get(p, "mineable_properties")
      for _, pr in ipairs((mp and mp.products) or {}) do products[#products + 1] = pr.name end
      g = { name = e.name, count = 0, amount = 0, category = get(p, "resource_category"), products = products,
            infinite = get(p, "infinite_resource") or nil, tiles = {} }
      groups[e.name] = g
    end
    local x, y = e.position.x, e.position.y
    g.count = g.count + 1
    g.amount = g.amount + e.amount
    if not g.bbox then g.bbox = { x - 0.5, y - 0.5, x + 0.5, y + 0.5 }
    else
      g.bbox[1], g.bbox[2] = math.min(g.bbox[1], x - 0.5), math.min(g.bbox[2], y - 0.5)
      g.bbox[3], g.bbox[4] = math.max(g.bbox[3], x + 0.5), math.max(g.bbox[4], y + 0.5)
    end
    g.tiles[#g.tiles + 1] = { x, y, e.amount }
  end
  local n = 0
  for _ in pairs(groups) do n = n + 1 end
  local per = n > 0 and math.floor((opts.limit or 2000) / n) or 0
  for _, g in pairs(groups) do
    table.sort(g.tiles, function(a, b) if a[2] ~= b[2] then return a[2] < b[2] end return a[1] < b[1] end)
    g.truncated = #g.tiles > per
    if g.truncated then
      local k = {}
      for i = 1, per do k[i] = g.tiles[i] end
      g.tiles = k
    end
  end
  return groups
end

-- Metrics kc.best ranks by. Each takes an entity prototype and returns a number or nil.
local METRICS = {
  belt_speed = function(p) return get(p, "belt_speed") end,
  supply = function(p) return first(p, "get_supply_area_distance", "supply_area_distance") end,
  reach = function(p) return first(p, "get_max_wire_distance", "max_wire_distance") end,
  crafting_speed = function(p) return first(p, "get_crafting_speed", "crafting_speed") end,
  mining_speed = function(p) return get(p, "mining_speed") end,
  mining_radius = function(p) return get(p, "mining_drill_radius") end,
  max_underground_distance = function(p) return get(p, "max_underground_distance") end,
  module_slots = function(p) return get(p, "module_inventory_size") end,
  distribution_effectivity = function(p) return get(p, "distribution_effectivity") end,
  size = function(p) return (get(p, "tile_width") or 0) * (get(p, "tile_height") or 0) end,
}
M.best_keys = sorted_keys(METRICS)

--- The best entity of a prototype type this force can build now (an enabled recipe makes its item, or
--- the player carries some), ranked by `key`: a METRICS name ("belt_speed", "supply", "reach",
--- "crafting_speed", "mining_speed", "mining_radius", "max_underground_distance", "module_slots",
--- "distribution_effectivity", "size") or a function(proto) -> number.
--- opts: {force, player, category = crafting or resource category the entity must have,
---        where = function(proto) -> bool, all = true (ignore research: every non-hidden prototype)}.
--- Returns {name, item, value, craftable, have, candidates = {{name, item, value, craftable, have}, ...}}
--- best first (ties by name), or {candidates = {}} when none qualifies.
function M.best(ptype, key, opts)
  opts = opts or {}
  local force = opts.force or game.forces.player
  local player = opts.player or game.connected_players[1]
  local metric = type(key) == "function" and key or METRICS[key]
  if not metric then error("kc.best: unknown key " .. tostring(key) .. "; use one of " .. table.concat(M.best_keys, ", ")) end
  local list = {}
  for name, p in pairs(prototypes.get_entity_filtered{ { filter = "type", type = ptype } }) do
    local place = get(p, "items_to_place_this")
    local item = place and place[1] and item_name(place[1])
    if item and not get(p, "hidden") then
      local ok_cat = true
      if opts.category then
        local cats = get(p, "crafting_categories") or get(p, "resource_categories") or {}
        ok_cat = cats[opts.category] and true or false
      end
      if ok_cat and (not opts.where or opts.where(p)) then
        local value = metric(p)
        if type(value) == "number" then
          local can = craftable(force, item)
          local have = (player and player.valid) and player.get_item_count(item) or 0
          if opts.all or can or have > 0 then
            list[#list + 1] = { name = name, item = item, value = value, craftable = can, have = have }
          end
        end
      end
    end
  end
  table.sort(list, function(a, b)
    if a.value ~= b.value then return a.value > b.value end
    return a.name < b.name
  end)
  local b = list[1]
  if not b then return { candidates = list } end
  return { name = b.name, item = b.item, value = b.value, craftable = b.craftable, have = b.have, candidates = list }
end

M.circuits = require("scripts.circuits").read -- circuit snapshot, docs/circuits-format.md
return M
