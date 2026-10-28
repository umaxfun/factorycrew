local protocol = require("scripts.protocol")

local function kc_tags()
  local n = 0
  for _, t in ipairs(game.forces.player.find_chart_tags(game.surfaces[1])) do
    if string.sub(t.text, 1, #protocol.MARK_PREFIX) == protocol.MARK_PREFIX then n = n + 1 end
  end
  return n
end

local function all_tags()
  return #game.forces.player.find_chart_tags(game.surfaces[1])
end

before_each(function()
  protocol.init_storage()
  protocol.unmark(nil, { all = true })
end)

describe("mark / unmark / list_marks", function()
  it("places tags and remembers them per chat", function()
    local r = protocol.mark("Logistics", {
      { surface = "nauvis", position = { x = 10, y = 20 }, text = "no filter" },
      { position = { 30, 40 }, text = "another" },
    })
    assert(#r.ids == 2 and #r.failed == 0)
    local list = protocol.list_marks("Logistics")
    assert(#list == 2)
    assert(list[1].text == "[KC] no filter" and list[1].surface == "nauvis")
    assert(kc_tags() == 2)
  end)

  it("reports what could not be placed and why", function()
    local r = protocol.mark("x", {
      { surface = "no-such-planet", position = { 0, 0 } },
      { position = { x = "a" } },
      { position = { 1, 1 }, icon = "no-such-item" },
      { position = { 2, 2 } },
    })
    assert(#r.ids == 1)
    assert(r.failed[1].index == 1 and r.failed[1].reason == "no_such_surface")
    assert(r.failed[2].index == 2 and r.failed[2].reason == "bad_position")
    assert(r.failed[3].index == 3, serpent.line(r.failed))
  end)

  it("removes by ids only within the chat", function()
    local a = protocol.mark("a", { { position = { 0, 0 } }, { position = { 5, 5 } } })
    local b = protocol.mark("b", { { position = { 9, 9 } } })
    assert(protocol.unmark("a", { ids = { a.ids[1], b.ids[1] } }).removed == 1)
    assert(#protocol.list_marks("a") == 1 and #protocol.list_marks("b") == 1)
  end)

  it("removes all of a chat, or all, and leaves player tags alone", function()
    local own = game.forces.player.add_chart_tag(game.surfaces[1], { position = { 100, 100 }, text = "my base" })
    protocol.mark("a", { { position = { 0, 0 } }, { position = { 5, 5 } } })
    protocol.mark("b", { { position = { 9, 9 } } })
    assert(protocol.unmark("a", { all_in_chat = true }).removed == 2)
    assert(#protocol.list_marks() == 1)
    assert(protocol.unmark(nil, { all = true }).removed == 1)
    assert(kc_tags() == 0 and all_tags() == 1 and own.valid)
    own.destroy()
  end)

  it("forgets a tag the player deleted on the map", function()
    local r = protocol.mark("a", { { position = { 0, 0 } }, { position = { 5, 5 } } })
    local tag = storage.kc.marks["a"][r.ids[1]].tag
    tag.destroy() -- whether or not on_chart_tag_removed fires for script destroys, list must not show it
    local list = protocol.list_marks("a")
    assert(#list == 1 and list[1].id == r.ids[2])
    assert(storage.kc.mark_chat[r.ids[1]] == nil)
  end)
end)

describe("tabs, ask, ack, reply, status", function()
  it("records an ask and builds the bridge message", function()
    local msg = protocol.ask("Trains", "where is the jam?")
    assert(msg.type == "ask" and msg.chat == "Trains" and msg.run_id == storage.kc.run_id)
    local feed = storage.kc.tabs["Trains"].feed
    assert(feed[#feed].text == "where is the jam?" and feed[#feed].acked == false)
  end)

  it("marks the ask acknowledged on ack", function()
    local msg = protocol.ask("Trains", "q")
    protocol.handle({ type = "ack", id = msg.id })
    local feed = storage.kc.tabs["Trains"].feed
    assert(feed[#feed].acked == true)
  end)

  it("appends a reply and clears thinking", function()
    protocol.handle({ type = "status", chat = "Trains", state = "thinking", run_id = storage.kc.run_id })
    assert(storage.kc.tabs["Trains"].state == "thinking")
    protocol.handle({ type = "reply", chat = "Trains", text = "at the junction", id = "r1", run_id = storage.kc.run_id })
    local t = storage.kc.tabs["Trains"]
    assert(t.state == "idle" and t.feed[#t.feed].from == "kira" and t.feed[#t.feed].text == "at the junction")
  end)

  it("ignores a reply meant for another run", function()
    local before = #protocol.tab("Trains").feed
    protocol.handle({ type = "reply", chat = "Trains", text = "from another run", run_id = "other-run" })
    assert(#protocol.tab("Trains").feed == before)
  end)

  it("keeps the feed at FEED_LIMIT", function()
    for i = 1, protocol.FEED_LIMIT + 5 do protocol.ask("long", "q" .. i) end
    local feed = storage.kc.tabs["long"].feed
    assert(#feed == protocol.FEED_LIMIT and feed[#feed].text == "q" .. (protocol.FEED_LIMIT + 5))
  end)

  it("handles mark/unmark/list_marks messages with a result", function()
    local r = protocol.handle({ type = "mark", id = "m1", chat = "c", marks = { { position = { 1, 2 } } } })
    assert(r[1].type == "result" and r[1].id == "m1" and #r[1].data.ids == 1)
    local l = protocol.handle({ type = "list_marks", id = "l1", chat = "c" })
    assert(#l[1].data == 1)
    local u = protocol.handle({ type = "unmark", id = "u1", chat = "c", all_in_chat = true })
    assert(u[1].data.removed == 1)
  end)
end)

describe("framing with non-ASCII text", function()
  it("does not split UTF-8 sequences across parts", function()
    local text = string.rep("ж", 6000) -- Cyrillic on purpose: 12000 bytes, odd chunk boundaries would cut a letter
    local packets = protocol.encode({ type = "reply", id = "u8", text = text })
    assert(#packets > 1)
    local msg
    for _, p in ipairs(packets) do msg = protocol.decode(p) or msg end
    assert(msg and msg.text == text)
  end)
end)
