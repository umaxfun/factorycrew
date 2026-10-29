-- Live panels (SPEC.md, "Panels"): small windows Kira writes once and the mod keeps up to date.
-- A panel is a Lua body (the data source) that returns a view; the mod runs it every `every`
-- seconds and draws the view from a fixed set of blocks. Kira is not involved in the refresh.
--
-- storage.kc.panels[name] = {name, title, lua, every, inputs, input, state, view, error, errors,
--                            paused, last_tick, refreshes, chat, hidden = {player_index = true}}
-- Compiled functions cannot live in storage, so they are cached per load (rebuilt lazily).

local protocol = require("scripts.protocol")
local gui = require("scripts.gui") -- the Panels row in Kira's window

local M = {}

M.MIN_EVERY, M.MAX_EVERY, M.DEFAULT_EVERY = 1, 600, 2 -- seconds
M.MAX_ERRORS = 5     -- consecutive failed refreshes before a panel pauses
M.MAX_ROWS = 200     -- rows drawn per table; the rest are counted in a footer line
M.MAX_PANELS = 12
M.PREVIEW_ROWS = 30  -- rows per table returned to Kira on create
M.GPS_ZOOM = 0.5     -- a link cell's view: about 60 tiles across instead of a few
local PREFIX = "kc_panel:"

local compiled = {}  -- name -> {src = lua, fn = function, env = table}

local function store()
  storage.kc.panels = storage.kc.panels or {}
  storage.kc.panel_order = storage.kc.panel_order or {}
  return storage.kc.panels
end

function M.init_storage() store() end

local function trim(s) return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")) end

-- ── data ─────────────────────────────────────────────────────────────────

local function compile(p)
  local c = compiled[p.name]
  if c and c.src == p.lua then return c end
  local env = protocol.query_env()
  local fn, err = load(p.lua, "=panel:" .. p.name, "t", env)
  c = { src = p.lua, fn = fn, env = env, err = err }
  compiled[p.name] = c
  return c
end

-- A view is a list of blocks; a single table with `rows` or `text` counts as one block.
local function as_blocks(v)
  if type(v) == "string" or type(v) == "number" then return { tostring(v) } end
  if type(v) ~= "table" then return nil end
  if v.rows or v.text or v.bar then return { v } end
  return v
end

--- Run the data source once and keep the result. Returns ok, error text.
function M.refresh_data(p)
  p.last_tick = game.tick
  local c = compile(p)
  if not c.fn then
    p.error, p.errors = "compile: " .. tostring(c.err), (p.errors or 0) + 1
  else
    -- the player may have moved to another surface since the panel was made
    local fresh = protocol.query_env()
    c.env.surface, c.env.player = fresh.surface, fresh.player
    c.env.input, c.env.state = p.input, p.state
    local prof = helpers.create_profiler()
    local ok, res = pcall(c.fn)
    prof.stop()
    compiled[p.name].profiler = prof
    local blocks = ok and as_blocks(res)
    if not ok then
      p.error, p.errors = tostring(res), (p.errors or 0) + 1
    elseif not blocks then
      p.error, p.errors = "the body must return a view: a list of blocks (text, {columns, rows}, {bar, max})", (p.errors or 0) + 1
    else
      p.view, p.error, p.errors = blocks, nil, 0
    end
  end
  p.refreshes = (p.refreshes or 0) + 1
  if (p.errors or 0) >= M.MAX_ERRORS then p.paused = true end
  return p.error == nil, p.error
end

-- ── drawing ──────────────────────────────────────────────────────────────

local COLORS = {
  red = { 1, 0.35, 0.3 }, green = { 0.4, 0.9, 0.4 }, yellow = { 1, 0.85, 0.3 }, orange = { 1, 0.6, 0.2 },
  gray = { 0.62, 0.62, 0.62 }, grey = { 0.62, 0.62, 0.62 }, blue = { 0.4, 0.7, 1 }, white = { 1, 1, 1 },
}

