local s = game.surfaces["nauvis"]
local stname = {}
for k, v in pairs(defines.entity_status) do stname[v] = k end
local protos, ents = {}, {}
local belt_types = {["transport-belt"]=true, ["underground-belt"]=true, ["splitter"]=true, ["loader"]=true, ["loader-1x1"]=true}
local machine_types = {["assembling-machine"]=true, ["furnace"]=true, ["rocket-silo"]=true}
for _, e in pairs(s.find_entities_filtered{force = "player"}) do
  local t = e.type
  if t ~= "character" then
    local n = e.name
    if not protos[n] then
      local p = e.prototype
      protos[n] = {type = t, w = p.tile_width, h = p.tile_height}
      if belt_types[t] and p.belt_speed then protos[n].belt_speed = p.belt_speed end
      if machine_types[t] then local ok, v = pcall(function() return p.get_crafting_speed() end) if ok then protos[n].crafting_speed = v end end
    end
    local r = {n, e.position.x, e.position.y, e.direction or 0}
    local x = {}
    if machine_types[t] then
      local rc = e.get_recipe and e.get_recipe()
      if t == "furnace" and not rc then rc = e.previous_recipe and e.previous_recipe.name end
      x.recipe = rc and (type(rc) == "string" and rc or rc.name) or nil
      x.status = stname[e.status]
      x.speed = e.crafting_speed
    elseif t == "inserter" then
      x.pick = {e.pickup_position.x, e.pickup_position.y}
      x.drop = {e.drop_position.x, e.drop_position.y}
      x.status = stname[e.status]
    elseif belt_types[t] then
      local items = {}
      for i = 1, e.get_max_transport_line_index() do
        local line = e.get_transport_line(i)
        for _, c in pairs(line.get_contents()) do items[c.name] = (items[c.name] or 0) + c.count end
      end
      if next(items) then x.items = items end
      if t == "underground-belt" then x.io = e.belt_to_ground_type end
    elseif t == "train-stop" then
      x.stop = e.backer_name
      x.limit = e.trains_limit
    elseif t == "container" or t == "logistic-container" then
      local inv = e.get_inventory(defines.inventory.chest)
      if inv then
        local items = {}
        for _, c in pairs(inv.get_contents()) do items[c.name] = (items[c.name] or 0) + c.count end
        if next(items) then x.items = items end
      end
    end
    if next(x) then r[5] = x end
    ents[#ents + 1] = r
  end
end
local stops = {}
for _, st in pairs(s.find_entities_filtered{type = "train-stop"}) do
  stops[#stops + 1] = {name = st.backer_name, x = st.position.x, y = st.position.y, limit = st.trains_limit,
                       incoming = #st.get_train_stop_trains()}
end
local trains = {}
for _, tr in pairs(game.train_manager.get_trains{surface = s}) do
  local recs = {}
  local sch = tr.schedule
  if sch then
    for i, rec in ipairs(sch.records) do
      local conds = {}
      for _, c in ipairs(rec.wait_conditions or {}) do conds[#conds + 1] = c.type end
      recs[#recs + 1] = {station = rec.station, wait = conds}
    end
  end
  local items = {}
  for _, c in pairs(tr.get_contents()) do items[c.name] = (items[c.name] or 0) + c.count end
  trains[#trains + 1] = {id = tr.id, state = tr.state, cargo = items, fluids = tr.get_fluid_contents(),
                         current = sch and sch.current, records = recs,
                         wagons = #tr.cargo_wagons, fluid_wagons = #tr.fluid_wagons,
                         at = tr.front_stock and {tr.front_stock.position.x, tr.front_stock.position.y}}
end
helpers.write_file("atlas/nauvis.json", helpers.table_to_json({protos = protos, entities = ents, stops = stops, trains = trains, tick = game.tick}))
rcon.print("ok " .. #ents .. " entities, " .. #stops .. " stops, " .. #trains .. " trains")
