-- Wire protocol between the mod and the bridge (SPEC.md, "Mod <-> bridge protocol").
-- Pure functions over storage/game so FactorioTest can exercise them without UDP.

local helpers_lib = require("scripts.helpers") -- `kc` in the query environment

local M = {}

M.VERSION = 1
M.BRIDGE_PORT = 34199
M.MAX_RESULT = 64 * 1024 -- query result cap, bytes of JSON
M.CHUNK_RAW = 4096 -- raw slice per part; JSON escaping can roughly double it, staying near 8 KB
M.HEARTBEAT_TICKS = 300 -- 5 s
M.PONG_TIMEOUT_TICKS = 900 -- 15 s without pong => "no connection"
M.ACK_TIMEOUT_TICKS = 180 -- 3 s without ack on an ask => "no connection" on that message
M.FEED_LIMIT = 30 -- messages kept per tab
M.TRACE_LIMIT = 12 -- live steps of a running turn shown under the question
M.MARK_PREFIX = "[KC] "

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
  kc.run_name = kc.run_name or "Run 1"
  kc.tabs = kc.tabs or {} -- chat -> {feed = {...}, state = "idle"|"thinking", note = nil}
  kc.tab_order = kc.tab_order or {}
  kc.marks = kc.marks or {} -- chat -> tag_number -> {tag = LuaCustomChartTag, surface, position, text}
  kc.mark_chat = kc.mark_chat or {} -- tag_number -> chat
  kc.seq = kc.seq or 0
  kc.pointers = kc.pointers or {} -- player_index -> {pointer, ...} (scripts/pointers.lua)
  kc.panels = kc.panels or {} -- name -> live panel (scripts/panels.lua)
  kc.panel_order = kc.panel_order or {}
end

local function next_id(prefix)
  storage.kc.seq = storage.kc.seq + 1
  return string.format("%s-%d-%d", prefix, game.tick, storage.kc.seq)
end

-- ── tabs ─────────────────────────────────────────────────────────────────

