-- cost of the atlas dump by part, on the loaded save (run over RCON with scripts/atlas-fixture.sh lua)
local atlas = nil
local s = game.surfaces.nauvis
local p_all = game.create_profiler()
local text, data = remote.call("kc", "atlas_dump", "nauvis", 100000, "atlas/bench-dump.txt")
p_all.stop()
rcon.print({"", "whole dump in one call (incl. write): ", p_all})
rcon.print(helpers.table_to_json(text))
-- the same entities without the per-type extras: what the bare loop costs
local p = game.create_profiler()
local n = 0
for c in s.get_chunks() do
  local x0, y0 = c.x * 32, c.y * 32
  local area = {{x0, y0}, {x0 + 32, y0 + 32}}
  if s.count_entities_filtered{area = area, force = "player", limit = 1} > 0 then
    for _, e in pairs(s.find_entities_filtered{area = area, force = "player"}) do
      local pos = e.position
      n = n + 1
    end
  end
end
p.stop()
rcon.print({"", "bare chunk loop (find + position): ", p, " for " .. n})
local p2 = game.create_profiler()
local k = 0
for _, m in pairs(s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace", "mining-drill"}}) do
  local fb = m.fluidbox
  for i = 1, #fb do
    local cons = fb.get_pipe_connections(i)
    k = k + #cons
  end
end
p2.stop()
rcon.print({"", "fluid connections of all machines: ", p2})
local p3 = game.create_profiler()
local j = 0
for _, m in pairs(s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace"}}) do
  local fb = m.fluidbox
  for i = 1, #fb do
    local pr = fb.get_prototype(i)
    j = j + 1
  end
end
p3.stop()
rcon.print({"", "fluidbox prototypes of all machines: ", p3})
