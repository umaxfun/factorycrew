local protocol = require("scripts.protocol")

-- recv_udp/send_udp raise when the game was not started with --enable-lua-udp
-- (e.g. under FactorioTest). Probe once per load and stay quiet afterwards.
local udp_ok = nil

local function send(msg)
  if not udp_ok then return end
  for _, packet in ipairs(protocol.encode(msg)) do
    helpers.send_udp(protocol.BRIDGE_PORT, packet)
  end
end

script.on_init(function()
  protocol.init_storage()
end)

script.on_configuration_changed(function()
  protocol.init_storage()
end)

script.on_nth_tick(10, function()
  if udp_ok == false then return end
  local ok = pcall(helpers.recv_udp)
  if udp_ok == nil then
    udp_ok = ok
    if ok then send(protocol.heartbeat("hello")) end
  end
end)

script.on_nth_tick(protocol.HEARTBEAT_TICKS, function()
  send(protocol.heartbeat())
end)

script.on_event(defines.events.on_udp_packet_received, function(e)
  local msg = protocol.decode(e.payload)
  if not msg then return end
  for _, reply in ipairs(protocol.handle(msg)) do
    send(reply)
  end
end)

if script.active_mods["factorio-test"] then
  require("__factorio-test__/init")({ "tests/test-protocol" })
end
