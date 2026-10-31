-- In-game window (SPEC.md, "In-game window"). State lives in storage.kc (protocol.lua);
-- per-player view state (selected tab, mark cursor) in storage.kc.ui[player_index].

local protocol = require("scripts.protocol")
local pointers = require("scripts.pointers")
local mod_gui = require("mod-gui")

local M = {}

local ROOT = "kc_main"
local DEFAULT_TAB = "General"
local WIDTH = 460
local TRACE_COLOR = "[color=0.62,0.62,0.62]" -- live steps of a turn: quieter than the answer
local QUIET_SECONDS = 60 -- a running turn without a new step this long says so in the status line

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
  -- the first version had a text button; swap it for the face
  if flow.kc_toggle and flow.kc_toggle.type ~= "sprite-button" then flow.kc_toggle.destroy() end
  if not flow.kc_toggle then
    flow.add{ type = "sprite-button", name = "kc_toggle", sprite = "kc-kira", tooltip = "Kira (Ctrl+Shift+K)",
              style = mod_gui.button_style }
  end
end

-- ── build ────────────────────────────────────────────────────────────────

local function build(player)
  local frame = player.gui.screen.add{ type = "frame", name = ROOT, direction = "vertical" }
  frame.auto_center = true

  local bar = frame.add{ type = "flow", name = "kc_titlebar", direction = "horizontal" }
  bar.drag_target = frame
  local face = bar.add{ type = "sprite", sprite = "kc-kira", resize_to_sprite = false, ignored_by_interaction = true }
  face.style.width = 24
  face.style.height = 24
  face.style.stretch_image_to_widget_size = true
  bar.add{ type = "label", caption = "Kira", style = "frame_title", ignored_by_interaction = true }
  local grip = bar.add{ type = "empty-widget", style = "draggable_space_header", ignored_by_interaction = true }
  grip.style.horizontally_stretchable = true
  grip.style.height = 24
  bar.add{ type = "sprite-button", name = "kc_close", sprite = "utility/close", style = "frame_action_button",
           tooltip = "Close (Ctrl+Shift+K)" }

  local inner = frame.add{ type = "frame", name = "kc_inner", direction = "vertical", style = "inside_shallow_frame_with_padding" }
  inner.style.width = WIDTH

  local kc_run_row = inner.add{ type = "flow", name = "kc_run_row", direction = "horizontal" }
  kc_run_row.style.vertical_align = "center"
  local kc_tabs_row = inner.add{ type = "flow", name = "kc_tabs_row", direction = "horizontal" }
  kc_tabs_row.style.vertical_align = "top"
  local kc_status_row = inner.add{ type = "flow", name = "kc_status_row", direction = "horizontal" }
  kc_status_row.style.vertical_align = "center"

  local feed = inner.add{ type = "scroll-pane", name = "kc_feed", direction = "vertical",
                          horizontal_scroll_policy = "never" }
  feed.style.height = 300
  feed.style.horizontally_stretchable = true

  local kc_marks_row = inner.add{ type = "flow", name = "kc_marks_row", direction = "horizontal" }
  kc_marks_row.style.vertical_align = "center"

  local kc_panels_row = inner.add{ type = "flow", name = "kc_panels_row", direction = "horizontal" }
  kc_panels_row.style.vertical_align = "center"

  local kc_pointers_row = inner.add{ type = "flow", name = "kc_pointers_row", direction = "vertical" }
  kc_pointers_row.style.vertical_spacing = 0

  local input_row = inner.add{ type = "flow", name = "kc_input_row", direction = "horizontal" }
  input_row.style.vertical_align = "center"
  local field = input_row.add{ type = "textfield", name = "kc_input", lose_focus_on_confirm = false,
                               tooltip = "Enter to send" }
  field.style.horizontally_stretchable = true
  field.style.maximal_width = 0
  input_row.add{ type = "sprite-button", name = "kc_point", sprite = "item/kc-pointer", style = "tool_button",
                 tooltip = "Point (Alt+K): click a thing or drag a box, then mention it as @1, @2 in the question. "
                        .. "Shift-drag removes pointers." }
  input_row.add{ type = "button", name = "kc_send", caption = "Send" }
  return frame
end

-- ── refresh parts ────────────────────────────────────────────────────────

local function tool(parent, name, sprite, tooltip, enabled, style)
  return parent.add{ type = "sprite-button", name = name, sprite = sprite, tooltip = tooltip,
                     style = style or "tool_button", enabled = enabled ~= false }
