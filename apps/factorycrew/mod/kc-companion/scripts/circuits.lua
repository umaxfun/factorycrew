-- Circuit-network reader: `kc.circuits(surface, opts)` -> a snapshot in the format of
-- docs/circuits-format.md (v1). Reads combinators, every circuit-capable entity's control behavior,
-- the networks on their wire connectors with the current signals, and the wires between them.
-- Works on Factorio 2.0.77 and 2.1.x: every field is read through pcall, so a field one version
-- lacks is simply absent (see the 2.0/2.1 notes next to the field tables).
local M = {}

M.FORMAT_VERSION = 1

-- ── small safe accessors ─────────────────────────────────────────────────────

--- obj[key], nil when missing or when the read raises (a field the running version lacks).
local function get(obj, key)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if ok then return v end
  return nil
end

--- obj[method](...) as a method call, nil when it is missing or raises.
local function call(obj, method, ...)
  local f = get(obj, method)
  if type(f) ~= "function" then return nil end
  local args = { ... }
  local ok, r = pcall(function() return f(table.unpack(args)) end)
  if ok then return r end
  return nil
end

local function round(v, n)
  if type(v) ~= "number" then return v end
  return tonumber(string.format("%." .. (n or 4) .. "f", v))
end

local function name_of(v) -- a prototype or its name
  if type(v) == "string" then return v end
  local n = get(v, "name")
  if type(n) == "string" then return n end
  return nil
end

-- ── signals and conditions ───────────────────────────────────────────────────

--- "<type>/<name>[@quality]" from a SignalID / SignalFilter / item name; nil for an empty slot.
function M.signal_key(s)
  if s == nil then return nil end
  if type(s) == "string" then return "item/" .. s end
  local n = name_of(get(s, "name"))
  if not n then return nil end
  local key = (get(s, "type") or "item") .. "/" .. n
  local q = name_of(get(s, "quality"))
  if q and q ~= "normal" then key = key .. "@" .. q end
  return key
end

local COMPARATOR = { ["≥"] = ">=", ["≤"] = "<=", ["≠"] = "!=" }
local function comparator(c)
  c = c or "<"
  return COMPARATOR[c] or c
end

local function networks(sel)
  if type(sel) ~= "table" then return { red = true, green = true } end
  return { red = sel.red ~= false, green = sel.green ~= false }
end

--- Normalised Condition from a CircuitCondition(Definition) or a DeciderCombinatorCondition.
function M.condition(c, decider)
  if type(c) ~= "table" then return nil end
  local second = M.signal_key(get(c, "second_signal"))
  local out = {
    first = M.signal_key(get(c, "first_signal")),
    comparator = comparator(get(c, "comparator")),
    second = second,
    constant = (second == nil) and (get(c, "constant") or 0) or nil,
    join = "and",
  }
  if decider then
    out.first_networks = networks(get(c, "first_signal_networks"))
    out.second_networks = networks(get(c, "second_signal_networks"))
    out.join = get(c, "compare_type") == "or" and "or" or "and"
  else
    out.first_networks = { red = true, green = true }
    out.second_networks = { red = true, green = true }
  end
  local fulfilled = get(c, "fulfilled")
  if fulfilled ~= nil then out.fulfilled = fulfilled end
  return out
end

--- {key = count} of a Signal array, zero counts left out, duplicate keys summed.
local function signal_set(list)
  local out = {}
  for _, s in ipairs(list or {}) do
    local k = M.signal_key(s.signal)
    if k and s.count and s.count ~= 0 then out[k] = (out[k] or 0) + s.count end
  end
  for k, v in pairs(out) do if v == 0 then out[k] = nil end end
  return out
end

-- ── combinators ──────────────────────────────────────────────────────────────

local function decider_output(o)
  return {
    signal = M.signal_key(get(o, "signal")),
    copy_count_from_input = get(o, "copy_count_from_input") ~= false,
    networks = networks(get(o, "networks")),
    constant = get(o, "constant") or 1,
  }
