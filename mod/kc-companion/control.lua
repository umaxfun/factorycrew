local protocol = require("scripts.protocol")
local gui = require("scripts.gui")
local pointers = require("scripts.pointers")
local panels = require("scripts.panels")
local atlas = require("scripts.atlas")

-- recv_udp/send_udp raise when the game was not started with --enable-lua-udp
-- (e.g. under FactorioTest). Probe once per load and stay quiet afterwards.
local udp_ok = nil

local function send(msg)
  if not udp_ok then return end
  for _, packet in ipairs(protocol.encode(msg)) do
    helpers.send_udp(protocol.BRIDGE_PORT, packet)
  end
end

local function init_players()
  for _, p in pairs(game.players) do gui.ensure_button(p) end
end

script.on_init(function()
  protocol.init_storage()
  init_players()
end)

script.on_configuration_changed(function()
  protocol.init_storage()
  init_players()
  -- other mods (or their versions) changed: what stands may mean something else now
  atlas.mark_all()
  storage.kc.atlas_version = (storage.kc.atlas_version or 0) + 1
end)

script.on_event(defines.events.on_player_created, function(e)
  gui.ensure_button(game.get_player(e.player_index))
end)

script.on_nth_tick(10, function()
  if udp_ok == false then return end
  local ok = pcall(helpers.recv_udp)
  if udp_ok == nil then
    udp_ok = ok
    protocol.udp_state = ok and "on" or "off"
    if ok then send(protocol.heartbeat("hello")) end
    gui.refresh_all()
  end
end)

script.on_nth_tick(protocol.HEARTBEAT_TICKS, function()
  send(protocol.heartbeat())
end)

script.on_nth_tick(60, function()
  gui.tick()
  panels.tick()
  if panels.library_tick() then gui.refresh_all() end
end)

-- the base changed: the atlas's cached structure is stale (the MCP server compares atlas_version)
local function changed(e)
  -- closing the inventory or a biter killing a tree changes nothing the atlas knows
  local ent = e.entity or e.created_entity
  if e.name == defines.events.on_gui_closed and not ent then return end
  if ent and ent.valid and ent.force and ent.force.name ~= "player" then return end
  storage.kc.atlas_version = (storage.kc.atlas_version or 0) + 1
  atlas.mark(ent or e.destination)
end
for _, ev in pairs({ "on_built_entity", "on_robot_built_entity", "script_raised_built", "script_raised_revive",
                     "on_space_platform_built_entity", "on_player_mined_entity", "on_robot_mined_entity",
                     "on_entity_died", "script_raised_destroy", "on_player_rotated_entity", "on_player_flipped_entity",
                     "on_entity_settings_pasted", "on_gui_closed" }) do
  if defines.events[ev] then script.on_event(defines.events[ev], changed) end
end

-- the atlas dump, a few chunks per tick (scripts/atlas.lua)
script.on_event(defines.events.on_tick, function()
  if not atlas.running() then return end
  for _, m in ipairs(atlas.step()) do send(m) end
end)

-- what changes the window when it arrives from the bridge
local REFRESH = { reply = true, status = true, ack = true, mark = true, unmark = true, trace = true,
                  library_result = true }

script.on_event(defines.events.on_udp_packet_received, function(e)
  local msg = protocol.decode(e.payload)
  if not msg then return end
  local was_connected = protocol.connected()
  local update_to = protocol.update_to
  if msg.type == "library_result" then
    if not msg.run_id or msg.run_id == storage.kc.run_id then panels.on_library(msg) end
  else
    for _, reply in ipairs(protocol.handle(msg)) do
      send(reply)
    end
  end
  if REFRESH[msg.type] or (msg.type == "pong" and (not was_connected or protocol.update_to ~= update_to)) then
    gui.refresh_all()
  end
end)

script.on_event(defines.events.on_chart_tag_removed, function(e)
  protocol.on_tag_removed(e.tag)
  gui.refresh_all()
end)

script.on_event("kc-toggle", function(e)
  gui.toggle(game.get_player(e.player_index))
end)

