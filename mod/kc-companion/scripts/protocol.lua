-- Wire protocol between the mod and the bridge (SPEC.md, "Протокол мод ↔ мост").
-- Pure functions over storage/game so FactorioTest can exercise them without UDP.

local M = {}

M.VERSION = 1
M.BRIDGE_PORT = 34199
M.MAX_RESULT = 64 * 1024 -- query result cap, bytes of JSON
M.CHUNK_RAW = 4096 -- raw slice per part; JSON escaping can roughly double it, staying near 8 KB
M.HEARTBEAT_TICKS = 300 -- 5 s
M.PONG_TIMEOUT_TICKS = 900 -- 15 s without pong => "no connection"

-- ── storage ──────────────────────────────────────────────────────────────

function M.init_storage()
  storage.kc = storage.kc or {}
  local kc = storage.kc
  if not kc.run_id then
    -- The Lua API exposes no save-file name, so a run is identified by an id minted
    -- the first time the mod sees this map (new game, or mod added to an old save).
    local seed = game.surfaces[1] and game.surfaces[1].map_gen_settings.seed or 0
    kc.run_id = string.format("%08x-%x", seed, game.tick)
  end
  kc.run_name = kc.run_name or "Ран 1"
  kc.marks = kc.marks or {}
  kc.last_pong_tick = kc.last_pong_tick or nil
end

-- ── query execution ──────────────────────────────────────────────────────

local function query_env(surface_name)
  local player = game.players[1] -- v1 is single-player
  local surface
  if surface_name then
    surface = game.surfaces[surface_name]
  elseif player and player.valid then
    surface = player.surface
  else
    surface = game.surfaces[1]
  end
  return {
    game = game, helpers = helpers, defines = defines, prototypes = prototypes,
    surface = surface, player = player,
    pairs = pairs, ipairs = ipairs, next = next, select = select, type = type,
    tostring = tostring, tonumber = tonumber, pcall = pcall, error = error,
    table = table, string = string, math = math, table_size = table_size,
    serpent = serpent, log = log,
  }
end

--- Run a Lua body against the save. Returns {ok=true, data=...} or {ok=false, error=code, message=...}.
function M.run_query(lua, surface_name)
  if type(lua) ~= "string" or lua == "" then
    return { ok = false, error = "bad_request", message = "lua must be a non-empty string" }
  end
  if surface_name and not game.surfaces[surface_name] then
    return { ok = false, error = "no_such_surface", message = tostring(surface_name) }
  end
  local fn, err = load(lua, "=kc-query", "t", query_env(surface_name))
  if not fn then
    return { ok = false, error = "compile_error", message = tostring(err) }
  end
  local ok, res = pcall(fn)
  if not ok then
    return { ok = false, error = "runtime_error", message = tostring(res) }
  end
  local enc_ok, json = pcall(helpers.table_to_json, { v = res })
  if not enc_ok or type(json) ~= "string" then
    return { ok = false, error = "not_serializable", message = "return plain tables, strings, numbers or booleans" }
  end
  if #json > M.MAX_RESULT then
    return { ok = false, error = "result_too_large", size = #json,
             message = "aggregate on the Lua side: count, group, return positions only" }
  end
  return { ok = true, data = res }
end

-- ── framing ──────────────────────────────────────────────────────────────

--- Encode one message into one or more JSON packets. Large messages become
--- {type="part", id, part, parts, data=<slice of the full JSON>}.
function M.encode(msg)
  msg.v = M.VERSION
  local s = helpers.table_to_json(msg)
  if #s <= M.CHUNK_RAW then
    return { s }
  end
  local parts = math.ceil(#s / M.CHUNK_RAW)
  local out = {}
  for i = 1, parts do
    local slice = string.sub(s, (i - 1) * M.CHUNK_RAW + 1, i * M.CHUNK_RAW)
    out[i] = helpers.table_to_json({ v = M.VERSION, type = "part", id = msg.id, part = i, parts = parts, data = slice })
  end
  return out
end

-- Partial messages are not persisted: losing an in-flight chunk on save/load is acceptable.
local pending = {}

--- Feed one packet; returns the decoded message when complete, else nil.
function M.decode(payload)
  local msg = helpers.json_to_table(payload)
  if type(msg) ~= "table" then return nil end
  if msg.type ~= "part" then return msg end
  local id, n = tostring(msg.id), tonumber(msg.parts)
  if not n or n < 1 or n > 1024 then return nil end
  local p = pending[id]
  if not p then
    p = { parts = n, got = 0, slices = {} }
    pending[id] = p
  end
  if not p.slices[msg.part] then
    p.slices[msg.part] = msg.data
    p.got = p.got + 1
  end
  if p.got < p.parts then return nil end
  pending[id] = nil
  return M.decode(table.concat(p.slices))
end

-- ── dispatch ─────────────────────────────────────────────────────────────

function M.heartbeat(kind)
  return {
    type = kind or "heartbeat",
    id = "hb-" .. game.tick,
    run_id = storage.kc.run_id,
    run_name = storage.kc.run_name,
    tick = game.tick,
    tabs = {},
  }
end

--- Handle one decoded message from the bridge; returns a list of reply messages.
function M.handle(msg)
  local t = msg.type
  if t == "query" then
    local r = M.run_query(msg.lua, msg.surface)
    r.type = "result"
    r.id = msg.id
    return { r }
  elseif t == "pong" then
    storage.kc.last_pong_tick = game.tick
    return {}
  end
  return {}
end

function M.connected()
  local last = storage.kc and storage.kc.last_pong_tick
  return last ~= nil and (game.tick - last) <= M.PONG_TIMEOUT_TICKS
end

return M
