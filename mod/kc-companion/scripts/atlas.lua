-- Structure dump of a surface for the atlas (docs/atlas.md), spread over ticks.
--
-- The bridge asks with {type="atlas", id, surface, budget}. The dump then runs a few chunks per
-- tick (at most `budget` entities, default BUDGET) and sends each tick's lines as
-- {type="atlas_part", id, seq, text}; the last message is the usual {type="result", id, ok, data}
-- with the part count, so the bridge can tell a lost packet from a finished dump.
--
-- Only structure: what stands where and how it is set up. State (statuses, contents, train
-- positions) changes every tick and is read live by queries, never dumped.
--
-- Text, one record per line, fields joined by "|" (helpers.table_to_json of a whole base costs
-- seconds; plain concatenation costs a tenth of that):
--   H|1|<surface>|<tick>|<budget>
--   N|<id>|<name>|<type>|<tile width>|<tile height>[|k=v...]   first time a name is seen
--        bs=<belt_speed tiles/tick>  ug=<max underground distance>  lm=<logistic mode>
--   E|<name id>|<2x>|<2y>|<direction>[|k=v...]
--        r=<recipe> sp=<crafting speed incl. modules and beacons>     crafting machines
--        t=<mined resource>                                         drills
--        p=<2x>,<2y> d=<2x>,<2y>                                    inserter pickup / drop
--        io=input|output                                            undergrounds, loaders
--        s=<stop name> lim=<trains limit>                            train stops
--        L=<logistic network id>                                    logistic containers
--        f=<seg>+<seg>:<production type>:<fluid>;...                fluidboxes, by pipe connections
--        pi=<seg>+... po=<seg>+... fl=<fluid>                       pumps (input / output side)
--   R|<recipe>|<energy>|<ingredients>|<products>    name:amount:type;...   recipes in use
--   T|<train id>|<cargo wagons>|<fluid wagons>|<station>;<station>...      train schedules
--   Z|<entities>|<chunks>|<ticks>
-- Coordinates are doubled so half tiles stay integers. Names are sanitised of "|" and newlines.

local M = {}

M.BUDGET = 150      -- entities per tick, also inside one busy chunk
M.MAX_CHUNKS = 3    -- chunks looked at per tick. Looking is the cost, empty or not: on the real client with a
                    -- 7888-chunk base, 24 per tick gave 34-53 UPS, 2 per tick a flat 60, while the entity
                    -- budget (10..200) and the send size (1000..4000) barely mattered
M.SEND_BYTES = 4000  -- text sent per tick (~240 KB/s at 60 UPS); entities wait while more is queued.
                    -- 2.2 MB in 269 ticks (~500 KB/s) once left the real client answering nothing

local SKIP = { character = true, ["entity-ghost"] = true, ["tile-ghost"] = true, ["cargo-wagon"] = true,
               ["fluid-wagon"] = true, locomotive = true, ["artillery-wagon"] = true, car = true,
               ["spider-vehicle"] = true, ["construction-robot"] = true, ["logistic-robot"] = true,
               ["combat-robot"] = true, ["item-entity"] = true, ["item-request-proxy"] = true,
               ["deconstructible-tile-proxy"] = true, projectile = true, ["flying-text"] = true }
local CRAFTER = { ["assembling-machine"] = true, furnace = true, ["rocket-silo"] = true }
local FLUID_MACHINE = { ["assembling-machine"] = true, furnace = true, ["rocket-silo"] = true,
                        ["mining-drill"] = true }

local fmt, floor, concat = string.format, math.floor, table.concat
local job = nil -- module state: one dump at a time; lost on save/load (the bridge times out)

local function clean(s)
  return (tostring(s or ""):gsub("[|\r\n]", " "))
end

