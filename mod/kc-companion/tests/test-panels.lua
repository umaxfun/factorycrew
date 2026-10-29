local protocol = require("scripts.protocol")
local panels = require("scripts.panels")
local gui = require("scripts.gui")

local player

local function texts(el, out)
  out = out or {}
  if el.type == "label" then
    local c = el.caption
    out[#out + 1] = type(c) == "table" and serpent.line(c) or tostring(c)
  end
  for _, c in pairs(el.children) do texts(c, out) end
  return out
end

local function frame(name) return player and player.gui.left["kc_panel:" .. name] end
local function shown(name) local f = frame(name) return f and table.concat(texts(f), "\n") or "" end

local function due(name)
  local p = panels.get(name)
  p.last_tick = game.tick - p.every * 60 -- as if the interval passed
end

describe("panels", function()
  before_each(function()
    player = game.players[1]
    protocol.init_storage()
    for _, p in ipairs(panels.list(false)) do panels.remove(p.name) end
  end)

  it("runs the body once on create and draws a table with icons, numbers and bars", function()
    local r = panels.set{ name = "t1", title = "Test panel", every = 3, description = "What the test panel is about.", lua = [[
      return {
        "hello [color=green]world[/color]",
        {title = "Items", columns = {"", "item", "per min", "fill"},
         rows = {{{item = "iron-plate"}, "iron", 1234.5, {bar = 30, max = 120}},
                 {{item = "copper-plate"}, "copper", 12345, {bar = 0, max = 50, color = "red"}}}},
        {bar = 3, max = 4, caption = "3 of 4"},
      }]] }
    assert(r.ok, serpent.line(r))
    assert(r.view[2].rows[1][3] == 1234.5, serpent.line(r.view))
    local p = panels.get("t1")
    assert(p.every == 3 and p.refreshes == 1 and p.error == nil)
    if not player then return end
    local s = shown("t1")
    -- the description is for the saved artifact only; the panel in the game does not show it
    assert(not s:find("What the test panel is about.", 1, true) and panels.list()[1].description == "What the test panel is about.", s)
    assert(s:find("Test panel", 1, true) and s:find("1235", 1, true) and s:find("12.3k", 1, true), s)
    assert(s:find("3 of 4", 1, true) and s:find("30 / 120", 1, true), s)
  end)

  it("keeps a panel whose body fails and shows the error until a fix with the same name", function()
    local r = panels.set{ name = "bad", lua = "return {" }
    assert(not r.ok and r.error:find("compile", 1, true), serpent.line(r))
    assert(panels.get("bad"), "a failed panel is still kept")
    if player then assert(shown("bad"):find("data failed", 1, true)) end
    r = panels.set{ name = "bad", lua = "return {'fixed'}" }
    assert(r.ok and #panels.list() == 1, serpent.line(r))
    if player then
      local s = shown("bad")
      assert(s:find("fixed", 1, true) and not s:find("data failed", 1, true), s)
    end
  end)

  it("keeps the last good view on a runtime error and pauses after repeated errors", function()
    local r = panels.set{ name = "flaky", every = 1, lua = [[
      state.n = (state.n or 0) + 1
      if state.n > 1 then error("boom " .. state.n) end
      return {"good"}]] }
    assert(r.ok)
    for _ = 1, panels.MAX_ERRORS do
      due("flaky")
      panels.tick()
    end
    local p = panels.get("flaky")
    assert(p.paused and p.error:find("boom", 1, true) and p.view[1] == "good", serpent.line(p))
    local before = p.refreshes
    due("flaky")
    panels.tick()
    assert(panels.get("flaky").refreshes == before, "a paused panel must not refresh")
    if player then
      local s = shown("flaky")
      assert(s:find("good", 1, true) and s:find("paused", 1, true), s)
    end
  end)

  it("passes inputs to the body and refreshes at once when one changes", function()
    local r = panels.set{ name = "per", lua = "return {'period ' .. input.period}",
                          inputs = {{key = "period", label = "Period", options = {"1m", "10m", "1h"}, default = "10m"}} }
    assert(r.ok and r.view[1] == "period 10m", serpent.line(r))
    assert(panels.set_input("per", "period", "1h"))
    assert(panels.get("per").view[1] == "period 1h")
    -- replacing the panel keeps the player's choice when it is still offered
    panels.set{ name = "per", lua = "return {'p=' .. input.period}",
                inputs = {{key = "period", options = {"1m", "1h"}}} }
    assert(panels.get("per").view[1] == "p=1h", serpent.line(panels.get("per").view))
    if not player then return end
    local dd
    for _, c in pairs(frame("per").kc_panel_inner.kc_panel_inputs.children) do
      if c.type == "drop-down" then dd = c end
    end
    assert(dd and dd.items[dd.selected_index] == "1h")
    dd.selected_index = 1
    panels.on_selection({ element = dd, player_index = player.index })
    assert(shown("per"):find("p=1m", 1, true), shown("per"))
  end)

  it("refreshes by itself every `every` seconds, not before", function()
    panels.set{ name = "clock", every = 2, lua = "return {'tick ' .. game.tick}" }
    local p = panels.get("clock")
    local n = p.refreshes
    panels.tick()
    assert(p.refreshes == n, "refreshed before its interval")
    due("clock")
    panels.tick()
    assert(p.refreshes == n + 1)
  end)

  it("scrolls by default and refreshes in place, so the scroll position stays", function()
    panels.set{ name = "long", every = 1, lua = [[
      state.n = (state.n or 0) + 1
      local rows = {}
      for i = 1, 60 + state.n do rows[i] = {{item = "iron-plate"}, i * state.n} end
      return {"refresh " .. state.n, {columns = {"", "n"}, rows = rows}}]] }
    if not player then return end
    local body = frame("long").kc_panel_inner.kc_panel_body
    assert(body.type == "scroll-pane" and body.vertical_scroll_policy == "auto-and-reserve-space")
    local t1, l1 = body.children[2], body.children[1]
    assert(t1.type == "table" and #t1.children == 2 + 61 * 2, #t1.children)
    local first_cell = t1.children[3]
    due("long")
    panels.tick()
    local t2 = body.children[2]
    assert(t2.index == t1.index and body.children[1].index == l1.index, "refresh rebuilt the view instead of updating it")
    assert(t2.children[3].index == first_cell.index and #t2.children == 2 + 62 * 2, #t2.children)
    assert(body.children[1].caption == "refresh 2" and t2.children[4].caption == "2" and t2.children[6].caption == "4", t2.children[4].caption)
  end)

  it("collapses to the title bar and expands again with the data up to date", function()
    panels.set{ name = "fold", every = 1, lua = "state.n = (state.n or 0) + 1 return {'n=' .. state.n}" }
    if not player then return end
    assert(helpers.is_valid_sprite_path("utility/collapse") and helpers.is_valid_sprite_path("utility/expand"))
    local f = frame("fold")
    local btn = f.children[1].kc_panel_collapse
    assert(btn and btn.sprite == "utility/collapse")
    panels.on_click({ element = btn, player_index = player.index })
    assert(not f.kc_panel_inner.visible and btn.sprite == "utility/expand", "collapse did not fold the panel")
    due("fold")
    panels.tick() -- data keeps refreshing while folded
    assert(panels.get("fold").view[1] == "n=2")
    panels.on_click({ element = btn, player_index = player.index })
    assert(f.kc_panel_inner.visible and shown("fold"):find("n=2", 1, true), shown("fold"))
    -- replacing the panel keeps it folded
    panels.collapse(player, "fold", true)
    panels.set{ name = "fold", lua = "return {'v2'}" }
    assert(not frame("fold").kc_panel_inner.visible)
  end)

  it("draws link cells as buttons that open the map at their position", function()
    local r = panels.set{ name = "links", lua = [[
      return {{columns = {"item", "short", "chest"}, rows = {
        {{item = "iron-plate"}, {text = 9759, gps = {51, 33}, surface = "nauvis", color = "red"}, {gps = "[gps=-20,14,nauvis]"}},
        {{item = "copper-plate"}, {text = 5, gps = {x = 7.6, y = -3.2}}, {gps = {1, 2}, item = "iron-chest", text = "box"}},
      }}}]] }
    assert(r.ok, serpent.line(r))
    if not player then return end
    local buttons = {}
    local function walk(el)
      if el.type == "button" and el.tags.kc_gps then buttons[#buttons + 1] = el end
      for _, c in pairs(el.children) do walk(c) end
    end
    walk(frame("links"))
    assert(#buttons == 4, "link buttons: " .. #buttons)
    local caps = {}
    for _, b in ipairs(buttons) do caps[#caps + 1] = b.caption end
    local s = table.concat(caps, "|")
    assert(s == "9759|-20, 14|5|[item=iron-chest] box", s)
    assert(buttons[2].tags.kc_gps.x == -20 and buttons[2].tags.kc_gps.surface == "nauvis")
    -- a refresh keeps the buttons (in place) and their positions
    due("links")
    panels.tick()
    assert(buttons[1].valid and buttons[1].tags.kc_gps.x == 51)
    local before = player.physical_position
    panels.on_click({ element = buttons[1], player_index = player.index })
    local remote = player.controller_type == defines.controllers.remote
    local at = player.position
    if remote then player.exit_remote_view() end
    assert(remote, "the click did not open the map: controller " .. tostring(player.controller_type))
    assert(at.x == 51 and at.y == 33, serpent.line(at))
    assert(player.physical_position.x == before.x, "the character moved")
  end)

  it("lists, opens and deletes the run's saved panels from Kira's window, with no model", function()
    storage.kc.library = nil
    local out = {}
    local function send(m) out[#out + 1] = m end
    if not player then return end
    if not player.gui.screen.kc_main then gui.toggle(player) end
    local function find(el, n)
      if el.name == n then return el end
      for _, c in pairs(el.children) do local f = find(c, n) if f then return f end end
    end
    local function click(n)
      local el = find(player.gui.screen.kc_main, n) or (player.gui.left.kc_library and find(player.gui.left.kc_library, n))
      assert(el, "no element " .. n)
      gui.on_click({ element = el, player_index = player.index }, send)
    end
    local function text() return player.gui.left.kc_library and table.concat(texts(player.gui.left.kc_library), "\n") or "" end
    click("kc_library_toggle")
    assert(#out == 1 and out[1].type == "library" and out[1].op == "list" and out[1].run_id == storage.kc.run_id, serpent.line(out))
    assert(text():find("loading", 1, true), text())
    local SLUG = "factorio-panel-x-science"
    panels.on_library({ id = out[1].id, ok = true, panels = {{ slug = SLUG, title = "Science packs made" }} })
    gui.refresh(player)
    local s = text()
    assert(not s:find("loading", 1, true) and find(player.gui.left.kc_library, "kc_lib_open:" .. SLUG), s)
    -- open: the saved spec arrives and becomes a live panel
    click("kc_lib_open:" .. SLUG)
    assert(out[2].op == "open" and out[2].slug == SLUG)
    panels.on_library({ id = out[2].id, ok = true, spec = { name = "science", title = "Science packs made", lua = "return {'saved'}" } })
    assert(panels.get("science") and frame("science") and shown("science"):find("saved", 1, true))
    -- delete takes two clicks
    click("kc_lib_del:" .. SLUG)
    assert(#out == 2, "the first click only asks to confirm")
    click("kc_lib_del:" .. SLUG)
    assert(out[3].op == "delete" and out[3].slug == SLUG)
    panels.on_library({ id = out[3].id, ok = true, panels = {} })
    gui.refresh(player)
    assert(text():find("Nothing saved yet", 1, true), text())
    -- no answer in time
    click("kc_library_toggle") click("kc_library_toggle")
    storage.kc.library.pending[out[#out].id].tick = game.tick - panels.LIBRARY_TIMEOUT_TICKS - 1
    assert(panels.library_tick())
    gui.refresh(player)
    assert(text():find("did not answer", 1, true), text())
    click("kc_library_close")
    assert(not player.gui.left.kc_library, "the library frame did not close")
    gui.toggle(player)
  end)

  it("hides on close, shows again from the toggle, and removes the window", function()
    panels.set{ name = "h", lua = "return {'x'}" }
    if player then
      assert(frame("h"))
      panels.show(player, "h", false)
      assert(not frame("h"))
      panels.show(player, "h", true)
      assert(frame("h"))
    end
    assert(panels.remove("h").ok)
    assert(not frame("h") and #panels.list() == 0)
  end)

  it("is available to Kira's queries as kc.panel", function()
    local r = protocol.run_query("return kc.panel{name = 'q', lua = 'return {kc.fmt(2500)}'}")
    assert(r.ok and r.data.ok and r.data.view[1] == "2500", serpent.line(r))
    local l = protocol.run_query("return kc.panels()")
    assert(l.ok and l.data[1].name == "q", serpent.line(l))
  end)

  it("reads production statistics by period (the science panel's data)", function()
    local stats = game.forces.player.get_item_production_statistics(game.surfaces[1])
    stats.on_flow("automation-science-pack", 120)
    local r = panels.set{ name = "sci", lua = [[
      local idx = defines.flow_precision_index[input.period]
      local st = game.forces.player.get_item_production_statistics(game.surfaces[1])
      return {{rows = {{{item = "automation-science-pack"},
        st.get_flow_count{name = "automation-science-pack", category = "input", precision_index = idx, count = true}}}}}]],
      inputs = {{key = "period", options = {"one_minute", "ten_minutes"}}} }
    assert(r.ok, serpent.line(r))
    assert(r.view[1].rows[1][2] >= 0, serpent.line(r.view)) -- the flow lands in the stats on the next sample
  end)
end)