script.on_event(defines.events.on_player_selected_area, function(e)
  if e.item ~= pointers.TOOL then return end
  local player = game.get_player(e.player_index)
  local p, why = pointers.add(player, e.surface, e.area, e.entities)
  pointers.drop_tool(player) -- one pointer per pick-up: the tool leaves the hand
  if p then
    gui.on_pointer(player, p)
  elseif why == "limit" then
    player.create_local_flying_text{ text = "Up to " .. pointers.LIMIT .. " pointers per question", create_at_cursor = true }
  end
end)

-- shift-drag (or shift-click) with the tool: start over
script.on_event(defines.events.on_player_alt_selected_area, function(e)
  if e.item ~= pointers.TOOL then return end
  local player = game.get_player(e.player_index)
  pointers.clear(player)
  pointers.drop_tool(player)
  gui.refresh(player)
end)

script.on_event("kc-clear-cursor", function(e)
  pointers.drop_tool(game.get_player(e.player_index))
end)

script.on_event(defines.events.on_gui_click, function(e)
  if panels.on_click(e) then
    gui.refresh(game.get_player(e.player_index)) -- the Panels row shows which are open
    return
  end
  gui.on_click(e, send)
end)

script.on_event(defines.events.on_gui_selection_state_changed, function(e)
  panels.on_selection(e)
end)

script.on_event(defines.events.on_gui_confirmed, function(e)
  gui.on_confirmed(e, send)
end)

-- Entry points for level 2/3 tests over RCON (they act as the player).
remote.add_interface("kc", {
  ask = function(chat, text)
    local player = game.connected_players[1]
    local msg = protocol.ask(chat, text, player, player and pointers.take(player))
    send(msg)
    gui.refresh_all()
    return msg.id
  end,
  state = function()
    return protocol.state()
  end,
  set_run_name = function(name)
    storage.kc.run_name = name
    gui.refresh_all()
  end,
  -- window, for the UI iteration loop (tools/ui_dev.py) and tests
  ui_open = function(index) return gui.open(index) end,
  ui_close = function(index) gui.close(index) end,
  ui_click = function(name, index) return gui.click(name, send, index) end,
  ui_confirm = function(name, text, index) return gui.confirm(name, text, send, index) end,
  -- pointers as if made with the tool: {x, y} is a click, {{x0, y0}, {x1, y1}} a drag
  point = function(where, index)
    local player = game.get_player(index or 1)
    local p = pointers.simulate(player, where)
    if p then gui.on_pointer(player, p) end
    return p
  end,
  pointers = function(index) return pointers.list(game.get_player(index or 1)) end,
  -- the atlas structure dump in one call, into script-output/<file> (scripts/atlas-fixture.sh)
  atlas_dump = function(surface, budget, file, profile_file)
    local text, data = atlas.dump_now(surface, budget, profile_file)
    if not text then return { ok = false, error = data } end
    helpers.write_file(file or "atlas/dump.txt", text)
    data.ok, data.bytes = true, #text
    return data
  end,
  -- a bridge message delivered locally (status, reply, ...), for UI work without a bridge
  -- live panels: the same calls Kira makes through kc.panel, plus the drop-down
  panels = function(with_view) return panels.list(with_view ~= false) end,
  panel_set = function(spec) return panels.set(spec) end,
  panel_input = function(name, key, value) return panels.set_input(name, key, value) end,
  panel_remove = function(name) return panels.remove(name) end,
  -- the library as the window uses it: the request that would go out, and an answer arriving
  library_request = function(op, slug) local m = protocol.library_request(op, slug) send(m) gui.refresh_all() return m end,
  -- the window's Stop on a tab's running turn
  stop = function(chat) local m = protocol.stop_request(chat) send(m) gui.refresh_all() return m end,
  library_result = function(msg) panels.on_library(msg) gui.refresh_all() return panels.library() end,
  library = function() return panels.library() end,
  panel_collapse = function(name, folded, index) panels.collapse(game.get_player(index or 1), name, folded) return true end,
  ui_inject = function(msg)
    for _, r in ipairs(protocol.handle(msg)) do send(r) end
    gui.refresh_all()
    return true
  end,
})

if script.active_mods["factorio-test"] then
  require("__factorio-test__/init")({ "tests/test-protocol", "tests/test-marks", "tests/test-gui", "tests/test-helpers", "tests/test-circuits", "tests/test-trial", "tests/test-pointers", "tests/test-panels", "tests/test-atlas" })
end