end

local function decider_params(cb)
  local p = get(cb, "parameters") or {}
  local conds, outs = {}, {}
  for i, c in ipairs(p.conditions or {}) do
    conds[i] = M.condition(c, true)
    conds[i].fulfilled = nil
  end
  if conds[1] then conds[1].join = "and" end
  for i, o in ipairs(p.outputs or {}) do outs[i] = decider_output(o) end
  local out = { conditions = conds, outputs = outs }
  -- 2.1: outputs used when the conditions are false. Absent on 2.0 (and when empty).
  if type(p.else_outputs) == "table" and #p.else_outputs > 0 then
    local e = {}
    for i, o in ipairs(p.else_outputs) do e[i] = decider_output(o) end
    out.else_outputs = e
  end
  return out
end

local function arithmetic_params(cb)
  local p = get(cb, "parameters") or {}
  local first, second = M.signal_key(p.first_signal), M.signal_key(p.second_signal)
  return {
    first = first,
    first_constant = first == nil and (p.first_constant or 0) or nil,
    first_networks = networks(p.first_signal_networks),
    operation = p.operation or "*",
    second = second,
    second_constant = second == nil and (p.second_constant or 0) or nil,
    second_networks = networks(p.second_signal_networks),
    output = M.signal_key(p.output_signal),
  }
end

local function constant_params(cb)
  local sections = {}
  for _, sec in ipairs(get(cb, "sections") or {}) do
    local sig = {}
    for _, f in ipairs(get(sec, "filters") or {}) do
      local k = M.signal_key(f.value)
      local n = f.min or 0
      if k and n ~= 0 then sig[k] = (sig[k] or 0) + n end
    end
    for k, v in pairs(sig) do if v == 0 then sig[k] = nil end end
    sections[#sections + 1] = {
      active = get(sec, "active") ~= false,
      multiplier = round(get(sec, "multiplier") or 1, 6),
      signals = sig,
    }
  end
  return { enabled = get(cb, "enabled") ~= false, sections = sections }
end

-- Selector fields per mode, as the game names them (2.0 + 2.1; a field one version lacks is absent).
local SELECTOR_FIELDS = {
  select = { "select_max", "index_constant", "index_signal", "index_comparator" },
  count = { "count_signal" },
  random = { "random_update_interval" },
  ["quality-filter"] = { "quality_filter" },
  ["quality-transfer"] = { "select_quality_from_signal", "quality_source_static", "quality_source_signal",
                           "quality_destination_signal" },
  ["quality-select"] = { "select_highest_quality" },                     -- 2.1
  time = { "game_tick_signal", "day_tick_signal", "day_length_signal" }, -- 2.1
}

local function selector_params(cb)
  local p = get(cb, "parameters") or {}
  local mode = p.operation or "select"
  local out = { mode = mode }
  for _, f in ipairs(SELECTOR_FIELDS[mode] or {}) do
    local v = p[f]
    if v ~= nil then
      if f:sub(-7) == "_signal" then
        out[f] = M.signal_key(v)
      elseif f == "quality_filter" then
        if type(v) == "string" or name_of(v) then
          out[f] = { quality = name_of(v), comparator = "=" }
        else
          out[f] = { quality = name_of(v.quality), comparator = comparator(v.comparator or "=") }
        end
      elseif f == "quality_source_static" then
        out[f] = name_of(v)
      elseif f == "index_comparator" then
        out[f] = comparator(v)
      else
        out[f] = v
      end
    end
  end
  return out
end

-- ── control behavior of every other circuit entity ───────────────────────────

-- Reverse maps for the enums the control behaviors return as numbers.
local function enum(path)
  local t = defines.control_behavior
  for part in path:gmatch("[^.]+") do
    t = get(t, part)
    if t == nil then return nil end
  end
  local rev = {}
  for k, v in pairs(t) do rev[v] = k end
  return rev
