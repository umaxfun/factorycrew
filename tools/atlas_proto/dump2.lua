local s = game.surfaces["nauvis"]
local function boxes(e)
  local out = {}
  local fb = e.fluidbox
  if not fb then return out end
  for i = 1, #fb do
    local ok, seg = pcall(function() return fb.get_fluid_segment_id(i) end)
    local okp, pr = pcall(function() return fb.get_prototype(i) end)
    if okp and type(pr) == "table" and pr[1] then pr = pr[1] end
    local ptype = okp and pr and pr.production_type or nil
    local f = fb[i]
    local fname = f and f.name or nil
    if not fname then
      local okl, lk = pcall(function() return fb.get_locked_fluid(i) end)
      if okl then fname = lk end
    end
    local segs = {}
    if ok and seg then segs[1] = seg end
    local okc, cons = pcall(function() return fb.get_pipe_connections(i) end)
    if okc and cons then
      for _, c in pairs(cons) do
        if c.target then
          local okt, ts = pcall(function() return c.target.get_fluid_segment_id(c.target_fluidbox_index) end)
          if okt and ts then segs[#segs + 1] = ts end
        end
      end
    end
    out[#out + 1] = {segs, ptype or "-", fname or "-"}
  end
  return out
end
local machines = {}
for _, e in pairs(s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace", "mining-drill"}}) do
  local b = boxes(e)
  if #b > 0 then machines[#machines + 1] = {e.position.x, e.position.y, b} end
end
local pumps = {}
for _, e in pairs(s.find_entities_filtered{force = "player", type = {"pump", "offshore-pump"}}) do
  local fb = e.fluidbox
  local segs, targets = {}, {}
  for i = 1, #fb do
    local ok, seg = pcall(function() return fb.get_fluid_segment_id(i) end)
    segs[#segs + 1] = ok and seg or -1
    local okc, cons = pcall(function() return fb.get_pipe_connections(i) end)
    if okc and cons then
      for _, c in pairs(cons) do
        local tseg = nil
        if c.target then
          local okt, ts = pcall(function() return c.target.get_fluid_segment_id(c.target_fluidbox_index) end)
          if okt then tseg = ts end
        end
        targets[#targets + 1] = {tseg or -1, c.flow_direction}
      end
    end
  end
  pumps[#pumps + 1] = {e.name, e.type, e.position.x, e.position.y, segs, targets, fb[1] and fb[1].name}
end
local logi = {}
for _, e in pairs(s.find_entities_filtered{force = "player", type = "logistic-container"}) do
  local n = e.logistic_network
  logi[#logi + 1] = {e.position.x, e.position.y, n and n.network_id or -1}
end
helpers.write_file("atlas/fluids.json", helpers.table_to_json({machines = machines, pumps = pumps, logi = logi}))
rcon.print("ok " .. #machines .. " machines with fluidboxes, " .. #pumps .. " pumps, " .. #logi .. " logistic chests")