function M.fmt(n)
  if type(n) ~= "number" then return tostring(n) end
  local a = math.abs(n)
  if a >= 1e9 then return string.format("%.1fG", n / 1e9) end
  if a >= 1e6 then return string.format("%.1fM", n / 1e6) end
  if a >= 1e4 then return string.format("%.1fk", n / 1e3) end
  if a == math.floor(a) then return string.format("%d", n) end
  if a >= 100 then return string.format("%.0f", n) end
  if a >= 10 then return string.format("%.1f", n) end
  return string.format("%.2f", n)
end

local function plain_label(parent, caption, color, bold)
  local l = parent.add{ type = "label", caption = caption }
  l.style.single_line = false
  l.style.maximal_width = 420
  if bold then l.style.font = "default-bold" end
  if color and COLORS[color] then
    local c = COLORS[color]
    l.style.font_color = { r = c[1], g = c[2], b = c[3] }
  end
  return l
end

-- A refresh updates the elements in place: the i-th child is reused when it is the same kind,
-- otherwise it and everything after it are replaced. Clearing and rebuilding the scroll pane
-- would throw the player's scroll position back to the top on every refresh.
local function slot(parent, i, kind, spec)
  local c = parent.children[i]
  if c and c.valid and c.tags.kc_k == kind then return c, false end
  local kids = parent.children
  for j = #kids, i, -1 do kids[j].destroy() end
  spec.tags = { kc_k = kind }
  return parent.add(spec), true
end

local function cut_after(parent, n)
  local kids = parent.children
  for j = #kids, n + 1, -1 do kids[j].destroy() end
end

local function label(parent, i, caption, color, bold)
  local l, new = slot(parent, i, "l:" .. (COLORS[color] and color or "") .. (bold and ":b" or ""), { type = "label" })
  if new then
    l.style.single_line = false
    l.style.maximal_width = 420
    if bold then l.style.font = "default-bold" end
    if COLORS[color] then
      local c = COLORS[color]
      l.style.font_color = { r = c[1], g = c[2], b = c[3] }
    end
  end
  l.caption = caption
  return l
end

local function sprite_of(cell)
  local kind, name
  if cell.item then kind, name = "item", cell.item
  elseif cell.fluid then kind, name = "fluid", cell.fluid
  elseif cell.signal then kind, name = "virtual-signal", cell.signal
  elseif cell.sprite then return cell.sprite, nil end
  local path = kind .. "/" .. tostring(name)
  local proto = (kind == "item" and prototypes.item[name]) or (kind == "fluid" and prototypes.fluid[name])
      or (kind == "virtual-signal" and prototypes.virtual_signal[name])
  return path, proto and proto.localised_name or name
end

local function bar(parent, i, value, max, caption, color)
  local f, new = slot(parent, i, "bar:" .. (COLORS[color] and color or ""), { type = "flow", direction = "horizontal" })
  if new then f.style.vertical_align = "center" end
  local pb, pnew = slot(f, 1, "pb", { type = "progressbar" })
  if pnew then
    pb.style.width = 110
    if COLORS[color] then
      local c = COLORS[color]
      pb.style.color = { r = c[1], g = c[2], b = c[3] }
    end
  end
  pb.value = math.max(0, math.min(1, (max and max > 0) and value / max or 0))
  label(f, 2, caption or (M.fmt(value) .. (max and (" / " .. M.fmt(max)) or "")))
  cut_after(f, 2)
end

-- The position a link cell points at, or nil: {gps = {x, y}, surface?}, {gps = {x =, y =}},
-- {gps = entity.position}, or the chat tag {gps = "[gps=x,y,surface]"} ("goto" is a Lua keyword).
local function gps_of(v)
  local g = v.gps
  local x, y, s
  if type(g) == "string" then
    local a, b, c = g:match("^%s*%[?gps=(%-?[%d%.]+),(%-?[%d%.]+),?([^%]]*)%]?%s*$")
    x, y, s = tonumber(a), tonumber(b), c
  elseif type(g) == "table" then
    x, y, s = tonumber(g.x or g[1]), tonumber(g.y or g[2]), g.surface or g[3]
  end
  if not (x and y) then return nil end
  s = v.surface or s
  local zoom = tonumber(v.zoom)
  if type(s) == "table" and s.name then s = s.name end -- a LuaSurface
  return { x = x, y = y, surface = (s and s ~= "") and tostring(s) or nil, zoom = zoom }