end

-- attribute -> kind. Kinds: b (boolean), s (SignalID), c (condition), e:<defines path> (enum),
-- color, msgs (display panel), speaker, raw (plain JSON-able table).
-- Collected from every Lua*ControlBehavior class of the 2.0.77 and 2.1.24 runtime APIs; an
-- attribute the running version or the entity's behavior lacks reads as nil and is skipped.
local ATTRS = {
  -- generic on/off (inserter, belt, train stop, lamp, pump, offshore pump, power switch, drill, ...)
  { "circuit_enable_disable", "b" }, { "circuit_condition", "c" },
  { "connect_to_logistic_network", "b" }, { "logistic_condition", "c" },
  { "circuit_condition_enabled", "b" },                -- logistic container
  -- inserter
  { "circuit_read_hand_contents", "b" }, { "circuit_hand_read_mode", "e:inserter.hand_read_mode" },
  { "circuit_set_filters", "b" }, { "circuit_set_stack_size", "b" }, { "circuit_stack_control_signal", "s" },
  -- belt, containers, hubs
  { "read_contents", "b" }, { "read_contents_mode", "e:transport_belt.content_read_mode" },
  { "read_content", "b" }, { "include_hands", "b" },   -- asteroid collector
  { "read_empty_slots", "b" }, { "empty_slots_signal", "s" }, -- 2.1
  { "set_requests", "b" },                             -- 2.1 logistic container / landing pad / hub
  { "circuit_exclusive_mode_of_operation", "e:logistic_container.exclusive_mode" }, -- 2.0 (+2.1 fluid box)
  -- train stop
  { "send_to_train", "b" }, { "read_from_train", "b" }, { "read_stopped_train", "b" }, { "stopped_train_signal", "s" },
  { "read_trains_count", "b" }, { "trains_count_signal", "s" }, { "set_trains_limit", "b" },
  { "trains_limit_signal", "s" }, { "set_priority", "b" }, { "priority_signal", "s" },
  -- lamp
  { "use_colors", "b" }, { "color_mode", "e:lamp.color_mode" }, { "red_signal", "s" }, { "green_signal", "s" },
  { "blue_signal", "s" }, { "rgb_signal", "s" }, { "color", "color" },
  -- pump, asteroid collector, splitter
  { "set_filter", "b" },
  { "set_input_side", "b" }, { "set_output_side", "b" },
  { "input_left_condition", "c" }, { "input_right_condition", "c" },
  { "output_left_condition", "c" }, { "output_right_condition", "c" },
  -- roboport
  { "read_logistics", "b" }, { "read_robot_stats", "b" }, { "read_items_mode", "e:roboport.read_items_mode" },
  { "available_logistic_output_signal", "s" }, { "total_logistic_output_signal", "s" },
  { "available_construction_output_signal", "s" }, { "total_construction_output_signal", "s" },
  { "roboport_count_output_signal", "s" },
  -- rail signals (orange_signal; red/green/blue shared with the lamp)
  { "read_signal", "b" }, { "close_signal", "b" }, { "orange_signal", "s" },
  -- accumulator, wall/gate, reactor, heat pipe, single fluid box
  { "read_charge", "b" }, { "output_signal", "s" },
  { "open_gate", "b" }, { "read_sensor", "b" },
  { "read_fuel", "b" }, { "read_temperature", "b" }, { "temperature_signal", "s" },
  { "include_fuel", "b" },                             -- 2.0 assembler/furnace; 2.1 renames it read_fuel
  -- assembler, furnace
  { "circuit_set_recipe", "b" }, { "circuit_read_contents", "b" }, { "include_in_crafting", "b" },
  { "circuit_read_ingredients", "b" }, { "circuit_read_recipe_finished", "b" },
  { "circuit_recipe_finished_signal", "s" }, { "circuit_read_working", "b" }, { "circuit_working_signal", "s" },
  -- mining drill, loader, turrets
  { "circuit_read_resources", "b" }, { "resource_read_mode", "e:mining_drill.resource_read_mode" },
  { "circuit_read_transfers", "b" }, { "circuit_set_filters", "b" },
  { "read_ammo", "b" }, { "set_priority_list", "b" }, { "set_ignore_unlisted_targets", "b" },
  { "ignore_unlisted_targets_condition", "c" },
  -- agricultural tower (2.1 adds planting / harvesting conditions)
  { "enable_planting_condition", "b" }, { "planting_condition", "c" },
  { "enable_harvesting_condition", "b" }, { "harvesting_condition", "c" },
  -- rocket silo, platform hub, radar
  { "read_mode", "e:rocket_silo.read_mode" }, { "read_launched", "b" }, { "launched_signal", "s" },
  { "send_to_platform", "b" }, { "read_moving_from", "b" }, { "read_moving_to", "b" },
  { "read_speed", "b" }, { "speed_signal", "s" }, { "read_damage_taken", "b" }, { "damage_taken_signal", "s" },
  { "mode", "e:radar.mode" }, { "universe_channel", "s" }, -- 2.1
  -- lab (2.1)
  { "read_research_cost", "b" }, { "read_technology_level", "b" }, { "technology_level_signal", "s" },
  { "set_research", "b" }, { "research_conditions", "research" },
  -- programmable speaker, display panel
  { "circuit_parameters", "speaker" },
  { "messages", "msgs" }, -- 2.0
  { "records", "msgs" },  -- 2.1
  -- 2.1: which wires the entity reads / writes (on every LuaControlBehavior)
  { "input_networks", "nets" }, { "output_networks", "nets" },
}

