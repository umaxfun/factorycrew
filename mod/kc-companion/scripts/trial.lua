-- kc.trial: build a plan for real on a hidden copy of its area and let the game run it, so the
-- agent sees whether the design works (items move, machines run, power arrives) before it tells
-- the player "done". The player's surface is never touched: the copy lives on its own surface,
-- hidden from every force, and is deleted by kc.trial_report{finish = true} or the next trial.
local M = {}

local PREFIX = "kc-trial-"
local STAND_IN = "electric-energy-interface"

local STATUS = {}
for k, v in pairs(defines.entity_status) do STATUS[v] = k end

-- entities that have no status worth reporting
local QUIET = {
  ["electric-pole"] = true, wall = true, gate = true, ["straight-rail"] = true, ["curved-rail-a"] = true,
  ["curved-rail-b"] = true, ["half-diagonal-rail"] = true, ["legacy-straight-rail"] = true,
  ["legacy-curved-rail"] = true, ["rail-signal"] = true, ["rail-chain-signal"] = true, pipe = true,
  ["pipe-to-ground"] = true, ["transport-belt"] = true, ["underground-belt"] = true, splitter = true,
  tree = true, ["simple-entity"] = true, resource = true, cliff = true, ["item-entity"] = true,
  character = true, ["electric-energy-interface"] = true, ["tile-ghost"] = true,
}

local function box(area)
  local a, b = area[1] or area.left_top, area[2] or area.right_bottom
  local x0, y0 = a.x or a[1], a.y or a[2]
  local x1, y1 = b.x or b[1], b.y or b[2]
  return { { math.floor(x0), math.floor(y0) }, { math.ceil(x1), math.ceil(y1) } }
end

local function key(e) return e.name .. "@" .. e.position.x .. "," .. e.position.y end

local function contents(e)
  local out, any = {}, false
  local invs = {}
  if e.type == "container" or e.type == "logistic-container" or e.type == "infinity-container" then
    invs[1] = e.get_inventory(defines.inventory.chest)
  elseif e.get_output_inventory then
    local ok, inv = pcall(e.get_output_inventory)
    if ok and inv then invs[1] = inv end
  end
  for _, inv in ipairs(invs) do
    for _, it in pairs(inv.get_contents()) do
      local n = it.name or it[1]
      out[n] = (out[n] or 0) + (it.count or it[2] or 0)
      any = true
    end
  end
  return any and out or {}
end

local function snapshot(surface, b)
  local snap = {}
  for _, e in pairs(surface.find_entities_filtered{ area = b }) do
    if e.valid and e.unit_number then
      local s = { items = contents(e) }
      local ok, pf = pcall(function() return e.products_finished end)
      if ok and pf then s.finished = pf end
      snap[key(e)] = s
    end
  end
  return snap
end

local function belt_items(surface, b)
  local n = 0
  for _, e in pairs(surface.find_entities_filtered{ area = b, type = { "transport-belt", "underground-belt", "splitter" } }) do
    for i = 1, e.get_max_transport_line_index() do n = n + #e.get_transport_line(i) end
  end
  return n
end

local SOURCE_TYPES = { "generator", "burner-generator", "solar-panel", "accumulator", "electric-energy-interface",
                       "fusion-generator", "lightning-attractor" }

--- The electric network `pole` belongs to: how many power sources it has (generators, solar,
--- accumulators, ...; anywhere on the surface), what they produced and what consumers drew over
--- the last 5 seconds (watts). {sources = n, producing = bool, production_w, consumption_w}.
--- sources = 0: nothing powers that network, a design wired to it stays unpowered.
--- production 0 with sources > 0: idle (no load) or out of fuel; consumption tells which.
function M.grid_power(pole)
  local st = pole and pole.valid and pole.electric_network_statistics
  if not st then return { sources = 0, producing = false, production_w = 0, consumption_w = 0 } end
  local net, sources = pole.electric_network_id, 0
  for _, ty in ipairs(SOURCE_TYPES) do -- one type at a time: a type this game lacks is an error
    local ok, found = pcall(pole.surface.find_entities_filtered, { type = ty })
    if ok then
      for _, e in pairs(found) do
        if e.electric_network_id == net then sources = sources + 1 end
      end
    end
  end
  local function sum(category, counts)
    local t = 0
    for name in pairs(counts) do
      local ok, v = pcall(st.get_flow_count, { name = name, category = category,
                                               precision_index = defines.flow_precision_index.five_seconds })
      if ok and v then t = t + v end
    end
    return t * 60 -- per tick -> per second
  end
  -- electric statistics: "output" is what generators produced, "input" what consumers drew
  local prod, cons = sum("output", st.output_counts), sum("input", st.input_counts)
  return { sources = sources, producing = prod > 0, production_w = math.floor(prod), consumption_w = math.floor(cons) }