local function segs_of(fb, i)
  local out = {}
  local ok, own = pcall(fb.get_fluid_segment_id, i)
  if ok and own then out[#out + 1] = own end
  local okc, cons = pcall(fb.get_pipe_connections, i)
  if okc and cons then
    for _, c in pairs(cons) do
      if c.target then
        local okt, ts = pcall(c.target.get_fluid_segment_id, c.target_fluidbox_index)
        if okt and ts then out[#out + 1] = ts end
      end
    end
  end
  return out
end

local function fluid_name(fb, i)
  local f = fb[i]
  if f and f.name then return f.name end
  local ok, lk = pcall(fb.get_locked_fluid, i)
  if ok and lk then return lk end
  local okf, flt = pcall(fb.get_filter, i)
  if okf and flt and flt.name then return flt.name end
  return "-"
end

local function ptype_of(fb, i)
  local ok, pr = pcall(fb.get_prototype, i)
  if not ok or not pr then return "-" end
  if type(pr) == "table" and pr[1] then pr = pr[1] end
  local okp, t = pcall(function() return pr.production_type end)
  return okp and t or "-"
end

local function fluid_fields(e, parts)
  local okf, fb = pcall(function() return e.fluidbox end) -- 2.1 raises on entities without one
  if not okf or not fb or #fb == 0 then return end
  local boxes = {}
  for i = 1, #fb do
    local segs = segs_of(fb, i)
    local s = {}
    for k, v in ipairs(segs) do s[k] = fmt("%d", v) end
    boxes[#boxes + 1] = concat(s, "+") .. ":" .. ptype_of(fb, i) .. ":" .. fluid_name(fb, i)
  end
  parts[#parts + 1] = "f=" .. concat(boxes, ";")
end

local function pump_fields(e, parts)
  local okf, fb = pcall(function() return e.fluidbox end)
  if not okf or not fb then return end
  local pin, pout = {}, {}
  for i = 1, #fb do
    local okc, cons = pcall(fb.get_pipe_connections, i)
    if okc and cons then
      for _, c in pairs(cons) do
        local seg = -1
        if c.target then
          local okt, ts = pcall(c.target.get_fluid_segment_id, c.target_fluidbox_index)
          if okt and ts then seg = ts end
        end
        local ss = fmt("%d", seg)
        if c.flow_direction == "input" then pin[#pin + 1] = ss
        elseif c.flow_direction == "output" then pout[#pout + 1] = ss
        else pin[#pin + 1] = ss; pout[#pout + 1] = ss end
      end
    end
  end
  parts[#parts + 1] = "pi=" .. concat(pin, "+")
  parts[#parts + 1] = "po=" .. concat(pout, "+")
  parts[#parts + 1] = "fl=" .. fluid_name(fb, 1)
end

local function name_line(j, e, t)
  local p = e.prototype
  j.nid = j.nid + 1
  local id = j.nid
  j.names[e.name] = id
  local parts = { "N", id, clean(e.name), t, p.tile_width or 1, p.tile_height or 1 }
  local ok, bs = pcall(function() return p.belt_speed end)
  if ok and bs then parts[#parts + 1] = "bs=" .. bs end
  if t == "underground-belt" then
    local okd, ug = pcall(function() return p.max_underground_distance end)
    if okd and ug then parts[#parts + 1] = "ug=" .. ug end
  end
  if t == "logistic-container" then
    local okm, lm = pcall(function() return p.logistic_mode end)
    if okm and lm then parts[#parts + 1] = "lm=" .. lm end
  end
  j.out[#j.out + 1] = concat(parts, "|")
  return id
end

local function entity_line(j, e)
  local t = e.type
  if SKIP[t] then return false end
  local id = j.names[e.name] or name_line(j, e, t)
  local pos = e.position
  -- numbers through string.format only: Factorio's Lua turns a number into a string ~6 us slower
  -- with .. or table.concat, which made the first dump 13 times slower than this
  local parts = { fmt("E|%d|%d|%d|%d", id, floor(pos.x * 2 + 0.5), floor(pos.y * 2 + 0.5), e.direction or 0) }
  if CRAFTER[t] then
    local r = e.get_recipe()
    local rn = r and r.name
    if not rn and t == "furnace" and e.previous_recipe then
      rn = e.previous_recipe.name and e.previous_recipe.name.name or e.previous_recipe.name
    end
    if rn then
      parts[#parts + 1] = "r=" .. rn
      j.recipes[rn] = true
    end
    parts[#parts + 1] = "sp=" .. fmt("%.4g", e.crafting_speed)
  elseif t == "mining-drill" then
    local mt = e.mining_target
    if mt then parts[#parts + 1] = "t=" .. mt.name end
  elseif t == "inserter" then
    local a, b = e.pickup_position, e.drop_position
    parts[#parts + 1] = fmt("p=%d,%d", floor(a.x * 2 + 0.5), floor(a.y * 2 + 0.5))
    parts[#parts + 1] = fmt("d=%d,%d", floor(b.x * 2 + 0.5), floor(b.y * 2 + 0.5))
  elseif t == "underground-belt" then
    parts[#parts + 1] = "io=" .. e.belt_to_ground_type
  elseif t == "loader" or t == "loader-1x1" then
    parts[#parts + 1] = "io=" .. e.loader_type
  elseif t == "train-stop" then
    parts[#parts + 1] = e.trains_limit and fmt("lim=%d", e.trains_limit) or "lim="
    parts[#parts + 1] = "s=" .. clean(e.backer_name)
  elseif t == "logistic-container" then
    local n = e.logistic_network
    parts[#parts + 1] = fmt("L=%d", n and n.network_id or -1)
  elseif t == "pump" or t == "offshore-pump" then
    pump_fields(e, parts)
  end
  if FLUID_MACHINE[t] then
    local pf = M.prof
    if pf then pf.fluid.restart() end
    fluid_fields(e, parts)
    if pf then pf.fluid.stop() end
  end
  j.out[#j.out + 1] = concat(parts, "|")
  return true
end

local function amounts(list)
  local out = {}
  for _, x in pairs(list or {}) do
    local a = x.amount or (((x.amount_min or 0) + (x.amount_max or 0)) / 2)
    a = a * (x.probability or 1)
    out[#out + 1] = fmt("%s:%.6g:%s", x.name, a, x.type)
  end
  return concat(out, ";")
end

local function tail_lines(j)
  local recipes = {}
  for rn in pairs(j.recipes) do recipes[#recipes + 1] = rn end
  table.sort(recipes)
  for _, rn in ipairs(recipes) do
    local r = prototypes.recipe[rn]
    if r then
      j.out[#j.out + 1] = concat({ "R", rn, fmt("%.6g", r.energy), amounts(r.ingredients), amounts(r.products) }, "|")
    end
  end
  for _, tr in pairs(game.train_manager.get_trains{ surface = j.surface }) do
    local st = {}
    local sch = tr.schedule
    if sch and sch.records then
      for _, rec in ipairs(sch.records) do st[#st + 1] = clean(rec.station or "") end
    end
    j.out[#j.out + 1] = concat({ "T", tr.id, #tr.cargo_wagons, #tr.fluid_wagons, concat(st, ";") }, "|")
  end
  j.out[#j.out + 1] = concat({ "Z", j.count, #j.chunks, j.ticks }, "|")
end

--- Start a dump; returns an error string when one is already running or the surface is unknown.
function M.start(msg)
  if job then return "busy" end
  local surface = game.surfaces[msg.surface or ""] or (game.connected_players[1] and game.connected_players[1].surface)
    or game.surfaces[1]
  if not surface then return "no surface" end
  job = { id = msg.id, surface = surface, budget = tonumber(msg.budget) or M.BUDGET, chunks = nil, next = 1,
          names = {}, nid = 0, recipes = {}, out = {}, seq = 0, count = 0, ticks = 0,
          queue = {}, qhead = 1, qtail = 0, queued = 0, send_bytes = tonumber(msg.send_bytes),
          max_chunks = tonumber(msg.max_chunks) }
  job.out[1] = concat({ "H", 1, clean(surface.name), game.tick, job.budget }, "|")
  return nil
end

function M.running()
  return job ~= nil
end

local step

--- One tick's share of the work. Returns the messages to send (parts, and the final result).
--- Never raises: an error in on_tick would end the player's game, so a failing dump is dropped and
--- reported to the bridge instead.
function M.step()
  local j = job
  local ok, res = pcall(step)
  if ok then return res end
  job = nil
  return { { type = "result", id = j and j.id, ok = false, error = "atlas_failed", message = tostring(res) } }
end

step = function()
  local j = job
  if not j then return {} end
  if not j.surface.valid then
    job = nil
    return { { type = "result", id = j.id, ok = false, error = "surface_gone" } }
  end
  j.ticks = j.ticks + 1
  if not j.chunks then -- first tick: only the chunk list, so the heavy part starts on a fresh tick
    j.chunks = {}
    for c in j.surface.get_chunks() do j.chunks[#j.chunks + 1] = { c.x, c.y } end
  else
    local done, looked = 0, 0
    local s = j.surface
    local backlog = j.queued > 4 * (j.send_bytes or M.SEND_BYTES)  -- sending is behind: let it catch up
    local pf = M.prof
    while not backlog and done < j.budget do
      if not j.cur then -- the next chunk with entities of ours
        if j.next > #j.chunks or looked >= (j.max_chunks or M.MAX_CHUNKS) then break end
        local c = j.chunks[j.next]
        j.next = j.next + 1
        looked = looked + 1
        local x0, y0 = c[1] * 32, c[2] * 32
        local area = { { x0, y0 }, { x0 + 32, y0 + 32 } }
        if pf then pf.find.restart() end
        local found = s.count_entities_filtered{ area = area, force = "player", limit = 1 } > 0
          and s.find_entities_filtered{ area = area, force = "player" } or nil
        if pf then pf.find.stop() end
        if found then j.cur = { list = found, i = 1, x0 = x0, y0 = y0 } end
      else
        -- a busy chunk (up to ~900 entities on a real base) is spread over ticks as well:
        -- one whole chunk in a tick made 42-47 UPS dips where the base is dense
        local cur = j.cur
        local list, x0, y0 = cur.list, cur.x0, cur.y0
        if pf then pf.line.restart() end
        while cur.i <= #list and done < j.budget do
          local e = list[cur.i]
          cur.i = cur.i + 1
          if e.valid then
            local p = e.position
            -- an entity crossing a chunk border is found from both chunks: keep it in its own
            if p.x >= x0 and p.x < x0 + 32 and p.y >= y0 and p.y < y0 + 32 and entity_line(j, e) then
              j.count = j.count + 1
            end
          end
          done = done + 1
        end
        if pf then pf.line.stop() end
        if cur.i > #list then j.cur = nil end
      end
    end
  end
  local scanned = j.chunks and j.next > #j.chunks and not j.cur
  if scanned and not j.tail then
    tail_lines(j)
    j.tail = true
  end
  -- queue this tick's lines in pieces of at most SEND_BYTES, then send at most that much
  local limit = j.send_bytes or M.SEND_BYTES
  local piece, size = {}, 0
  local function flush()
    if #piece > 0 then
      j.qtail = j.qtail + 1  -- an explicit tail: # on a table with nils in front is undefined
      j.queue[j.qtail] = concat(piece, "\n")
      j.queued = j.queued + size
      piece, size = {}, 0
    end
  end
  for _, line in ipairs(j.out) do
    if size > 0 and size + #line + 1 > limit then flush() end
    piece[#piece + 1] = line
    size = size + #line + 1
  end
  flush()
  j.out = {}
  local msgs, sent = {}, 0
  while j.qhead <= j.qtail and (sent == 0 or sent + #j.queue[j.qhead] <= limit) do
    local text = j.queue[j.qhead]
    j.queue[j.qhead] = nil
    j.qhead = j.qhead + 1
    j.queued = j.queued - #text
    sent = sent + #text
    j.seq = j.seq + 1
    msgs[#msgs + 1] = { type = "atlas_part", id = j.id, seq = j.seq, text = text }
  end
  local finished = scanned and j.qhead > j.qtail
  if finished then
    msgs[#msgs + 1] = { type = "result", id = j.id, ok = true,
                        data = { parts = j.seq, entities = j.count, chunks = #j.chunks, ticks = j.ticks,
                                 budget = j.budget, surface = j.surface.name } }
    job = nil
  end
  return msgs
end

--- Whole dump in one call, for tests and the fixture script: returns the text and the counts.
--- `profile_file`: also write where the time went (find / entity lines / fluid part / total) there.
function M.dump_now(surface_name, budget, profile_file)
  if job then return nil, "busy" end
  local function prof()
    local p = game.create_profiler(true)
    return p
  end
  if profile_file then M.prof = { find = prof(), line = prof(), fluid = prof(), total = game.create_profiler() } end
  M.start({ id = "now", surface = surface_name, budget = budget, send_bytes = math.huge })
  local texts, data = {}, nil
  while job do
    for _, m in ipairs(M.step()) do
      if m.type == "atlas_part" then texts[#texts + 1] = m.text else data = m.data end
    end
  end
  local pf = M.prof
  if pf then
    pf.total.stop()
    M.prof = nil
    helpers.write_file(profile_file, { "", "total ", pf.total, "\nfind ", pf.find, "\nentity lines (incl. fluid) ",
                                       pf.line, "\nfluid part ", pf.fluid, "\n" })
  end
  return concat(texts, "\n"), data
end

return M