end

local function refresh_run(player, frame)
  local row = find(frame, "kc_run_row")
  row.clear()
  local v = view(player)
  if v.renaming then
    local f = row.add{ type = "textfield", name = "kc_run_name_input", text = storage.kc.run_name,
                       tooltip = "Enter to save" }
    f.style.horizontally_stretchable = true
    f.focus()
  else
    row.add{ type = "label", caption = { "", "[font=default-bold]Run:[/font] ", storage.kc.run_name } }
    tool(row, "kc_run_rename", "utility/rename_icon", "Rename the run")
  end
end

-- Tabs wrap into a grid of fixed-width buttons, so any number of tabs stays inside the window:
-- a flow of buttons sized to their captions pushed the whole window off the screen.
local TAB_COLUMNS = 4
local TAB_WIDTH = 90
local TAB_CHARS = 9 -- longer names are cut to this many characters + "…"; the tooltip has the full name
local TAB_ROWS_SHOWN = 3 -- more rows scroll

--- First `n` characters of a UTF-8 string (Factorio's Lua has no utf8 library).
local function utf8_head(s, n)
  local out, count = {}, 0
  for ch in s:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
    count = count + 1
    if count > n then return table.concat(out), true end
    out[count] = ch
  end
  return s, false
end

local function refresh_tabs(player, frame)
  local row = find(frame, "kc_tabs_row")
  row.clear()
  local v = view(player)
  local pane = row.add{ type = "scroll-pane", name = "kc_tabs_pane", horizontal_scroll_policy = "never",
                        vertical_scroll_policy = "auto" }
  pane.style.horizontally_stretchable = true
  -- rows of 28 px buttons with 4 px gaps, plus the pane's own border and padding
  pane.style.maximal_height = TAB_ROWS_SHOWN * 28 + (TAB_ROWS_SHOWN - 1) * 4 + 12
  local grid = pane.add{ type = "table", name = "kc_tabs", column_count = TAB_COLUMNS }
  grid.style.horizontal_spacing = 4
  grid.style.vertical_spacing = 4
  local selected
  for _, chat in ipairs(storage.kc.tab_order) do
    local head, cut = utf8_head(chat, TAB_CHARS)
    local b = grid.add{ type = "button", name = "kc_tab:" .. chat, caption = cut and (head .. "…") or chat,
                        tooltip = cut and chat or nil, tags = { kc_tab = chat }, toggled = chat == v.tab }
    b.style.width = TAB_WIDTH
    b.style.horizontal_align = "center"
    if chat == v.tab then selected = b end
  end
  if v.adding then
    local f = grid.add{ type = "textfield", name = "kc_new_tab_input", tooltip = "Tab name, Enter to create" }
    f.style.width = TAB_WIDTH
    f.focus()
  else
    tool(grid, "kc_tab_add", "utility/add", "New tab")
  end
  if selected then pane.scroll_to_element(selected) end
  tool(row, "kc_tab_close", "utility/close_black", "Close the tab (its Kiro Crew chat stays)")
end

local function status_text(tab)
  local parts = {}
  if protocol.udp_state == "off" then
    parts[#parts + 1] = "[color=red]UDP is off: start the game with --enable-lua-udp 34200[/color]"
  elseif not protocol.connected() then
    parts[#parts + 1] = "[color=red]Kira is offline[/color]"
  else
    parts[#parts + 1] = "[color=green]online[/color]"
    if protocol.update_to then
      parts[#parts + 1] = "[color=orange]Kira updated to " .. protocol.update_to ..
        ": restart Factorio to load the new mod[/color]"
    end
  end
  if tab.state == "thinking" then
    -- refreshed every second by M.tick, so the clock runs while a long turn works
    local s = tab.stopping and "stopping…" or "thinking…"
    if tab.thinking_since then s = s .. " " .. math.floor((game.tick - tab.thinking_since) / 60) .. " s" end
    if tab.steps and tab.steps > 0 then s = s .. " · step " .. tab.steps end
    -- a model thinking hard can go minutes without a step; the turn is still on until it ends
    local quiet = tab.last_sign and math.floor((game.tick - tab.last_sign) / 60)
    if quiet and quiet >= QUIET_SECONDS then s = s .. " · no new step for " .. quiet .. " s" end
    parts[#parts + 1] = "[color=yellow]" .. s .. "[/color]"
    if tab.activity and tab.activity ~= "" then
      parts[#parts + 1] = "[color=yellow]" .. tab.activity .. "[/color]"
    end
  elseif tab.note == "no_reply" then
    parts[#parts + 1] = "[color=orange]Kira did not answer here; the answer may be in the chat[/color]"
  end
  return table.concat(parts, "   ")
end

local function refresh_status(player, frame)
  local row = find(frame, "kc_status_row")
  local tab = protocol.tab(view(player).tab)
  local label = row.kc_status
  if not label then
    label = row.add{ type = "label", name = "kc_status" }
    label.style.single_line = false
    label.style.maximal_width = WIDTH - 90
  end
  label.caption = status_text(tab)
  -- M.tick runs this every second: the Stop button is added and removed only when the state
  -- changes, because a button rebuilt under the cursor loses the click
  local thinking = tab.state == "thinking"
  if thinking and not row.kc_stop then
    local filler = row.add{ type = "empty-widget", name = "kc_status_filler" }
    filler.style.horizontally_stretchable = true
    row.add{ type = "button", name = "kc_stop", caption = "Stop",
             tooltip = "Stop Kira's turn. Her Kiro Crew chat keeps what she did so far." }
  elseif not thinking and row.kc_stop then
    row.kc_stop.destroy()
    if row.kc_status_filler then row.kc_status_filler.destroy() end
  end
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
        warn = " [color=red](not delivered: no connection)[/color]"
      end
      local with = e.pointers and (" [color=0.3,0.8,1](+" .. e.pointers .. (e.pointers == 1 and " pointer" or " pointers") .. ")[/color]") or ""
      who = "[font=default-bold][color=0.6,0.8,1]You:[/color][/font] " .. e.text .. with .. warn
    elseif e.from == "trace_summary" then
      who = TRACE_COLOR .. "(worked: " .. e.text .. ")[/color]"
    elseif e.from == "stopped" then
      who = TRACE_COLOR .. "(stopped after " .. e.text .. ")[/color]"
    else
      who = "[img=kc-kira] [font=default-bold][color=1,0.8,0.4]Kira:[/color][/font] " .. e.text
    end
    local l = pane.add{ type = "label", caption = who }
    l.style.single_line = false
    l.style.maximal_width = WIDTH - 40
  end
  -- Kira's suggested answers ([OPTIONS: ...] of her last message) as buttons; a click sends the
  -- choice as the player's next message. Only while her message is the last one and she is idle.
  local last = tab.feed[#tab.feed]
  if last and last.from == "kira" and last.options and tab.state ~= "thinking" then
    local row = pane.add{ type = "flow", name = "kc_options", direction = "vertical" }
    row.style.vertical_spacing = 2
    for i, o in ipairs(last.options) do
      local b = row.add{ type = "button", name = "kc_option_" .. i, caption = o, tags = { kc_option = o } }
      b.style.horizontally_stretchable = true
      b.style.maximal_width = WIDTH - 40
      b.style.horizontal_align = "left"
    end
  end
  -- the running turn, step by step: Kira's own words between tool calls, and the tools
  if tab.state == "thinking" and tab.trace then
    for _, s in ipairs(tab.trace) do
      local text
      if s.kind == "tool" then
        text = TRACE_COLOR .. "  › " .. s.text .. ((s.n or 1) > 1 and (" ×" .. s.n) or "") .. "[/color]"
      else
        text = TRACE_COLOR .. "  " .. s.text .. "[/color]"
      end
      local l = pane.add{ type = "label", caption = text }
      l.style.single_line = false
      l.style.maximal_width = WIDTH - 40
    end
  end
  if #tab.feed == 0 then
    pane.add{ type = "label", caption = "[color=gray]Ask anything about this run.[/color]" }
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
  row.add{ type = "label", caption = "Marks: " .. #marks .. where }
  local filler = row.add{ type = "empty-widget" }
  filler.style.horizontally_stretchable = true
  local enabled = #marks > 0
  tool(row, "kc_mark_prev", "utility/left_arrow", "Previous mark (map view, Esc to return)", enabled)
  tool(row, "kc_mark_next", "utility/right_arrow", "Next mark (map view, Esc to return)", enabled)
  tool(row, "kc_mark_clear", "utility/trash", "Remove all marks of this tab", enabled, "tool_button_red")
end

-- Live panels Kira made (scripts/panels.lua): one toggle per panel, pressed while it is shown.
local function refresh_panels(player, frame)
  local row = find(frame, "kc_panels_row")
  if not row then return end
  row.clear()
  local order = storage.kc.panel_order or {}
  row.add{ type = "label", caption = "Panels:" }
  local flow = row.add{ type = "flow", direction = "horizontal" }
  if #order == 0 then flow.add{ type = "label", caption = "[color=gray]none open[/color]" } end
  for _, name in ipairs(order) do
    local p = storage.kc.panels[name]
    if p then
      local shown = not (p.hidden and p.hidden[player.index])
      local head, cut = utf8_head(p.title, TAB_CHARS + 4)
      local b = flow.add{ type = "button", caption = cut and (head .. "…") or p.title, toggled = shown,
                          tooltip = (shown and "Hide " or "Show ") .. p.title .. (p.error and ("\n[color=red]" .. p.error .. "[/color]") or ""),
                          tags = { kc_panel = name, kc_panel_toggle = true } }
      b.style.minimal_width = 0
      b.style.horizontal_align = "center"
    end
  end
  local filler = row.add{ type = "empty-widget" }
  filler.style.horizontally_stretchable = true
  row.add{ type = "button", name = "kc_library_toggle", caption = "Library", toggled = view(player).library == true,
           tooltip = "Panels saved in this run (Kiro Crew artifacts): open one without asking Kira" }
end

-- The run's saved panels (scripts/panels.lua, library): open or delete them, no model involved.
-- Its own frame at the top of the left column, beside the panels: Kira's window is tall and its
-- bottom rows can sit below the screen edge.
local function refresh_library(player)
  local v = view(player)
  local fr = player.gui.left.kc_library
  if not v.library then
    if fr then fr.destroy() end
    return
  end
  if not fr then
    fr = player.gui.left.add{ type = "frame", name = "kc_library", direction = "vertical" }
    pcall(function() player.gui.left.swap_children(1, fr.get_index_in_parent()) end) -- first in the column
    local bar = fr.add{ type = "flow", direction = "horizontal" }
    bar.style.vertical_align = "center"
    bar.add{ type = "label", caption = "Panel library", style = "frame_title", ignored_by_interaction = true }
    local grip = bar.add{ type = "empty-widget", ignored_by_interaction = true }
    grip.style.horizontally_stretchable, grip.style.minimal_width = true, 8
    bar.add{ type = "sprite-button", name = "kc_library_close", sprite = "utility/close", style = "frame_action_button",
             tooltip = "Close the library" }
    local inner = fr.add{ type = "frame", name = "kc_library_body", direction = "vertical",
                          style = "inside_shallow_frame_with_padding" }
    inner.style.minimal_width = 260
  end
  local box = fr.kc_library_body
  box.clear()
  local lib = storage.kc.library or { panels = {}, pending = {} }
  local loading = next(lib.pending or {}) ~= nil
  local head = "Saved in this run"
  if loading then head = head .. "  [color=gray]loading…[/color]" end
  box.add{ type = "label", caption = head }
  if lib.error then
    local l = box.add{ type = "label", caption = "[color=red]" .. lib.error .. "[/color]" }
    l.style.single_line = false
    l.style.maximal_width = 320
  end
  local list = lib.panels or {}
  if #list == 0 and not loading then
    box.add{ type = "label", caption = "[color=gray]Nothing saved yet: ask Kira for a panel.[/color]" }
  end
  local pane = box.add{ type = "scroll-pane", horizontal_scroll_policy = "never", vertical_scroll_policy = "auto" }
  pane.style.maximal_height = 6 * 32
  for _, p in ipairs(list) do
    local r = pane.add{ type = "flow", direction = "horizontal" }
    r.style.vertical_align = "center"
    local b = r.add{ type = "button", name = "kc_lib_open:" .. p.slug, caption = p.title,
                     tooltip = "Open", tags = { kc_lib_open = p.slug } }
    b.style.horizontally_stretchable = true
    b.style.maximal_width = 320
    b.style.horizontal_align = "left"
    local confirm = v.confirm_delete == p.slug
    r.add{ type = "sprite-button", name = "kc_lib_del:" .. p.slug, sprite = "utility/trash",
           style = confirm and "tool_button_red" or "tool_button",
           tooltip = confirm and "Click again to delete it from Kiro Crew" or "Delete from the library",
           tags = { kc_lib_del = p.slug } }
  end
end

local function refresh_pointers(player, frame)
  local row = find(frame, "kc_pointers_row")
  row.clear()
  local list = pointers.list(player)
  row.visible = #list > 0
  if #list == 0 then return end
  local head = row.add{ type = "flow", direction = "horizontal" }
  head.style.vertical_align = "center"
  head.add{ type = "label", caption = "[color=0.3,0.8,1]Pointers (go with the next question):[/color]" }
  local filler = head.add{ type = "empty-widget" }
  filler.style.horizontally_stretchable = true
  tool(head, "kc_pointer_clear", "utility/trash", "Remove the pointers", true, "tool_button_red")
  for _, p in ipairs(list) do
    local l = row.add{ type = "label", caption = "[color=0.3,0.8,1]" .. pointers.label(p) .. "[/color]" }
    l.style.maximal_width = WIDTH - 40
  end
end

function M.refresh(player)
  refresh_library(player)
  local frame = root(player)
  if not frame then return end
  refresh_run(player, frame)
  refresh_tabs(player, frame)
  refresh_status(player, frame)
  refresh_feed(player, frame)
  refresh_marks(player, frame)
  refresh_panels(player, frame)
  refresh_pointers(player, frame)
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

--- Send the input field's text (or `text`, a chosen option) as a question. `send` puts a message on the wire.
local function submit(player, send, text)
  local frame = root(player)
  local field = find(frame, "kc_input")
  if text == nil then
    text = field.text:gsub("^%s+", ""):gsub("%s+$", "")
    if text == "" then return end
    field.text = ""
  end
  send(protocol.ask(view(player).tab, text, player, pointers.take(player)))
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
  elseif name == "kc_stop" then
    send(protocol.stop_request(v.tab))
    M.refresh(player)
  elseif el.tags and el.tags.kc_option then
    submit(player, send, el.tags.kc_option)
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
  elseif name == "kc_library_toggle" or name == "kc_library_close" then
    if name == "kc_library_close" then v.library = true end -- the toggle below closes it
    v.library = not v.library
    v.confirm_delete = nil
    if v.library then send(protocol.library_request("list")) end -- the shown list is the last one until it arrives
    M.refresh(player)
  elseif el.tags and el.tags.kc_lib_open then
    v.confirm_delete = nil
    send(protocol.library_request("open", el.tags.kc_lib_open))
    M.refresh(player)
  elseif el.tags and el.tags.kc_lib_del then
    if v.confirm_delete == el.tags.kc_lib_del then
      v.confirm_delete = nil
      send(protocol.library_request("delete", el.tags.kc_lib_del))
    else
      v.confirm_delete = el.tags.kc_lib_del -- deleting is for good: the second click does it
    end
    M.refresh(player)
  elseif name == "kc_point" then
    pointers.give_tool(player)
  elseif name == "kc_pointer_clear" then
    pointers.clear(player)
    M.refresh(player)
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

--- A new pointer: open the window if needed and put "@n " at the end of the question.
function M.on_pointer(player, p)
  if not root(player) then
    view(player)
    build(player)
  end
  local field = find(root(player), "kc_input")
  local text = field.text
  if text ~= "" and not text:find("%s$") then text = text .. " " end
  field.text = text .. "@" .. p.n .. " "
  M.refresh(player)
end

-- ── driving the window from outside (UI iteration loop, tools/ui_dev.py) ──

local function player_of(index)
  return game.get_player(index or 1)
end

--- Open the window if it is closed (toggle closes an open one, which a script rarely wants).
function M.open(index)
  local player = player_of(index)
  if not root(player) then M.toggle(player) end
  return root(player) ~= nil
end

function M.close(index)
  local player = player_of(index)
  if root(player) then M.toggle(player) end
end

--- Press a button by element name, or a tab by its chat name ("kc_tab:<chat>").
function M.click(name, send, index)
  local player = player_of(index)
  local frame = root(player)
  local lib = player.gui.left.kc_library
  local el = (frame and find(frame, name)) or (lib and find(lib, name)) or mod_gui.get_button_flow(player)[name]
  if not el then return false end
  M.on_click({ element = el, player_index = player.index }, send)
  return true
end

--- Type into a text field and press Enter.
function M.confirm(name, text, send, index)
  local player = player_of(index)
  local frame = root(player)
  local el = frame and find(frame, name)
  if not el then return false end
  el.text = text
  M.on_confirmed({ element = el, player_index = player.index }, send)
  return true
end

return M
