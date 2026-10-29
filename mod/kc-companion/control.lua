local protocol = require("scripts.protocol")
local gui = require("scripts.gui")
local pointers = require("scripts.pointers")

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
end)

-- what changes the window when it arrives from the bridge
local REFRESH = { reply = true, status = true, ack = true, mark = true, unmark = true, trace = true }

script.on_event(defines.events.on_udp_packet_received, function(e)
  local msg = protocol.decode(e.payload)
  if not msg then return end
  local was_connected = protocol.connected()
  for _, reply in ipairs(protocol.handle(msg)) do
    send(reply)
  end
  if REFRESH[msg.type] or (msg.type == "pong" and not was_connected) then
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
  gui.refresh(player)
end)

script.on_event(defines.events.on_gui_click, function(e)
  gui.on_click(e, send)
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
  -- a bridge message delivered locally (status, reply, ...), for UI work without a bridge
  ui_inject = function(msg)
    for _, r in ipairs(protocol.handle(msg)) do send(r) end
    gui.refresh_all()
    return true
  end,
})

if script.active_mods["factorio-test"] then
  require("__factorio-test__/init")({ "tests/test-protocol", "tests/test-marks", "tests/test-gui", "tests/test-helpers", "tests/test-pointers" })
end