-- The same exclusive-mode attribute names a different enum on different behaviors.
local EXCLUSIVE_ENUM = {
  [defines.control_behavior.type.cargo_landing_pad or -1] = "cargo_landing_pad.exclusive_mode",
  [get(defines.control_behavior.type, "single_fluid_box") or -2] = "single_fluid_box.exclusive_mode",
}

local enum_cache = {}
local function enum_name(path, v)
  if type(v) ~= "number" then return v end
  if enum_cache[path] == nil then enum_cache[path] = enum(path) or false end
  local rev = enum_cache[path]
  return rev and rev[v] or v
end

local function convert(kind, v, cb_type)
  if kind == "b" then return v == true end
  if kind == "s" then return M.signal_key(v) end
  if kind == "c" then return M.condition(v) end
  if kind == "color" then
    if type(v) ~= "table" then return nil end
    return { r = round(v.r or v[1]), g = round(v.g or v[2]), b = round(v.b or v[3]), a = round(v.a or v[4] or 1) }
  end
  if kind == "nets" then -- 2.1 only; kept only when it narrows the default (both wires)
    local n = networks(v)
    if n.red and n.green then return nil end
    return n
  end
  if kind == "speaker" then
    if type(v) ~= "table" then return nil end
    return { signal_value_is_pitch = v.signal_value_is_pitch, instrument_id = v.instrument_id,
             note_id = v.note_id, stop_playing_sounds = v.stop_playing_sounds }
  end
  if kind == "msgs" then
    if type(v) ~= "table" then return nil end
    local out = {}
    for i, m in ipairs(v) do
      out[i] = { text = m.text, icon = M.signal_key(m.icon), condition = M.condition(m.condition) }
    end
    return out
  end
  if kind == "research" then
    if type(v) ~= "table" then return nil end
    local out = {}
    for i, r in ipairs(v) do out[i] = { name = r.name, count = r.count, condition = M.condition(r.condition) } end
    return out
  end
  if kind:sub(1, 2) == "e:" then
    local path = kind:sub(3)
    if path == "logistic_container.exclusive_mode" and EXCLUSIVE_ENUM[cb_type] then path = EXCLUSIVE_ENUM[cb_type] end
    return enum_name(path, v)
  end
  return nil
end

