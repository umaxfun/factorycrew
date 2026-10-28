-- In-game window (SPEC.md, "Окно в игре"). State lives in storage.kc (protocol.lua);
-- per-player view state (selected tab, mark cursor) in storage.kc.ui[player_index].

local protocol = require("scripts.protocol")
local mod_gui = require("mod-gui")

local M = {}

local ROOT = "kc_main"
local DEFAULT_TAB = "Общий"
local WIDTH = 460

local function view(player)
  local kc = storage.kc
  kc.ui = kc.ui or {}
  local v = kc.ui[player.index]
  if not v then
    v = { tab = nil, mark = 0 }
    kc.ui[player.index] = v
  end
  local order = kc.tab_order
  local present = false
  for _, c in ipairs(order) do
    if c == v.tab then present = true break end
  end
  if not present then
    if #order == 0 then protocol.tab(DEFAULT_TAB) end
    v.tab = order[1]
    v.mark = 0
  end
  return v
end

local function root(player)
  return player.gui.screen[ROOT]
end

local function find(el, name)
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find(c, name)
    if f then return f end
  end
  return nil
end

-- ── top-bar button ───────────────────────────────────────────────────────

function M.ensure_button(player)
  local flow = mod_gui.get_button_flow(player)
  if not flow.kc_toggle then
    flow.add{ type = "button", name = "kc_toggle", caption = "Кира", tooltip = "Окно Киры (Ctrl+Shift+K)",
              style = mod_gui.button_style }
  end
end

-- ── build ────────────────────────────────────────────────────────────────

local function build(player)
  local frame = player.gui.screen.add{ type = "frame", name = ROOT, direction = "vertical" }
  frame.auto_center = true

  local bar = frame.add{ type = "flow", name = "kc_titlebar", direction = "horizontal" }
  bar.drag_target = frame
  bar.add{ type = "label", caption = "Кира", style = "frame_title", ignored_by_interaction = true }
  local grip = bar.add{ type = "empty-widget", style = "draggable_space_header", ignored_by_interaction = true }
  grip.style.horizontally_stretchable = true
  grip.style.height = 24
  bar.add{ type = "sprite-button", name = "kc_close", sprite = "utility/close", style = "frame_action_button",
           tooltip = "Закрыть (Ctrl+Shift+K)" }

  local inner = frame.add{ type = "frame", name = "kc_inner", direction = "vertical", style = "inside_shallow_frame_with_padding" }
  inner.style.width = WIDTH

  inner.add{ type = "flow", name = "kc_run_row", direction = "horizontal" }
  inner.add{ type = "flow", name = "kc_tabs_row", direction = "horizontal" }
  inner.add{ type = "flow", name = "kc_status_row", direction = "horizontal" }

  local feed = inner.add{ type = "scroll-pane", name = "kc_feed", direction = "vertical",
                          horizontal_scroll_policy = "never" }
  feed.style.height = 300
  feed.style.horizontally_stretchable = true

  inner.add{ type = "flow", name = "kc_marks_row", direction = "horizontal" }

  local input_row = inner.add{ type = "flow", name = "kc_input_row", direction = "horizontal" }
  local field = input_row.add{ type = "textfield", name = "kc_input", lose_focus_on_confirm = false,
                               tooltip = "Enter — отправить" }
  field.style.horizontally_stretchable = true
  field.style.maximal_width = 0
  input_row.add{ type = "button", name = "kc_send", caption = "Отправить" }
  return frame
end

-- ── refresh parts ────────────────────────────────────────────────────────

local function refresh_run(player, frame)
  local row = find(frame, "kc_run_row")
  row.clear()
  local v = view(player)
  if v.renaming then
    local f = row.add{ type = "textfield", name = "kc_run_name_input", text = storage.kc.run_name,
                       tooltip = "Enter — сохранить" }
    f.style.horizontally_stretchable = true
    f.focus()
  else
    row.add{ type = "label", caption = { "", "[font=default-bold]Ран:[/font] ", storage.kc.run_name } }
    row.add{ type = "button", name = "kc_run_rename", caption = "✎", tooltip = "Переименовать ран",
             style = "mini_button" }
  end
