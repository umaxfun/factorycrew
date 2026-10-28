local protocol = require("scripts.protocol")
local gui = require("scripts.gui")

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
    gui.toggle(player)
    local root = player.gui.screen.kc_main
    assert(root, "window not opened")
    assert(storage.kc.tab_order[1] == "Общий")
    assert(find(root, "kc_tab:Общий"))
    local s = joined(root)
    assert(s:find("нет связи с Кирой", 1, true) or s:find("UDP выключен", 1, true), s)
    -- a pong turns the indicator green
    protocol.handle({ type = "pong", id = "p" })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("на связи", 1, true))
    gui.toggle(player)
    assert(not player.gui.screen.kc_main, "toggle did not close")
  end)

  it("sends the input as an ask into the selected tab and shows it", function()
    if not player then return end
    gui.toggle(player)
    confirm("kc_input", "  где пробка?  ")
    assert(#sent == 1 and sent[1].type == "ask" and sent[1].chat == "Общий" and sent[1].text == "где пробка?")
    assert(find(player.gui.screen.kc_main, "kc_input").text == "")
    assert(joined(player.gui.screen.kc_main):find("где пробка?", 1, true))
  end)

  it("shows thinking, then the reply, for the tab it belongs to", function()
    if not player then return end
    gui.toggle(player)
    protocol.handle({ type = "status", chat = "Общий", state = "thinking" })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("думаю", 1, true))
    protocol.handle({ type = "reply", chat = "Общий", text = "на развязке у депо" })
    gui.refresh_all()
    local s = joined(player.gui.screen.kc_main)
    assert(s:find("на развязке у депо", 1, true) and not s:find("думаю", 1, true), s)
  end)

  it("shows the no-reply note after the idle timeout", function()
    if not player then return end
    gui.toggle(player)
    protocol.handle({ type = "status", chat = "Общий", state = "idle", note = "no_reply" })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("Кира не ответила", 1, true))
  end)

  it("adds, switches and closes tabs", function()
    if not player then return end
    gui.toggle(player)
    click("kc_tab_add")
    confirm("kc_new_tab_input", "Поезда")
    assert(storage.kc.ui[player.index].tab == "Поезда")
    confirm("kc_input", "q")
    assert(sent[#sent].chat == "Поезда")
    click(nil, "Общий")
    assert(storage.kc.ui[player.index].tab == "Общий")
    click(nil, "Поезда")
    click("kc_tab_close")
    assert(#storage.kc.tab_order == 1 and storage.kc.tab_order[1] == "Общий")
    -- reopening by name brings the feed back
    click("kc_tab_add")
    confirm("kc_new_tab_input", "Поезда")
    assert(joined(player.gui.screen.kc_main):find("Вы:", 1, true))
  end)

  it("renames the run", function()
    if not player then return end
    gui.toggle(player)
    click("kc_run_rename")
    confirm("kc_run_name_input", "Мегабаза-2")
    assert(storage.kc.run_name == "Мегабаза-2")
    assert(joined(player.gui.screen.kc_main):find("Мегабаза-2", 1, true))
  end)

  it("counts, walks and clears the tab's marks", function()
    if not player then return end
    gui.toggle(player)
    protocol.mark("Общий", { { position = { 10, 10 } }, { position = { 20, 20 } } })
    protocol.mark("other", { { position = { 30, 30 } } })
    gui.refresh_all()
    assert(joined(player.gui.screen.kc_main):find("Метки: 2", 1, true))
    click("kc_mark_next")
    assert(storage.kc.ui[player.index].mark == 1)
    click("kc_mark_next")
    click("kc_mark_next")
    assert(storage.kc.ui[player.index].mark == 1, "wraps around")
    click("kc_mark_prev")
    assert(storage.kc.ui[player.index].mark == 2)
    click("kc_mark_clear")
    assert(#protocol.chat_marks("Общий") == 0 and #protocol.chat_marks("other") == 1)
    assert(joined(player.gui.screen.kc_main):find("Метки: 0", 1, true))
  end)
end)