end

local function delete_old()
  for _, s in pairs(game.surfaces) do
    if s.name:sub(1, #PREFIX) == PREFIX then game.delete_surface(s) end
  end
end

--- Start a trial of `area` ({{x0, y0}, {x1, y1}}: the design plus whatever feeds it and whatever it
--- feeds): copy it to a hidden surface, revive every ghost there, and stand in for the player's
--- grid with an energy source wherever a real pole in the area belongs to a network that produces
--- power now. Returns {surface, revived, failed, power = {{network, production_w, stand_in}}, notes}.
--- Then let the game run (a few seconds) and call kc.trial_report().
function M.start(surface, area, opts)
  opts = opts or {}
  local b = box(area)
  delete_old()
  local name = PREFIX .. game.tick
  local t = game.create_surface(name)
  t.generate_with_lab_tiles = true
  t.peaceful_mode = true
  t.daytime = surface.daytime
  for _, f in pairs(game.forces) do pcall(f.set_surface_hidden, t, true) end
  local cx, cy = (b[1][1] + b[2][1]) / 2, (b[1][2] + b[2][2]) / 2
  local r = math.ceil(math.max(b[2][1] - b[1][1], b[2][2] - b[1][2]) / 64) + 2
  t.request_to_generate_chunks({ cx, cy }, r)
  t.force_generate_chunk_requests()

  surface.clone_area{
    source_area = b, destination_area = b, destination_surface = t,
    clone_tiles = true, clone_entities = true, clone_decoratives = false,
    clear_destination_entities = true, clear_destination_decoratives = true,
    expand_map = false, create_build_effect_smoke = false,
  }
  for _, c in pairs(t.find_entities_filtered{ type = "character" }) do c.destroy() end

  -- ghosts -> entities: poles first so everything else is powered from the first tick
  local ghosts = t.find_entities_filtered{ area = b, type = "entity-ghost" }
  table.sort(ghosts, function(x, y)
    return (x.ghost_type == "electric-pole" and 0 or 1) < (y.ghost_type == "electric-pole" and 0 or 1)
  end)
  local revived, failed = 0, {}
  for _, g in ipairs(ghosts) do
    if g.valid then
      local gname, pos = g.ghost_name, { x = g.position.x, y = g.position.y }
      local ok, _, e = pcall(g.revive, { raise_revive = false })
      if ok and e then revived = revived + 1 else failed[#failed + 1] = { name = gname, x = pos.x, y = pos.y } end
    end
  end
  for _, g in pairs(t.find_entities_filtered{ area = b, type = "tile-ghost" }) do pcall(g.revive) end

  -- the player's grid: one stand-in source per real network that produces power now
  local power, notes, seen = {}, {}, {}
  local have_stand_in = prototypes.entity[STAND_IN] ~= nil
  for _, p in pairs(surface.find_entities_filtered{ area = b, type = "electric-pole" }) do
    local net = p.electric_network_id
    if net and not seen[net] then
      seen[net] = true
      local g = M.grid_power(p)
      local rec = { network = net, sources = g.sources, production_w = g.production_w, consumption_w = g.consumption_w,
                    pole = { p.position.x, p.position.y } }
      if g.sources > 0 and have_stand_in then
        rec.stand_in = false
        local twin = t.find_entity(p.name, p.position)
        local spot = twin and t.find_non_colliding_position(STAND_IN, twin.position, 30, 0.5, true)
        local src = spot and t.create_entity{ name = STAND_IN, position = spot, force = p.force }
        -- the stand-in sits wherever it fits; a pole of the same kind beside it carries the power
        local fpos = src and t.find_non_colliding_position(p.name, spot, 10, 0.5, true)
        local feeder = fpos and t.create_entity{ name = p.name, position = fpos, force = p.force }
        if feeder then
          src.electric_buffer_size = 1e12
          src.power_production = math.max(g.production_w, 1e6) / 60
          src.energy = 1e12
          local a = feeder.get_wire_connector(defines.wire_connector_id.pole_copper, true)
          local b = twin.get_wire_connector(defines.wire_connector_id.pole_copper, true)
          -- a new pole may already have wired itself to the twin; connect_to is false then
          rec.stand_in = a.connect_to(b, false) or a.is_connected_to(b) or false
        else
          rec.why = not twin and "pole not found on the trial surface" or "no free spot for the stand-in"
        end
      end
      if g.sources == 0 then
        notes[#notes + 1] = "network " .. net .. " (pole at " .. p.position.x .. ", " .. p.position.y ..
          ") has no power source at all on the player's map: the design will be unpowered there too"
      elseif not g.producing and g.consumption_w == 0 then
        notes[#notes + 1] = "network " .. net .. " has sources but produced nothing in the last 5 s (idle or out of fuel)"
      end
      power[#power + 1] = rec
    end
  end
  if not have_stand_in then notes[#notes + 1] = "no " .. STAND_IN .. " prototype: the trial runs without the grid" end
  if next(seen) == nil then notes[#notes + 1] = "no real pole in the area: the trial has no grid power unless the area holds its own generators" end
  notes[#notes + 1] = "only what is inside the area exists in the trial: include the sources (chests, belts, drills) and the destinations"

  storage.kc_trial = { surface = name, source = surface.name, area = b, tick = game.tick, before = snapshot(t, b),
                       belts = belt_items(t, b) }
  return { surface = name, revived = revived, failed = failed, power = power, notes = notes }
end

--- What happened in the trial since kc.trial_start: {ticks, entities = {{name, x, y, status}} for
--- everything that is not working (capped), working = n, changes = {{name, x, y, items = {delta},
--- finished = crafts}}, belt_items = {before, now}}. opts.finish = true deletes the trial surface.
function M.report(opts)
  opts = opts or {}
  local tr = storage.kc_trial
  if not tr then return { error = "no trial running; call kc.trial_start(surface, area) first" } end
  local t = game.surfaces[tr.surface]
  if not t then storage.kc_trial = nil return { error = "the trial surface is gone; start again" } end
  local b = tr.area
  local out = { ticks = game.tick - tr.tick, working = 0, entities = {}, changes = {} }
  local cap = opts.limit or 40
  for _, e in pairs(t.find_entities_filtered{ area = b }) do
    if e.valid and not QUIET[e.type] then
      local ok, st = pcall(function() return e.status end)
      local word = ok and st and STATUS[st] or nil
      if word == "working" or word == "normal" then
        out.working = out.working + 1
      elseif word and #out.entities < cap then
        out.entities[#out.entities + 1] = { name = e.name, x = e.position.x, y = e.position.y, status = word }
      end
      local before = tr.before[key(e)]
      if before then
        local now = contents(e)
        local delta, any = {}, false
        for n, c in pairs(now) do if c ~= (before.items[n] or 0) then delta[n] = c - (before.items[n] or 0) any = true end end
        for n, c in pairs(before.items) do if now[n] == nil then delta[n] = -c any = true end end
        local okf, pf = pcall(function() return e.products_finished end)
        local fin = okf and pf and before.finished and pf - before.finished or 0
        if any or fin > 0 then
          out.changes[#out.changes + 1] = { name = e.name, x = e.position.x, y = e.position.y, items = delta,
                                            finished = fin > 0 and fin or nil }
        end
      end
    end
  end
  out.belt_items = { before = tr.belts, now = belt_items(t, b) }
  if opts.finish then
    game.delete_surface(t)
    storage.kc_trial = nil
    out.finished = true
  end
  return out
end

return M