end

-- A link cell: a flat button; a click opens the remote view at the position (on_click).
local function link(parent, i, v, pos)
  -- the ordinary button with its own dark text: `color` is ignored here, colored text on it is unreadable
  local b, new = slot(parent, i, "gps", { type = "button" })
  if new then
    local s = b.style
    s.height, s.minimal_width, s.top_padding, s.bottom_padding, s.left_padding, s.right_padding = 24, 0, 0, 0, 6, 6
  end
  local text = v.text
  if type(text) == "number" then text = M.fmt(text) end
  if text == nil then text = string.format("%d, %d", math.floor(pos.x), math.floor(pos.y)) end
  local icon = v.item and ("[item=" .. v.item .. "] ") or (v.fluid and ("[fluid=" .. v.fluid .. "] ")) or ""
  b.caption = icon .. tostring(text)
  b.tooltip = string.format("Show on the map (%d, %d%s)", math.floor(pos.x), math.floor(pos.y),
    pos.surface and (", " .. pos.surface) or "")
  local tags = b.tags
  tags.kc_gps = pos
  b.tags = tags
end

local function cell(parent, i, v)
  local pos = type(v) == "table" and gps_of(v)
  if pos then
    link(parent, i, v, pos)
  elseif type(v) == "number" then
    label(parent, i, M.fmt(v))
  elseif type(v) ~= "table" then
    label(parent, i, tostring(v == nil and "" or v))
  elseif v.bar then
    bar(parent, i, tonumber(v.bar) or 0, tonumber(v.max), v.caption, v.color)
  elseif v.item or v.fluid or v.signal or v.sprite then
    local path, tip = sprite_of(v)
    local f, new = slot(parent, i, "icon", { type = "flow", direction = "horizontal" })
    if new then f.style.vertical_align = "center" end
    local j, valid = 1, helpers.is_valid_sprite_path(path)
    if valid then
      local sp, snew = slot(f, j, "s", { type = "sprite" })
      if snew then sp.style.width, sp.style.height, sp.style.stretch_image_to_widget_size = 24, 24, true end
      sp.sprite, sp.tooltip = path, tip
      j = j + 1
    end
    local text = v.text or (v.label and tip) or nil
    if text == nil and not valid then text = tostring(v.item or v.fluid or v.signal or v.sprite) end
    if text ~= nil then
      label(f, j, type(text) == "number" and M.fmt(text) or text, v.color)
      j = j + 1
    end
    cut_after(f, j - 1)
  else
    label(parent, i, v.text ~= nil and (type(v.text) == "number" and M.fmt(v.text) or tostring(v.text)) or "", v.color, v.bold)
  end
end

