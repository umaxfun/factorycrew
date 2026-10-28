local protocol = require("scripts.protocol")

describe("run_id", function()
  it("is minted once and stays stable", function()
    protocol.init_storage()
    local first = storage.kc.run_id
    assert(type(first) == "string" and #first > 0)
    protocol.init_storage()
    assert(storage.kc.run_id == first)
  end)
end)

describe("run_query", function()
  it("returns what the body returns", function()
    local r = protocol.run_query("return { name = surface.name, n = 2 + 2 }")
    assert(r.ok, r.message)
    assert(r.data.name == "nauvis")
    assert(r.data.n == 4)
  end)

  it("honours an explicit surface", function()
    local r = protocol.run_query("return surface.name", "nauvis")
    assert(r.ok and r.data == "nauvis")
  end)

  it("reports an unknown surface", function()
    local r = protocol.run_query("return 1", "no-such-planet")
    assert(not r.ok and r.error == "no_such_surface")
  end)

  it("reports a compile error", function()
    local r = protocol.run_query("return (")
    assert(not r.ok and r.error == "compile_error")
  end)

  it("reports a runtime error", function()
    local r = protocol.run_query("return nosuch.field")
    assert(not r.ok and r.error == "runtime_error")
    assert(string.find(r.message, "nosuch", 1, true))
  end)

  it("refuses a result over 64 KB", function()
    local r = protocol.run_query("local t = {} for i = 1, 20000 do t[i] = 'xxxxx' end return t")
    assert(not r.ok and r.error == "result_too_large", r.error)
    assert(r.size > protocol.MAX_RESULT)
  end)

  it("does not crash on a LuaObject result", function()
    local r = protocol.run_query("return surface")
    -- Either encoded somehow or refused; it must never raise.
    assert(r.ok or r.error == "not_serializable")
  end)
end)

describe("framing", function()
  it("keeps small messages in one packet", function()
    local packets = protocol.encode({ type = "result", id = "s1", ok = true, data = "hi" })
    assert(#packets == 1)
    local msg = protocol.decode(packets[1])
    assert(msg.id == "s1" and msg.data == "hi")
  end)

  it("splits and reassembles a large message, with quotes to escape", function()
    local big = {}
    for i = 1, 3000 do big[i] = 'a"b\\c' .. i end
    local packets = protocol.encode({ type = "result", id = "big1", ok = true, data = big })
    assert(#packets > 1, "expected several packets")
    for _, p in ipairs(packets) do
      assert(#p <= 2 * protocol.CHUNK_RAW + 256, "packet too large: " .. #p)
    end
    local msg
    for i = #packets, 1, -1 do -- out of order on purpose
      msg = protocol.decode(packets[i]) or msg
    end
    assert(msg and msg.id == "big1")
    assert(#msg.data == 3000 and msg.data[3000] == 'a"b\\c3000')
  end)
end)

describe("chart tags (game behaviour the mod relies on)", function()
  it("can be placed on an uncharted chunk", function()
    local s, f = game.surfaces[1], game.forces.player
    assert(not f.is_chunk_charted(s, { 50000 / 32, 50000 / 32 }))
    local tag = f.add_chart_tag(s, { position = { 50000, 50000 }, text = "[KC] probe" })
    assert(tag ~= nil, "add_chart_tag returned nil on an uncharted chunk")
    tag.destroy()
  end)
end)

describe("handle", function()
  it("answers a query with a result carrying the same id", function()
    local replies = protocol.handle({ type = "query", id = "q7", lua = "return 42" })
    assert(#replies == 1)
    assert(replies[1].type == "result" and replies[1].id == "q7" and replies[1].data == 42)
  end)

  it("tracks pong for the connection indicator", function()
    storage.kc.last_pong_tick = nil
    assert(not protocol.connected())
    protocol.handle({ type = "pong", id = "p1" })
    assert(protocol.connected())
  end)
end)