-- read/set flag -> the attribute carrying its value (signal or mode); without one the value is true.
local FLAG_VALUE = {
  circuit_read_hand_contents = "circuit_hand_read_mode", circuit_set_stack_size = "circuit_stack_control_signal",
  read_contents = "read_contents_mode", read_empty_slots = "empty_slots_signal",
  read_stopped_train = "stopped_train_signal", read_trains_count = "trains_count_signal",
  set_trains_limit = "trains_limit_signal", set_priority = "priority_signal",
  read_charge = "output_signal", read_sensor = "output_signal", read_temperature = "temperature_signal",
  read_speed = "speed_signal", read_damage_taken = "damage_taken_signal", read_launched = "launched_signal",
  read_technology_level = "technology_level_signal", circuit_read_working = "circuit_working_signal",
  circuit_read_recipe_finished = "circuit_recipe_finished_signal", circuit_read_resources = "resource_read_mode",
  read_logistics = "read_items_mode",
}
-- flag -> {group, key}; derived names when not listed (circuit_read_x -> read.x, set_x -> set.x)
local FLAG_NAME = {
  circuit_read_hand_contents = { "read", "hand" }, circuit_read_resources = { "read", "resources" },
  read_from_train = { "read", "train_contents" }, read_content = { "read", "contents" },
  circuit_set_recipe = { "set", "recipe" },
}

local function flag_slot(attr)
  local n = FLAG_NAME[attr]
  if n then return n[1], n[2] end
  local a = attr:gsub("^circuit_", "")
  if a:sub(1, 5) == "read_" then return "read", a:sub(6) end
  if a:sub(1, 4) == "set_" then return "set", a:sub(5) end
  return nil
end

--- {enable_condition, read, set, raw} of a non-combinator control behavior (nil cb -> empty).
function M.control(cb)
  local out = { enable_condition = nil, read = {}, set = {}, raw = {} }
  if not cb then return out end
  local cb_type = get(cb, "type")
  local raw = out.raw
  for _, a in ipairs(ATTRS) do
    local name, kind = a[1], a[2]
    local v = get(cb, name)
    if v ~= nil and type(v) ~= "function" then
      local c = convert(kind, v, cb_type)
      if c ~= nil then raw[name] = c end
    end
  end
  -- leave out what does nothing: the logistic condition while unconnected, empty conditions
  if not raw.connect_to_logistic_network then raw.logistic_condition = nil end
  local switched = raw.circuit_enable_disable or raw.circuit_condition_enabled
  for name, v in pairs(raw) do
    if type(v) == "table" and v.comparator and v.first == nil and v.second == nil
       and not (name == "circuit_condition" and switched) then
      raw[name] = nil
    end
  end
  -- 2.0 exclusive mode -> the 2.1-style booleans, so read/set look the same on both
  local ex = raw.circuit_exclusive_mode_of_operation
  if ex == "send_contents" and raw.read_contents == nil then out.read.contents = true end
  if ex == "set_requests" and raw.set_requests == nil then out.set.requests = true end
  if ex == "send_segment_contents" then out.read.segment_contents = true end
  for name, v in pairs(raw) do
    if v == true then
      local group, key = flag_slot(name)
      if group then
        local val = FLAG_VALUE[name] and raw[FLAG_VALUE[name]]
        if val == nil or val == "none" then val = true end
        out[group][key] = val
      end
    end
  end
  -- the condition that switches the entity on/off, when that switch is on
  if raw.circuit_enable_disable or raw.circuit_condition_enabled then
    out.enable_condition = raw.circuit_condition
  elseif get(cb, "type") == defines.control_behavior.type.programmable_speaker then
    out.enable_condition = raw.circuit_condition
  end
  if out.enable_condition then
    out.enable_condition = {
      first = out.enable_condition.first, comparator = out.enable_condition.comparator,
      second = out.enable_condition.second, constant = out.enable_condition.constant,
      first_networks = out.enable_condition.first_networks, second_networks = out.enable_condition.second_networks,
      join = "and",
    }
  end
  return out