--- Draw one block at child index i of body; returns the next free index.
local function draw_block(body, i, b)
  if type(b) == "string" or type(b) == "number" then
    label(body, i, tostring(b))
    return i + 1
  elseif type(b) ~= "table" then
    return i
  elseif b.rows then
    if b.title then
      label(body, i, b.title, nil, true)
      i = i + 1
    end
    local rows = type(b.rows) == "table" and b.rows or {}
    local cols = b.columns and #b.columns or 0
    for _, r in ipairs(rows) do if type(r) == "table" and #r > cols then cols = #r end end
    if cols == 0 or #rows == 0 then
      label(body, i, b.empty or "(nothing)", "gray")
      return i + 1
    end
    local t, new = slot(body, i, "t:" .. cols .. (b.columns and ":h" or ""),
      { type = "table", column_count = cols, draw_horizontal_line_after_headers = b.columns ~= nil })
    if new then t.style.horizontal_spacing, t.style.vertical_spacing = 12, 2 end
    local k = 1
    if b.columns then
      for c = 1, cols do label(t, k, tostring(b.columns[c] or ""), nil, true) k = k + 1 end
    end
    for n, r in ipairs(rows) do
      if n > M.MAX_ROWS then break end
      for c = 1, cols do
        cell(t, k, type(r) == "table" and r[c] or (c == 1 and r or ""))
        k = k + 1
      end
    end
    cut_after(t, k - 1)
    i = i + 1
    if #rows > M.MAX_ROWS then
      label(body, i, "… " .. (#rows - M.MAX_ROWS) .. " more rows", "gray")
      i = i + 1
    end
    return i
  elseif b.bar then
    bar(body, i, tonumber(b.bar) or 0, tonumber(b.max), b.caption, b.color)
    return i + 1
  else
    cell(body, i, b)
    return i + 1
  end
end

local function root_name(name) return PREFIX .. name end

local function draw_inputs(row, p)
  row.clear()
  for _, inp in ipairs(p.inputs or {}) do
    if inp.label then plain_label(row, inp.label) end
    local items, sel = {}, 1
    for i, o in ipairs(inp.options or {}) do
      items[i] = tostring(o)
      if tostring(o) == tostring(p.input[inp.key]) then sel = i end
    end
    row.add{ type = "drop-down", items = items, selected_index = sel,
             tags = { kc_panel = p.name, kc_input = inp.key } }
  end
  row.visible = #(p.inputs or {}) > 0
end

local function build(player, p, index)
  -- the left column: Factorio stacks its frames one under another, so panels never overlap
  local frame = player.gui.left.add{ type = "frame", name = root_name(p.name), direction = "vertical",
                                      tags = { kc_panel = p.name } }
  local bar_ = frame.add{ type = "flow", direction = "horizontal" }
  bar_.style.vertical_align = "center"
  bar_.add{ type = "label", caption = p.title, style = "frame_title", ignored_by_interaction = true }
  local grip = bar_.add{ type = "empty-widget", ignored_by_interaction = true }
  grip.style.horizontally_stretchable, grip.style.minimal_width = true, 8
  -- collapse to the title bar: the panel stays in place and keeps its data, the view is out of the way
  bar_.add{ type = "sprite-button", name = "kc_panel_collapse", sprite = "utility/collapse", style = "frame_action_button",
            tooltip = "Collapse", tags = { kc_panel = p.name, kc_panel_collapse = true } }
  bar_.add{ type = "sprite-button", sprite = "utility/close", style = "frame_action_button",
            tooltip = "Hide (reopen from Kira's window)", tags = { kc_panel = p.name, kc_panel_close = true } }
  local inner = frame.add{ type = "frame", name = "kc_panel_inner", direction = "vertical",
                           style = "inside_shallow_frame_with_padding" }
  inner.style.minimal_width = 220
  local inputs = inner.add{ type = "flow", name = "kc_panel_inputs", direction = "horizontal" }
  inputs.style.vertical_align = "center"
  -- always a scroll pane: a long view scrolls inside the panel instead of growing off the screen
  local pane = inner.add{ type = "scroll-pane", name = "kc_panel_body", direction = "vertical",
                          horizontal_scroll_policy = "never", vertical_scroll_policy = "auto-and-reserve-space" }
  pane.style.maximal_height = 360
  inner.add{ type = "label", name = "kc_panel_footer" }
  draw_inputs(inputs, p)
  return frame
end

local function footer_caption(p)
  local every = "every " .. p.every .. " s"
  if p.paused then
    return "[color=1,0.35,0.3]paused after " .. M.MAX_ERRORS .. " errors: ask Kira to fix it[/color]"
  end
  local c = compiled[p.name]
  if c and c.profiler then
    return { "", "[color=0.62,0.62,0.62]", every, " · ", c.profiler, "[/color]" }
  end
  return "[color=0.62,0.62,0.62]" .. every .. "[/color]"
end

local function draw(player, p, index)
  local frame = player.gui.left[root_name(p.name)]
  if p.hidden and p.hidden[player.index] then
    if frame then frame.destroy() end
    return
  end
  if frame and frame.tags.kc_panel_src ~= p.version then
    frame.destroy() -- the panel was replaced: inputs or title may differ
    frame = nil
  end
  if not frame then
    frame = build(player, p, index)
    local tags = frame.tags
    tags.kc_panel_src = p.version
    frame.tags = tags
  end
  local inner = frame.kc_panel_inner
  local folded = p.collapsed and p.collapsed[player.index] or false
  inner.visible = not folded
  local fold = frame.children[1].kc_panel_collapse
  fold.sprite, fold.tooltip = folded and "utility/expand" or "utility/collapse", folded and "Expand" or "Collapse"
  if folded then return end -- nothing to draw; it is redrawn on expand
  local body = inner.kc_panel_body
  local i = 1
  if p.error then
    label(body, i, "data failed: " .. p.error, "red")
    i = i + 1
  end
  for _, b in ipairs(p.view or {}) do i = draw_block(body, i, b) end
  cut_after(body, i - 1)
  inner.kc_panel_footer.caption = footer_caption(p)
end

function M.draw_all(only)
  local panels = store()
  for i, name in ipairs(storage.kc.panel_order) do
    local p = panels[name]
    if p and (only == nil or only == name) then
      for _, player in pairs(game.connected_players) do draw(player, p, i) end
    end
  end
end

-- ── lifecycle ────────────────────────────────────────────────────────────

local function preview(view)
  local out = {}
  for i, b in ipairs(view or {}) do
    if type(b) == "table" and b.rows and #b.rows > M.PREVIEW_ROWS then
      local rows = {}
      for r = 1, M.PREVIEW_ROWS do rows[r] = b.rows[r] end
      out[i] = { title = b.title, columns = b.columns, rows = rows, rows_total = #b.rows }
    else
      out[i] = b
    end
  end
  return out
end

--- Create or replace a panel and run it once. spec: {name, title?, description?, lua, every?, inputs?, chat?}.
--- inputs: {{key, label?, options = {...}, default?}}; the body reads input.<key> (a string).
--- Returns {ok, name, view (preview) | error}. A panel whose first run fails is still kept,
--- so Kira can fix it with the same name; it shows the error to the player meanwhile.
function M.set(spec)
  if type(spec) ~= "table" then return { ok = false, error = "spec must be a table" } end
  local name = trim(spec.name)
  if name == "" then return { ok = false, error = "name is required" } end
  if type(spec.lua) ~= "string" or trim(spec.lua) == "" then return { ok = false, error = "lua is required" } end
  local panels = store()
  if not panels[name] and #storage.kc.panel_order >= M.MAX_PANELS then
    return { ok = false, error = "too many panels (" .. M.MAX_PANELS .. "): remove one first" }
  end
  local old = panels[name]
  local inputs, input = {}, {}
  for _, inp in ipairs(spec.inputs or {}) do
    if type(inp) == "table" and inp.key and type(inp.options) == "table" and #inp.options > 0 then
      local opts = {}
      for i, o in ipairs(inp.options) do opts[i] = tostring(o) end
      inputs[#inputs + 1] = { key = tostring(inp.key), label = inp.label, options = opts }
      local want = inp.default ~= nil and tostring(inp.default) or opts[1]
      local prev = old and old.input and old.input[tostring(inp.key)]
      for _, o in ipairs(opts) do if o == prev then want = prev end end -- keep the player's choice
      input[tostring(inp.key)] = want
    end
  end
  local every = math.floor(tonumber(spec.every) or M.DEFAULT_EVERY)
  every = math.max(M.MIN_EVERY, math.min(M.MAX_EVERY, every))
  local p = {
    name = name, title = trim(spec.title) ~= "" and trim(spec.title) or name, lua = spec.lua,
    description = trim(spec.description) ~= "" and trim(spec.description) or nil,
    every = every, inputs = inputs, input = input, state = (old and old.state) or {},
    chat = spec.chat, hidden = {}, collapsed = (old and old.collapsed) or {}, created_tick = game.tick, errors = 0, refreshes = 0,
  }
  storage.kc.seq = (storage.kc.seq or 0) + 1
  p.version = game.tick .. ":" .. storage.kc.seq -- a replaced panel's window is rebuilt
  panels[name] = p
  if not old then storage.kc.panel_order[#storage.kc.panel_order + 1] = name end
  compiled[name] = nil
  local ok, err = M.refresh_data(p)
  M.draw_all(name)
  gui.refresh_all()
  if ok then
    return { ok = true, name = name, every = every, input = input, view = preview(p.view),
             run_name = storage.kc.run_name, run_id = storage.kc.run_id }
  end
  return { ok = false, name = name, error = err,
           message = "the panel is kept and shows this error; fix the body and call again with the same name" }
end

function M.remove(name)
  local panels = store()
  if not panels[name] then return { ok = false, error = "no panel " .. tostring(name) } end
  panels[name] = nil
  compiled[name] = nil
  for i, n in ipairs(storage.kc.panel_order) do
    if n == name then table.remove(storage.kc.panel_order, i) break end
  end
  for _, player in pairs(game.players) do
    local f = player.gui.left[root_name(name)]
    if f then f.destroy() end
  end
  gui.refresh_all()
  return { ok = true, removed = name }
end

--- Plain snapshot: every panel with its last view and error (for Kira, tests, the remote interface).
function M.list(with_view)
  local out = {}
  local panels = store()
  for _, name in ipairs(storage.kc.panel_order) do
    local p = panels[name]
    if p then
      out[#out + 1] = {
        name = name, title = p.title, description = p.description, every = p.every, input = p.input, inputs = p.inputs,
        error = p.error, paused = p.paused or false, refreshes = p.refreshes, last_tick = p.last_tick,
        lua = with_view and p.lua or nil, view = with_view and p.view or nil, chat = p.chat,
      }
    end
  end
  return out
end

function M.get(name) return store()[name] end

--- Set an input (what the drop-down does) and refresh at once.
function M.set_input(name, key, value)
  local p = store()[name]
  if not p then return false end
  p.input[key] = tostring(value)
  p.paused, p.errors = false, 0
  M.refresh_data(p)
  M.draw_all(name)
  return true
end

--- Fold a panel to its title bar or unfold it (per player). `folded` nil toggles.
function M.collapse(player, name, folded)
  local p = store()[name]
  if not p then return end
  p.collapsed = p.collapsed or {}
  if folded == nil then folded = not p.collapsed[player.index] end
  p.collapsed[player.index] = folded or nil
  M.draw_all(name)
end

function M.show(player, name, visible)
  local p = store()[name]
  if not p then return end
  p.hidden = p.hidden or {}
  p.hidden[player.index] = (not visible) or nil
  if visible then p.paused, p.errors = false, 0 end -- reopening is a retry
  M.draw_all(name)
end

--- on_nth_tick(60): refresh panels that are due and visible to someone.
function M.tick()
  local panels = store()
  for i, name in ipairs(storage.kc.panel_order) do
    local p = panels[name]
    if p and not p.paused and game.tick - (p.last_tick or 0) >= p.every * 60 then
      local seen = false
      for _, player in pairs(game.connected_players) do
        if not (p.hidden and p.hidden[player.index]) then seen = true end
      end
      -- nobody looks (or no player, e.g. a headless test server): refresh anyway, so the data
      -- Kira or a test reads is live; drawing has nobody to draw for
      M.refresh_data(p)
      if seen then
        for _, player in pairs(game.connected_players) do draw(player, p, i) end
      end
    end
  end
end

-- ── events ───────────────────────────────────────────────────────────────

--- A link cell's click: the remote view (the map) centred on the position. The character stays put.
function M.go_to(player, pos)
  local surface = game.get_surface(pos.surface or player.physical_surface.name)
  if not surface then
    player.print("[KC] no surface " .. tostring(pos.surface))
    return false
  end
  player.set_controller{ type = defines.controllers.remote, surface = surface, position = { pos.x, pos.y } }
  -- the remote view opens zoomed far in; a chest with its neighbours needs a wider look
  pcall(function() player.zoom = tonumber(pos.zoom) or M.GPS_ZOOM end)
  return true
end

function M.on_click(e)
  local el = e.element
  if not (el and el.valid and el.tags) then return false end
  if el.tags.kc_gps then
    -- an error in a click handler would stop the game: report it instead
    local player = game.get_player(e.player_index)
    local ok, err = pcall(M.go_to, player, el.tags.kc_gps)
    if not ok then player.print("[KC] could not open the map there: " .. tostring(err)) end
    return true
  end
  if not el.tags.kc_panel then return false end
  local player = game.get_player(e.player_index)
  if el.tags.kc_panel_collapse then
    M.collapse(player, el.tags.kc_panel)
    return true
  end
  if el.tags.kc_panel_close then
    M.show(player, el.tags.kc_panel, false)
    return true
  end
  if el.tags.kc_panel_toggle then
    local p = store()[el.tags.kc_panel]
    if p then M.show(player, p.name, p.hidden and p.hidden[player.index] or false) end
    return true
  end
  return false
end

function M.on_selection(e)
  local el = e.element
  if not (el and el.valid and el.tags and el.tags.kc_panel and el.tags.kc_input) then return false end
  M.set_input(el.tags.kc_panel, el.tags.kc_input, el.items[el.selected_index])
  return true
end

-- ── the run's library: panels saved as Kiro Crew artifacts, opened without a model ──
--
-- The window asks the bridge (protocol.library_request); the dispatcher, which runs inside the
-- gateway, reads the artifacts and answers with a library_result. storage.kc.library =
-- {panels = {{slug, title}}, pending = {id -> op}, error, tick}; the last list is kept, so the
-- window shows it at once and replaces it when the fresh one arrives.

M.LIBRARY_TIMEOUT_TICKS = 600 -- 10 s without an answer: Kiro Crew is not there

function M.library()
  local kc = storage.kc
  kc.library = kc.library or { panels = {}, pending = {} }
  kc.library.pending = kc.library.pending or {}
  return kc.library
end

--- A library_result from the bridge: a list, a panel to open, or a deletion.
function M.on_library(msg)
  local lib = M.library()
  local op = (lib.pending[msg.id] and lib.pending[msg.id].op) or msg.op
  lib.pending[msg.id] = nil
  if not msg.ok then
    lib.error = tostring(msg.error or "Kiro Crew refused")
    return
  end
  lib.error = nil
  if msg.panels then
    local list = {}
    for _, p in ipairs(msg.panels) do
      if type(p) == "table" and p.slug then list[#list + 1] = { slug = p.slug, title = p.title or p.slug, name = p.name } end
    end
    lib.panels, lib.tick = list, game.tick
  end
  if op == "open" and type(msg.spec) == "table" then
    local r = M.set(msg.spec)
    if not r.ok then lib.error = "opened, but its data failed: " .. tostring(r.error) end
    -- opened from the library: shown even if this player had hidden it
    for _, player in pairs(game.connected_players) do M.show(player, r.name or msg.spec.name, true) end
  end
end

--- Requests that got no answer in time turn into an error line.
function M.library_tick()
  local lib = M.library()
  local late = {}
  for id, op in pairs(lib.pending) do
    if type(op) ~= "table" or game.tick - op.tick > M.LIBRARY_TIMEOUT_TICKS then late[#late + 1] = id end
  end
  for _, id in ipairs(late) do lib.pending[id] = nil end
  if #late > 0 then lib.error = "Kiro Crew did not answer (is the dispatcher running?)" end
  return #late > 0
end

-- in the query environment, beside the other kc helpers: kc.panel{...}, kc.panel_remove(name), kc.panels()
local kc = require("scripts.helpers")
kc.panel, kc.panel_remove = M.set, M.remove
kc.panels = function() return M.list(false) end
kc.fmt = M.fmt

return M