end

local function refresh_tabs(player, frame)
  local row = find(frame, "kc_tabs_row")
  row.clear()
  local v = view(player)
  for _, chat in ipairs(storage.kc.tab_order) do
    local selected = chat == v.tab
    local caption = selected and ("[font=default-bold]▸ " .. chat .. "[/font]") or chat
    local b = row.add{ type = "button", name = "kc_tab:" .. chat, caption = caption, tags = { kc_tab = chat } }
    b.style.minimal_width = 0
    b.style.horizontal_align = "center"
  end
  if v.adding then
    local f = row.add{ type = "textfield", name = "kc_new_tab_input", tooltip = "Имя вкладки, Enter — создать" }
    f.style.width = 120
    f.focus()
  else
    row.add{ type = "button", name = "kc_tab_add", caption = "+", tooltip = "Новая вкладка", style = "mini_button" }
  end
  row.add{ type = "button", name = "kc_tab_close", caption = "✕",
           tooltip = "Закрыть вкладку (чат в Kiro Crew останется)", style = "mini_button" }
end

local function status_text(tab)
  local parts = {}
  if protocol.udp_state == "off" then
    parts[#parts + 1] = "[color=red]UDP выключен: запустите игру с --enable-lua-udp 34200[/color]"
  elseif not protocol.connected() then
    parts[#parts + 1] = "[color=red]нет связи с Кирой[/color]"
  else
    parts[#parts + 1] = "[color=green]на связи[/color]"
  end
  if tab.state == "thinking" then
    parts[#parts + 1] = "[color=yellow]думаю…[/color]"
  elseif tab.note == "no_reply" then
    parts[#parts + 1] = "[color=orange]Кира не ответила, ответ может прийти в чат[/color]"
  end
  return table.concat(parts, "   ")
end

local function refresh_status(player, frame)
  local row = find(frame, "kc_status_row")
  row.clear()
  row.add{ type = "label", name = "kc_status", caption = status_text(protocol.tab(view(player).tab)) }
end

local function refresh_feed(player, frame)
  local pane = find(frame, "kc_feed")
  pane.clear()
  local tab = protocol.tab(view(player).tab)
  local now = game.tick
  for _, e in ipairs(tab.feed) do
    local who
    if e.from == "player" then
      local warn = ""
      if not e.acked and now - e.tick > protocol.ACK_TIMEOUT_TICKS then
        warn = " [color=red](нет связи, вопрос не ушёл)[/color]"
      end
      who = "[font=default-bold][color=0.6,0.8,1]Вы:[/color][/font] " .. e.text .. warn
    else
      who = "[font=default-bold][color=1,0.8,0.4]Кира:[/color][/font] " .. e.text
    end
    local l = pane.add{ type = "label", caption = who }
    l.style.single_line = false
    l.style.maximal_width = WIDTH - 40
  end
  if #tab.feed == 0 then
    pane.add{ type = "label", caption = "[color=gray]Спросите что-нибудь про этот ран.[/color]" }
  end
  pane.scroll_to_bottom()
end

local function refresh_marks(player, frame)
  local row = find(frame, "kc_marks_row")
  row.clear()
  local v = view(player)
  local marks = protocol.chat_marks(v.tab)
  if v.mark > #marks then v.mark = #marks end
  local where = v.mark > 0 and (" (" .. v.mark .. "/" .. #marks .. ")") or ""
  row.add{ type = "label", caption = "Метки: " .. #marks .. where }
  local filler = row.add{ type = "empty-widget" }
  filler.style.horizontally_stretchable = true
  local enabled = #marks > 0
  row.add{ type = "button", name = "kc_mark_prev", caption = "◀", tooltip = "К предыдущей метке",
           style = "mini_button", enabled = enabled }
  row.add{ type = "button", name = "kc_mark_next", caption = "▶", tooltip = "К следующей метке",
           style = "mini_button", enabled = enabled }
  row.add{ type = "button", name = "kc_mark_clear", caption = "Убрать", tooltip = "Убрать все метки этой вкладки",
           enabled = enabled }
end

function M.refresh(player)
  local frame = root(player)
  if not frame then return end
  refresh_run(player, frame)
  refresh_tabs(player, frame)
  refresh_status(player, frame)
  refresh_feed(player, frame)
  refresh_marks(player, frame)
end

function M.refresh_all()
  for _, p in pairs(game.connected_players) do M.refresh(p) end
end

--- Cheap periodic update: connection indicator and unacked warnings only.
function M.tick()
  for _, p in pairs(game.connected_players) do
    local frame = root(p)
    if frame then
      refresh_status(p, frame)
      local tab = protocol.tab(view(p).tab)
      local last = tab.feed[#tab.feed]
      if last and last.from == "player" and not last.acked then refresh_feed(p, frame) end
    end
  end
end

-- ── actions ──────────────────────────────────────────────────────────────

function M.toggle(player)
  local frame = root(player)
  if frame then
    frame.destroy()
    return
  end
  view(player)
  build(player)
  M.refresh(player)
  find(root(player), "kc_input").focus()
end

--- Send the input field's text as a question. `send` puts a message on the wire.
local function submit(player, send)
  local frame = root(player)
  local field = find(frame, "kc_input")
  local text = field.text:gsub("^%s+", ""):gsub("%s+$", "")
  if text == "" then return end
  field.text = ""
  send(protocol.ask(view(player).tab, text))
  M.refresh(player)
  field.focus()
end

local function go_to_mark(player, step)
  local v = view(player)
  local marks = protocol.chat_marks(v.tab)
  if #marks == 0 then return end
  v.mark = ((v.mark - 1 + step) % #marks) + 1
  local m = marks[v.mark]
  -- remote view (the map) centred on the mark; Esc returns to the character
  pcall(player.set_controller, { type = defines.controllers.remote, position = m.position, surface = m.surface })
  M.refresh(player)
end

function M.on_click(e, send)
  local el = e.element
  if not (el and el.valid) then return false end
  local name = el.name
  if not name:find("^kc_") then return false end
  local player = game.get_player(e.player_index)
  local v = view(player)
  if name == "kc_toggle" or name == "kc_close" then
    M.toggle(player)
  elseif name == "kc_send" then
    submit(player, send)
  elseif el.tags and el.tags.kc_tab then
    v.tab, v.mark, v.adding = el.tags.kc_tab, 0, false
    M.refresh(player)
  elseif name == "kc_tab_add" then
    v.adding = true
    M.refresh(player)
  elseif name == "kc_tab_close" then
    protocol.close_tab(v.tab)
    v.tab = nil
    M.refresh(player)
  elseif name == "kc_run_rename" then
    v.renaming = true
    M.refresh(player)
  elseif name == "kc_mark_prev" then
    go_to_mark(player, -1)
  elseif name == "kc_mark_next" then
    go_to_mark(player, 1)
  elseif name == "kc_mark_clear" then
    protocol.unmark(v.tab, { all_in_chat = true })
    v.mark = 0
    M.refresh_all()
  end
  return true
end

function M.on_confirmed(e, send)
  local el = e.element
  if not (el and el.valid) then return end
  local player = game.get_player(e.player_index)
  local v = view(player)
  if el.name == "kc_input" then
    submit(player, send)
  elseif el.name == "kc_new_tab_input" then
    local name = el.text:gsub("^%s+", ""):gsub("%s+$", "")
    v.adding = false
    if name ~= "" then
      protocol.tab(name)
      v.tab, v.mark = name, 0
    end
    M.refresh(player)
  elseif el.name == "kc_run_name_input" then
    local name = el.text:gsub("^%s+", ""):gsub("%s+$", "")
    v.renaming = false
    if name ~= "" then storage.kc.run_name = name end
    M.refresh_all()
  end
end

return M
