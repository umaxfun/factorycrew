local protocol = require("scripts.protocol")
local gui = require("scripts.gui")
local mod_gui = require("mod-gui")

-- FactorioTest's headless run may have no LuaPlayer; then the GUI cannot be built and these
-- tests only report that (the window is then checked in the live game, level 3).
-- `game` does not exist while tests are being defined, so the player is looked up per test.
local player

local function find(el, name)
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find(c, name)
    if f then return f end
  end
end

local function texts(el, out)
  out = out or {}
  if el.type == "label" then
    local c = el.caption
    out[#out + 1] = type(c) == "table" and serpent.line(c) or tostring(c)
  end
  for _, c in pairs(el.children) do texts(c, out) end
  return out
end

local function joined(el) return table.concat(texts(el), "\n") end

local sent = {}
local function send(msg) sent[#sent + 1] = msg end

local function click(name, tags_chat)
  local root = player.gui.screen.kc_main
  local el
  if tags_chat then
    el = find(root, "kc_tab:" .. tags_chat)
  else
    el = find(root, name)
  end
  assert(el, "no element " .. tostring(name or tags_chat))
  gui.on_click({ element = el, player_index = player.index }, send)
end

local function confirm(name, text)
  local el = find(player.gui.screen.kc_main, name)
  assert(el, "no element " .. name)
  el.text = text
  gui.on_confirmed({ element = el, player_index = player.index }, send)
end

describe("gui", function()
  before_each(function()
    player = game.players[1]
    protocol.init_storage()
    storage.kc.tabs, storage.kc.tab_order, storage.kc.ui = {}, {}, {}
    storage.kc.last_pong_tick = nil -- earlier suites leave a pong behind
    protocol.unmark(nil, { all = true })
    sent = {}
    if player and player.gui.screen.kc_main then player.gui.screen.kc_main.destroy() end
  end)

  it("has a player to build the window for", function()
    assert(player and player.valid, "no LuaPlayer in this run: the window tests below did nothing")
  end)

  it("opens with a default tab, top-bar button and no-connection indicator", function()
    if not player then return end
    gui.ensure_button(player)
    local btn = mod_gui.get_button_flow(player).kc_toggle
    assert(btn and btn.type == "sprite-button" and btn.sprite == "kc-kira", "top-bar button is not Kira's face")
    gui.toggle(player)
    local root = player.gui.screen.kc_main
    assert(root, "window not opened")
    assert(storage.kc.tab_order[1] == "General")
    assert(find(root, "kc_tab:General"))
    local s = joined(root)
    assert(s:find("Kira is offline", 1, true) or s:find("UDP is off", 1, true), s)
    -- a pong turns the indicator green
    protocol.handle({ type = "pong", id = "p" })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("online", 1, true))
    gui.toggle(player)
    assert(not player.gui.screen.kc_main, "toggle did not close")
  end)

  it("sends the input as an ask into the selected tab and shows it", function()
    if not player then return end
    gui.toggle(player)
    confirm("kc_input", "  where is the jam?  ")
    assert(#sent == 1 and sent[1].type == "ask" and sent[1].chat == "General" and sent[1].text == "where is the jam?")
    local where = sent[1].player
    assert(where and where.surface == player.surface.name and type(where.x) == "number" and type(where.y) == "number",
      serpent.line(where))
    assert(find(player.gui.screen.kc_main, "kc_input").text == "")
    assert(joined(player.gui.screen.kc_main):find("where is the jam?", 1, true))
  end)

  it("shows thinking, then the reply, for the tab it belongs to", function()
    if not player then return end
    gui.toggle(player)
    protocol.handle({ type = "status", chat = "General", state = "thinking" })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("thinking", 1, true))
    protocol.handle({ type = "reply", chat = "General", text = "at the junction by the depot" })
    gui.refresh_all()
    local s = joined(player.gui.screen.kc_main)
    assert(s:find("at the junction by the depot", 1, true) and not s:find("thinking", 1, true), s)
  end)

  it("shows elapsed time, steps and activity while a long turn works", function()
    if not player then return end
    gui.toggle(player)
    protocol.handle({ type = "status", chat = "General", state = "thinking" })
    local tab = protocol.tab("General")
    tab.thinking_since = game.tick - 60 * 42 -- as if the turn started 42 s ago
    protocol.handle({ type = "status", chat = "General", state = "thinking", steps = 5,
                      activity = "placing 140 lamp ghosts" })
    gui.tick()
    local s = joined(player.gui.screen.kc_main)
    assert(s:find("thinking… 42 s · step 5", 1, true) and s:find("placing 140 lamp ghosts", 1, true), s)
    protocol.handle({ type = "reply", chat = "General", text = "done" })
    gui.refresh_all()
    s = joined(player.gui.screen.kc_main)
    assert(not s:find("step 5", 1, true) and not s:find("lamp ghosts", 1, true), s)
    assert(tab.thinking_since == nil and tab.steps == nil)
  end)

  it("shows the running turn step by step, then folds it into one line", function()
    if not player then return end
    gui.toggle(player)
    protocol.handle({ type = "status", chat = "General", state = "thinking" })
    protocol.handle({ type = "trace", chat = "General", kind = "text", text = "I'll plan a 32×32 logo first." })
    protocol.handle({ type = "trace", chat = "General", kind = "tool", text = "running a Lua query" })
    protocol.handle({ type = "trace", chat = "General", kind = "tool", text = "running a Lua query" })
    protocol.handle({ type = "trace", chat = "General", kind = "text", text = "88 ghosts placed, now the colors." })
    gui.refresh_all()
    local s = joined(player.gui.screen.kc_main)
    assert(s:find("I'll plan a 32×32 logo first.", 1, true), s)
    assert(s:find("› running a Lua query ×2", 1, true), s)
    assert(s:find("88 ghosts placed, now the colors.", 1, true), s)
    protocol.handle({ type = "status", chat = "General", state = "thinking", steps = 3 })
    protocol.handle({ type = "reply", chat = "General", text = "Done: KC logo." })
    gui.refresh_all()
    s = joined(player.gui.screen.kc_main)
    assert(not s:find("plan a 32×32", 1, true) and s:find("(worked: 3 steps", 1, true) and s:find("Done: KC logo.", 1, true), s)
    -- a trace for another run is ignored
    protocol.handle({ type = "trace", run_id = "someone-else", chat = "General", kind = "text", text = "stray" })
    assert(protocol.tab("General").trace == nil)
  end)

  it("shows the no-reply note after the idle timeout", function()
    if not player then return end
    gui.toggle(player)
    protocol.handle({ type = "status", chat = "General", state = "idle", note = "no_reply" })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("Kira did not answer", 1, true))
  end)

  it("adds, switches and closes tabs", function()
    if not player then return end
    gui.toggle(player)
    click("kc_tab_add")
    confirm("kc_new_tab_input", "Trains")
    assert(storage.kc.ui[player.index].tab == "Trains")
    confirm("kc_input", "q")
    assert(sent[#sent].chat == "Trains")
    click(nil, "General")
    assert(storage.kc.ui[player.index].tab == "General")
    click(nil, "Trains")
    click("kc_tab_close")
    assert(#storage.kc.tab_order == 1 and storage.kc.tab_order[1] == "General")
    -- reopening by name brings the feed back
    click("kc_tab_add")
    confirm("kc_new_tab_input", "Trains")
    assert(joined(player.gui.screen.kc_main):find("You:", 1, true))
  end)

  it("wraps many tabs into a grid of fixed-width buttons and cuts long names", function()
    if not player then return end
    gui.toggle(player)
    local names = { "Trains", "Power", "Oil processing", "Логистика нефти", "Main bus", "Defense", "Science",
                    "Rails", "Nauvis", "Vulcanus" }
    for _, n in ipairs(names) do
      click("kc_tab_add")
      confirm("kc_new_tab_input", n)
    end
    local root = player.gui.screen.kc_main
    local grid = find(root, "kc_tabs")
    assert(grid and grid.type == "table" and grid.column_count == 4, "tabs are not in a 4-column grid")
    assert(find(root, "kc_tabs_pane").type == "scroll-pane")
    local long = find(root, "kc_tab:Oil processing")
    assert(long.caption == "Oil proce…" and long.tooltip == "Oil processing", serpent.line(long.caption))
    local cyr = find(root, "kc_tab:Логистика нефти")
    assert(cyr.caption == "Логистика…", serpent.line(cyr.caption))
    assert(find(root, "kc_tab:Trains").caption == "Trains" and long.style.maximal_width == long.style.minimal_width)
    click(nil, "Oil processing")
    assert(storage.kc.ui[player.index].tab == "Oil processing")
    assert(find(root, "kc_tab_add") and find(root, "kc_tab_close"))
  end)

  it("renames the run", function()
    if not player then return end
    gui.toggle(player)
    click("kc_run_rename")
    confirm("kc_run_name_input", "Megabase 2")
    assert(storage.kc.run_name == "Megabase 2")
    assert(joined(player.gui.screen.kc_main):find("Megabase 2", 1, true))
  end)

  it("counts, walks and clears the tab's marks", function()
    if not player then return end
    gui.toggle(player)
    protocol.mark("General", { { position = { 10, 10 } }, { position = { 20, 20 } } })
    protocol.mark("other", { { position = { 30, 30 } } })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("Marks: 2", 1, true))
    click("kc_mark_next")
    assert(storage.kc.ui[player.index].mark == 1)
    click("kc_mark_next")
    click("kc_mark_next")
    assert(storage.kc.ui[player.index].mark == 1, "wraps around")
    click("kc_mark_prev")
    assert(storage.kc.ui[player.index].mark == 2)
    click("kc_mark_clear")
    assert(#protocol.chat_marks("General") == 0 and #protocol.chat_marks("other") == 1)
    assert(joined(player.gui.screen.kc_main):find("Marks: 0", 1, true))
  end)
end)
