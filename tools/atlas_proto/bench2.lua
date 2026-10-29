local s = game.surfaces["nauvis"]
local all = s.find_entities_filtered{force = "player"}
local n = #all
local p = game.create_profiler()
local parts, names, nid = {}, {}, 0
local fmt, floor = string.format, math.floor
for i = 1, n do
  local e = all[i]
  local nm = e.name
  local id = names[nm]
  if not id then nid = nid + 1 id = nid names[nm] = id end
  local pos = e.position
  parts[i] = fmt("%d,%d,%d,%d", id, floor(pos.x * 2), floor(pos.y * 2), e.direction or 0)
end
local body = table.concat(parts, "\n")
p.stop()
rcon.print({"", "manual csv build: ", p})
local p2 = game.create_profiler()
helpers.write_file("atlas/bench.csv", body)
p2.stop()
rcon.print({"", "write: ", p2, " bytes ", #body})
-- the same in slices of 10k entities, as one tick each would do
local worst = 0
local p3 = game.create_profiler()
for a = 1, n, 10000 do
  local part = {}
  for i = a, math.min(n, a + 9999) do
    local e = all[i]
    local pos = e.position
    part[#part + 1] = fmt("%d,%d,%d,%d", names[e.name], floor(pos.x * 2), floor(pos.y * 2), e.direction or 0)
  end
  local x = table.concat(part, "\n")
end
p3.stop()
rcon.print({"", "13 slices of 10k: ", p3})
local p4 = game.create_profiler()
local ms = s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace"}}
local st = {}
for i, m in ipairs(ms) do st[i] = m.status end
p4.stop()
rcon.print({"", "all machine statuses (live state of the whole base): ", p4})