function M.tab(chat)
  local kc = storage.kc
  local t = kc.tabs[chat]
  if not t then
    t = { feed = {}, state = "idle" }
    kc.tabs[chat] = t
  end
  -- a closed tab keeps its feed; opening it again (or a reply arriving) brings it back
  local listed = false
  for _, c in ipairs(kc.tab_order) do
    if c == chat then listed = true break end
  end
  if not listed then kc.tab_order[#kc.tab_order + 1] = chat end
  return t
end

--- Hide a tab from the window. The chat in Kiro Crew stays; the same name reopens it.
function M.close_tab(chat)
  local order = storage.kc.tab_order
  for i, c in ipairs(order) do
    if c == chat then table.remove(order, i) return true end
  end
  return false
end

--- Live marks of one chat, in placement order.
function M.chat_marks(chat)
  local out = {}
  for n, m in pairs(storage.kc.marks[chat] or {}) do
    if m.tag.valid then out[#out + 1] = { id = n, surface = m.surface, position = m.position, text = m.text } end
  end
  table.sort(out, function(a, b) return a.id < b.id end)
  return out
end

-- "unknown" until the first recv_udp, then "on" or "off" (game started without --enable-lua-udp)
M.udp_state = "unknown"

local function push(tab, entry)
  entry.tick = game.tick
  tab.feed[#tab.feed + 1] = entry
  while #tab.feed > M.FEED_LIMIT do
    table.remove(tab.feed, 1)
  end
  return entry
end

--- Record a player question in its tab and return the `ask` message for the bridge.
--- Where the asking player stands, so "build here" / "near me" has a default place.
local function whereabouts(player)
  player = player or game.connected_players[1]
  if not (player and player.valid) then return nil end
  local p = player.position
  return {
    name = player.name, surface = player.surface.name,
    x = math.floor(p.x * 10 + 0.5) / 10, y = math.floor(p.y * 10 + 0.5) / 10,
  }
end

--- Record a player question in its tab and return the `ask` message for the bridge.
--- pointers: the player's @1, @2, ... (scripts/pointers.lua), omitted when empty.
function M.ask(chat, text, player, pointers)
  local id = next_id("ask")
  local entry = { from = "player", text = text, id = id, acked = false }
  if pointers and #pointers > 0 then entry.pointers = #pointers end
  push(M.tab(chat), entry)
  return {
    type = "ask", id = id, chat = chat, text = text,
    run_id = storage.kc.run_id, run_name = storage.kc.run_name, tick = game.tick,
    player = whereabouts(player),
    pointers = (pointers and #pointers > 0) and pointers or nil,
  }
end

--- Player messages whose ask was not acknowledged within ACK_TIMEOUT_TICKS.
function M.unacked(chat)
  local out = {}
  for _, e in ipairs(M.tab(chat).feed) do
    if e.from == "player" and not e.acked and game.tick - e.tick > M.ACK_TIMEOUT_TICKS then
      out[#out + 1] = e.id
    end
  end
  return out
end

-- ── the run's panel library (scripts/panels.lua keeps the answers) ──────────

--- A request to the run's saved-panel library: op = "list" | "open" | "delete" (with slug).
--- The dispatcher answers with a library_result; no model is involved.
function M.library_request(op, slug)
  local kc = storage.kc
  kc.library = kc.library or { panels = {}, pending = {} }
  kc.library.pending = kc.library.pending or {}
  local id = next_id("lib")
  kc.library.pending[id] = { op = op, tick = game.tick }
  kc.library.error = nil
  return { type = "library", id = id, op = op, slug = slug, run_id = kc.run_id, run_name = kc.run_name }
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
    serpent = serpent, log = log, remote = remote,
    kc = helpers_lib,
  }
end
M.query_env = query_env -- panels run their data source in the same environment

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

-- ── marks ────────────────────────────────────────────────────────────────

local function to_position(p)
  if type(p) ~= "table" then return nil end
  local x, y = p.x or p[1], p.y or p[2]
  if type(x) ~= "number" or type(y) ~= "number" then return nil end
  return { x = x, y = y }
end

local function forget_mark(tag_number)
  local kc = storage.kc
  local chat = kc.mark_chat[tag_number]
  if not chat then return nil end
  kc.mark_chat[tag_number] = nil
  local m = kc.marks[chat] and kc.marks[chat][tag_number]
  if kc.marks[chat] then
    kc.marks[chat][tag_number] = nil
    if next(kc.marks[chat]) == nil then kc.marks[chat] = nil end
  end
  return m
end

--- Place chart tags for a chat. Returns {ids = {...}, failed = {{index, reason}}}.
function M.mark(chat, marks)
  local kc = storage.kc
  local force = game.forces.player
  local ids, failed = {}, {}
  for i, spec in ipairs(marks or {}) do
    local surface = game.surfaces[spec.surface or 1]
    local pos = to_position(spec.position)
    if not surface then
      failed[#failed + 1] = { index = i, reason = "no_such_surface" }
    elseif not pos then
      failed[#failed + 1] = { index = i, reason = "bad_position" }
    else
      local text = M.MARK_PREFIX .. tostring(spec.text or "")
      local tagspec = { position = pos, text = text }
      if spec.icon then tagspec.icon = { type = "item", name = spec.icon } end
      local ok, tag = pcall(force.add_chart_tag, surface, tagspec)
      if not ok then
        failed[#failed + 1] = { index = i, reason = "error", message = tostring(tag) }
      elseif not tag then
        failed[#failed + 1] = { index = i, reason = "add_chart_tag_nil" }
      else
        local n = tag.tag_number
        kc.marks[chat] = kc.marks[chat] or {}
        kc.marks[chat][n] = { tag = tag, surface = surface.name, position = tag.position, text = text }
        kc.mark_chat[n] = chat
        ids[#ids + 1] = n
      end
    end
  end
  return { ids = ids, failed = failed }
end

--- Remove mod-owned marks: by ids within a chat, all of a chat, or all. Returns {removed = n}.
function M.unmark(chat, opts)
  local kc = storage.kc
  local targets = {}
  if opts.all then
    for n in pairs(kc.mark_chat) do targets[#targets + 1] = n end
  elseif opts.all_in_chat then
    for n in pairs(kc.marks[chat] or {}) do targets[#targets + 1] = n end
  else
    for _, n in ipairs(opts.ids or {}) do
      if kc.mark_chat[n] == chat then targets[#targets + 1] = n end
    end
  end
  local removed = 0
  for _, n in ipairs(targets) do
    local m = forget_mark(n)
    if m then
      if m.tag.valid then m.tag.destroy() end
      removed = removed + 1
    end
  end
  return { removed = removed }
end

--- Live marks, optionally of one chat. Drops entries whose tag is gone.
function M.list_marks(chat)
  local kc = storage.kc
  local out = {}
  for c, by_id in pairs(kc.marks) do
    if chat == nil or c == chat then
      for n, m in pairs(by_id) do
        if m.tag.valid then
          out[#out + 1] = { id = n, chat = c, surface = m.surface, position = m.position, text = m.text }
        end
      end
    end
  end
  -- forget tags that vanished without an event (removal while iterating `pairs` is not safe)
  local stale = {}
  for n, c in pairs(kc.mark_chat) do
    local m = kc.marks[c] and kc.marks[c][n]
    if not m or not m.tag.valid then stale[#stale + 1] = n end
  end
  for _, n in ipairs(stale) do forget_mark(n) end
  table.sort(out, function(a, b) return a.id < b.id end)
  return out
end

--- on_chart_tag_removed: the player deleted a tag on the map (or we did).
function M.on_tag_removed(tag)
  if tag and tag.valid then forget_mark(tag.tag_number) end
end

-- ── framing ──────────────────────────────────────────────────────────────

-- Slice end that does not split a UTF-8 sequence: back off while the next byte is a continuation byte.
local function utf8_safe_end(s, stop)
  if stop >= #s then return #s end
  local e = stop
  while e > 1 do
    local b = string.byte(s, e + 1)
    if b < 0x80 or b >= 0xC0 then break end
    e = e - 1
  end
  return e
end

--- Encode one message into one or more JSON packets. Large messages become
--- {type="part", id, part, parts, data=<slice of the full JSON>}.
function M.encode(msg)
  msg.v = M.VERSION
  local s = helpers.table_to_json(msg)
  if #s <= M.CHUNK_RAW then
    return { s }
  end
  local slices, start = {}, 1
  while start <= #s do
    local stop = utf8_safe_end(s, start + M.CHUNK_RAW - 1)
    slices[#slices + 1] = string.sub(s, start, stop)
    start = stop + 1
  end
  local out = {}
  for i, slice in ipairs(slices) do
    out[i] = helpers.table_to_json({ v = M.VERSION, type = "part", id = msg.id, part = i, parts = #slices, data = slice })
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
    tabs = storage.kc.tab_order,
  }
end

local function find_feed_entry(id)
  for _, t in pairs(storage.kc.tabs) do
    for _, e in ipairs(t.feed) do
      if e.id == id then return e end
    end
  end
  return nil
end

local function result(msg, data)
  return { type = "result", id = msg.id, ok = true, data = data }
end

--- Handle one decoded message from the bridge; returns a list of reply messages.
function M.handle(msg)
  local t = msg.type
  local kc = storage.kc
  -- reply/status addressed to another run (a queued answer for a save that is no longer loaded)
  if (t == "reply" or t == "status" or t == "trace" or t == "library_result") and msg.run_id and msg.run_id ~= kc.run_id then
    return {}
  end
  if t == "query" then
    local r = M.run_query(msg.lua, msg.surface)
    r.type = "result"
    r.id = msg.id
    return { r }
  elseif t == "mark" then
    return { result(msg, M.mark(msg.chat, msg.marks)) }
  elseif t == "unmark" then
    return { result(msg, M.unmark(msg.chat, msg)) }
  elseif t == "list_marks" then
    return { result(msg, M.list_marks(msg.chat)) }
  elseif t == "pong" then
    kc.last_pong_tick = game.tick
  elseif t == "ack" then
    local e = find_feed_entry(msg.id)
    if e then e.acked = true end
  elseif t == "reply" then
    local tab = M.tab(msg.chat)
    -- the live trace folds into one line above the answer, so the feed keeps its history
    if tab.trace and #tab.trace > 0 then
      local secs = tab.thinking_since and math.floor((game.tick - tab.thinking_since) / 60)
      push(tab, { from = "trace_summary", text = (tab.steps or #tab.trace) .. " steps" ..
        (secs and (", " .. secs .. " s") or "") })
    end
    tab.trace = nil
    push(tab, { from = "kira", text = msg.text, id = msg.id })
    tab.state, tab.note = "idle", nil
    tab.thinking_since, tab.steps, tab.activity = nil, nil, nil
  elseif t == "trace" then
    -- a step of the running turn: what Kira wrote between tool calls, or which tool ran
    local tab = M.tab(msg.chat)
    tab.trace = tab.trace or {}
    local last = tab.trace[#tab.trace]
    if last and last.kind == "tool" and msg.kind == "tool" and last.text == msg.text then
      last.n = (last.n or 1) + 1
    else
      tab.trace[#tab.trace + 1] = { kind = msg.kind or "text", text = msg.text or "" }
      while #tab.trace > M.TRACE_LIMIT do table.remove(tab.trace, 1) end
    end
  elseif t == "status" then
    local tab = M.tab(msg.chat)
    if msg.state == "thinking" then
      -- progress of a running turn: the first status starts the clock, later ones add steps
      if tab.state ~= "thinking" then tab.thinking_since, tab.trace = game.tick, nil end
      tab.steps, tab.activity = msg.steps, msg.activity
    else
      tab.thinking_since, tab.steps, tab.activity = nil, nil, nil
      tab.trace = nil
    end
    tab.state, tab.note = msg.state, msg.note
  end
  return {}
end

function M.connected()
  local last = storage.kc and storage.kc.last_pong_tick
  return last ~= nil and (game.tick - last) <= M.PONG_TIMEOUT_TICKS
end

--- Plain snapshot for tests and the remote interface (no LuaObjects).
function M.state()
  local kc = storage.kc
  local tabs = {}
  for chat, t in pairs(kc.tabs) do
    tabs[chat] = { feed = t.feed, state = t.state, note = t.note, steps = t.steps, activity = t.activity,
                   thinking_since = t.thinking_since, trace = t.trace }
  end
  return {
    run_id = kc.run_id, run_name = kc.run_name, tick = game.tick,
    connected = M.connected(), tabs = tabs, tab_order = kc.tab_order,
  }
end

return M