end

-- ── connectors, networks, wires ──────────────────────────────────────────────

local W = defines.wire_connector_id
local COMBINATOR_CONNECTORS = {
  { "in_red", W.combinator_input_red }, { "in_green", W.combinator_input_green },
  { "out_red", W.combinator_output_red }, { "out_green", W.combinator_output_green },
}
local CIRCUIT_CONNECTORS = { { "red", W.circuit_red }, { "green", W.circuit_green } }
-- combinator_input_* and circuit_* share numeric ids, so a connector's name depends on its owner
local function name_in(list, id)
  for _, c in ipairs(list) do if c[2] == id then return c[1] end end
  return nil
end

local KIND = {
  ["decider-combinator"] = "decider", ["arithmetic-combinator"] = "arithmetic",
  ["constant-combinator"] = "constant", ["selector-combinator"] = "selector",
}
local HAS_IO = { decider = true, arithmetic = true, selector = true }

local function connectors_of(e, kind)
  return HAS_IO[kind] and COMBINATOR_CONNECTORS or CIRCUIT_CONNECTORS
end

local function wire_color(conn)
  local t = get(conn, "wire_type")
  if t == defines.wire_type.red then return "red" end
  if t == defines.wire_type.green then return "green" end
  return nil
end

--- Circuit wire connectors of an entity: {{name, LuaWireConnector, wire_connector_id}} (copper left out).
local function circuit_connectors(e)
  local out = {}
  local kind = KIND[e.type] or "other"
  for _, c in ipairs(connectors_of(e, kind)) do
    local conn = call(e, "get_wire_connector", c[2], false)
    if conn then out[#out + 1] = { c[1], conn, c[2] } end
  end
  return out
end

local function connections(conn)
  return get(conn, "real_connections") or get(conn, "connections") or {}
end

local function wired(e)
  for _, c in ipairs(circuit_connectors(e)) do
    if #connections(c[2]) > 0 then return true end
  end
  return false
end

local function usable(e)
  return e and e.valid and e.type ~= "entity-ghost" and e.type ~= "tile-ghost"
end

local function circuit_capable(e)
  if not usable(e) then return false end
  if KIND[e.type] then return true end
  return #circuit_connectors(e) > 0
end

-- ── the reader ───────────────────────────────────────────────────────────────

local function encoded_size(t)
  local ok, s = pcall(helpers.table_to_json, t)
  return ok and type(s) == "string" and #s or math.huge
end

local function by_yx(a, b)
  if a.position.y ~= b.position.y then return a.position.y < b.position.y end
  if a.position.x ~= b.position.x then return a.position.x < b.position.x end
  return (a.unit_number or 0) < (b.unit_number or 0)
end

-- Entity types that can carry a circuit connector (vanilla 2.0/2.1 + Space Age), for unit_number lookups.
local CIRCUIT_TYPES = {
  "decider-combinator", "arithmetic-combinator", "constant-combinator", "selector-combinator",
  "inserter", "transport-belt", "container", "logistic-container", "linked-container", "infinity-container",
  "proxy-container", "train-stop", "lamp", "pump", "offshore-pump", "roboport", "rail-signal", "rail-chain-signal",
  "power-switch", "accumulator", "display-panel", "programmable-speaker", "agricultural-tower",
  "asteroid-collector", "cargo-landing-pad", "space-platform-hub", "electric-pole", "wall", "gate",
  "storage-tank", "assembling-machine", "furnace", "mining-drill", "splitter", "lane-splitter", "loader",
  "loader-1x1", "radar", "turret", "ammo-turret", "electric-turret", "fluid-turret", "artillery-turret",
  "reactor", "rocket-silo", "boiler", "heat-pipe", "lab", "land-mine", "valve", "fusion-reactor",
  "fusion-generator", "underground-belt", "pipe", "pipe-to-ground", "generator", "burner-generator",
  "cargo-bay", "thruster", "asteroid-collector",
}

local function seeds(surface, opts)
  local list = {}
  if opts.entities then
    local wanted, missing = {}, false
    for i, x in ipairs(opts.entities) do
      local e = x
      if type(x) == "number" then
        -- only prototypes flagged get-by-unit-number resolve here (2.0 and 2.1): not lamps, chests, ...
        e = game.get_entity_by_unit_number(x)
        if not e then wanted[x] = i; missing = true end
      end
      if usable(e) then list[i] = e end
    end
    if missing then -- one scan of the surface (or of opts.area) for the circuit entity types
      -- an unknown type name in a filter raises, so keep the types some loaded prototype has
      local have, types = {}, {}
      for _, p in pairs(prototypes.entity) do have[p.type] = true end
      for _, t in ipairs(CIRCUIT_TYPES) do if have[t] then types[#types + 1] = t; have[t] = nil end end
      local filter = { type = types }
      if opts.area then filter.area = opts.area end
      for _, e in pairs(surface.find_entities_filtered(filter)) do
        local i = wanted[get(e, "unit_number") or false]
        if i and usable(e) then list[i] = e end
      end
    end
    local out = {}
    for i = 1, #opts.entities do if list[i] then out[#out + 1] = list[i] end end
    return out
  end
  local filter
  if opts.area then
    filter = { area = opts.area }
  elseif opts.position then
    filter = { position = opts.position, radius = opts.radius or 10 }
  else
    error("kc.circuits: give opts.entities, opts.area or opts.position + radius")
  end
  for _, e in pairs(surface.find_entities_filtered(filter)) do
    if circuit_capable(e) and (KIND[e.type] or wired(e)) then list[#list + 1] = e end
  end
  table.sort(list, by_yx)
  return list
end

local function entity_record(e, id)
  local kind = KIND[e.type] or "other"
  local rec = {
    id = id, name = e.name, type = e.type, x = e.position.x, y = e.position.y,
    direction = e.direction or 0, kind = kind, connectors = {},
  }
  local q = name_of(get(e, "quality"))
  if q and q ~= "normal" then rec.quality = q end
  -- an unpowered combinator outputs nothing: the simulator needs to know
  local st = get(e, "status")
  if st then
    for k, v in pairs(defines.entity_status) do
      if v == st then rec.status = k break end
    end
  end
  for _, c in ipairs(connectors_of(e, kind)) do
    local net = call(e, "get_circuit_network", c[2])
    local nid = net and get(net, "network_id")
    -- JSON has no way to keep a nil value: an unwired connector is absent from `connectors`
    rec.connectors[c[1]] = nid and tostring(nid) or nil
  end
  local cb = call(e, "get_control_behavior")
  if kind == "decider" then rec.params = decider_params(cb)
  elseif kind == "arithmetic" then rec.params = arithmetic_params(cb)
  elseif kind == "constant" then rec.params = constant_params(cb)
  elseif kind == "selector" then rec.params = selector_params(cb)
  else rec.control = M.control(cb) end
  if kind ~= "other" and kind ~= "constant" then
    -- 2.1: a combinator may restrict which wires it reads / writes
    local i, o = get(cb, "input_networks"), get(cb, "output_networks")
    rec.params.input_networks = type(i) == "table" and convert("nets", i) or nil
    rec.params.output_networks = type(o) == "table" and convert("nets", o) or nil
  end
  return rec
end

--- Snapshot of the circuit networks around some entities.
--- surface: LuaSurface (nil = player's / first). opts: {entities = {LuaEntity | unit_number, ...}
--- | area = BoundingBox | position = MapPosition + radius = 10, follow = true (walk wires until the
--- networks close), limit = 300 (entities), max_bytes = 48000 (JSON budget)}.
--- Returns the v1 snapshot of docs/circuits-format.md; entities in walk order (seeds first, sorted by y, x).
function M.read(surface, opts)
  opts = opts or {}
  surface = surface or (game.connected_players[1] and game.connected_players[1].surface) or game.surfaces[1]
  local limit = opts.limit or 300
  local follow = opts.follow ~= false

  local order, seen = {}, {}
  local truncated = false
  -- id = unit_number; an entity without one gets a negative id, stable within this read
  local synthetic, ids = 0, {}
  local function ident(e)
    local u = get(e, "unit_number")
    if u then return u end
    local pk = e.name .. "@" .. e.position.x .. "," .. e.position.y
    if not ids[pk] then
      synthetic = synthetic - 1
      ids[pk] = synthetic
    end
    return ids[pk]
  end
  local queue = {}
  local function push(e)
    if not usable(e) then return end -- explicit entities may sit on any surface; wires never leave one
    local id = ident(e)
    if seen[id] then return end
    if #order >= limit then truncated = true; return end
    seen[id] = e
    order[#order + 1] = id
    queue[#queue + 1] = e
  end
  for _, e in ipairs(seeds(surface, opts)) do push(e) end
  local head = 1
  while follow and head <= #queue do
    local e = queue[head]
    head = head + 1
    for _, c in ipairs(circuit_connectors(e)) do
      for _, w in ipairs(connections(c[2])) do
        local t = w.target
        local owner = t and get(t, "owner")
        if owner then push(owner) end
      end
    end
  end

  local entities, nets, wires, wire_seen = {}, {}, {}, {}
  for _, id in ipairs(order) do
    local e = seen[id]
    local rec = entity_record(e, id)
    entities[#entities + 1] = rec
    for _, c in ipairs(circuit_connectors(e)) do
      local net = call(e, "get_circuit_network", c[3])
      local nid = net and get(net, "network_id")
      if nid and not nets[tostring(nid)] then
        nets[tostring(nid)] = { color = wire_color(net) or wire_color(c[2]), signals = signal_set(get(net, "signals")) }
      end
      for _, w in ipairs(connections(c[2])) do
        local t = w.target
        local owner = t and get(t, "owner")
        if usable(owner) then
          local oid = ident(owner)
          local tname = name_in(connectors_of(owner, KIND[owner.type] or "other"), get(t, "wire_connector_id"))
          if seen[oid] and tname then
            local a = { id, c[1] }
            local b = { oid, tname }
            if b[1] < a[1] or (b[1] == a[1] and b[2] < a[2]) then a, b = b, a end
            local k = a[1] .. ":" .. a[2] .. "-" .. b[1] .. ":" .. b[2]
            if not wire_seen[k] then
              wire_seen[k] = true
              wires[#wires + 1] = { a = a[1], a_connector = a[2], b = b[1], b_connector = b[2],
                                    color = wire_color(c[2]) }
            end
          end
        end
      end
    end
  end

  local snap = {
    v = M.FORMAT_VERSION, tick = game.tick, game_version = script.active_mods.base,
    truncated = truncated, networks = nets, entities = entities, wires = wires,
  }
  -- stay under the query result cap: drop wires, then far entities, then network signals
  local budget = opts.max_bytes or 48000
  if encoded_size(snap) > budget then
    snap.truncated = true
    snap.wires = {}
    snap.wires_dropped = true
  end
  while encoded_size(snap) > budget and #snap.entities > 1 do
    local n = math.max(1, math.floor(#snap.entities * 3 / 4))
    local k = {}
    for i = 1, n do k[i] = snap.entities[i] end
    snap.entities = k
  end
  if encoded_size(snap) > budget then
    for _, net in pairs(snap.networks) do
      local keys = {}
      for k in pairs(net.signals) do keys[#keys + 1] = k end
      table.sort(keys)
      local keep = {}
      for i = 1, math.min(#keys, 20) do keep[keys[i]] = net.signals[keys[i]] end
      net.signals = keep
      net.signals_truncated = true
    end
  end
  return snap
end

return M
