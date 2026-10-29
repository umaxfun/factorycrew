local k = 0
local s = game.surfaces["nauvis"]
local out = {}
local function phase(name, f)
  local p = game.create_profiler()
  local r = f()
  p.stop()
  out[#out + 1] = {"", name, ": ", p}
  return r
end
local all = phase("find all", function() return s.find_entities_filtered{force = "player"} end)
local n = #all
phase("read name/pos/dir", function()
  local t = {}
  for i = 1, n do local e = all[i] t[i] = {e.name, e.position.x, e.position.y, e.direction} end
  return t
end)
local machines = phase("find machines", function() return s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace"}} end)
phase("machine recipe+status", function()
  local c = 0
  for _, m in pairs(machines) do local r = m.get_recipe() local st = m.status c = c + 1 end
  return c
end)
local belts = phase("find belts", function() return s.find_entities_filtered{force = "player", type = {"transport-belt", "underground-belt", "splitter"}} end)
phase("belt contents", function() k = 0
  for _, b in pairs(belts) do
    for i = 1, b.get_max_transport_line_index() do local c = b.get_transport_line(i).get_contents() k = k + #c end
  end
end)
local ins = phase("find inserters", function() return s.find_entities_filtered{force = "player", type = "inserter"} end)
phase("inserter pick/drop", function()
  for _, e in pairs(ins) do local a = e.pickup_position local b = e.drop_position end
end)
phase("machine fluid segments", function() k = 0
  for _, m in pairs(machines) do
    local fb = m.fluidbox
    for i = 1, #fb do
      for _, c in pairs(fb.get_pipe_connections(i)) do
        if c.target then local sid = c.target.get_fluid_segment_id(c.target_fluidbox_index) k = k + (sid or 0) end
      end
    end
  end
end)
local static = phase("static record build", function()
  local t = {}
  for i = 1, n do local e = all[i] t[i] = {e.name, e.position.x, e.position.y, e.direction} end
  return t
end)
local json = phase("table_to_json", function() return helpers.table_to_json(static) end)
phase("write_file", function() helpers.write_file("atlas/bench.json", json) end)
local chunk = phase("one chunk 32x32 find", function() return s.find_entities_filtered{force = "player", area = {{-384, -128}, {-352, -96}}} end)
out[#out + 1] = {"", "entities ", n, ", machines ", #machines, ", belts ", #belts, ", inserters ", #ins, ", json bytes ", #json, ", chunk entities ", #chunk}
for _, l in ipairs(out) do rcon.print(l) end
