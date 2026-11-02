-- Pointers (SPEC.md, "Pointers"): the player shows Kira places with the kc-pointer selection tool.
-- A click is a point (the entity under it, or bare ground), a drag is a box. Pointers are numbered
-- @1, @2, ... per player, drawn on the ground for that player only, and go out with the next
-- question (protocol.ask), which clears them. storage.kc.pointers[player_index] = {pointer, ...}.

local M = {}

M.TOOL = "kc-pointer"
M.LIMIT = 9
M.CLICK = 1.0     -- a selection smaller than this both ways is a click
M.TOP = 12        -- entity names listed in a box summary
local COLOR = { r = 0.3, g = 0.8, b = 1, a = 0.9 }

local STATUS = {}
for k, v in pairs(defines.entity_status) do STATUS[v] = k end

local function r1(v) return math.floor(v * 10 + 0.5) / 10 end

local function list(player)
  local kc = storage.kc
  kc.pointers = kc.pointers or {}
  local l = kc.pointers[player.index]
  if not l then
    l = {}
    kc.pointers[player.index] = l
  end
  return l
end

--- What Kira needs to know about one entity without another query.
local function describe(e)
  local d = { name = e.name, type = e.type, x = r1(e.position.x), y = r1(e.position.y) }
  if e.type == "entity-ghost" then d.ghost = e.ghost_name end
  if e.type == "assembling-machine" or e.type == "furnace" or e.type == "rocket-silo" then
    local ok, recipe = pcall(e.get_recipe)
    if ok and recipe then
      d.recipe = recipe.name
    elseif e.type == "furnace" then
      local okp, prev = pcall(function() return e.previous_recipe end)
      if okp and prev then d.recipe = (prev.name and prev.name.name) or prev.name end
    end
  end
  if e.type == "mining-drill" then
    local ok, target = pcall(function() return e.mining_target end)
    if ok and target then d.mining = target.name end
  end
  if e.type == "resource" then d.amount = e.amount end
  local ok, status = pcall(function() return e.status end)
  if ok and status then d.status = STATUS[status] end
  return d
end

local function inside(e, pos)
  local b = e.selection_box
  return pos.x >= b.left_top.x and pos.x <= b.right_bottom.x and pos.y >= b.left_top.y and pos.y <= b.right_bottom.y
end

--- The entity a click means: one whose box holds the point, a building before ore under it;
--- otherwise the nearest building in the selection.
local function under(entities, pos)
  local best, best_score
  for _, e in ipairs(entities) do
    if e.valid and e.type ~= "character" then
      local dx, dy = e.position.x - pos.x, e.position.y - pos.y
      local score = dx * dx + dy * dy
      if not inside(e, pos) then score = score + 1e6 end
      if e.type == "resource" then score = score + 1e3 end
      if not best_score or score < best_score then best, best_score = e, score end
    end
  end
  return best
end

local function top(counts, limit)
  local arr = {}
  for name, n in pairs(counts) do arr[#arr + 1] = { name = name, n = n } end
  table.sort(arr, function(a, b) if a.n ~= b.n then return a.n > b.n end return a.name < b.name end)
  while #arr > limit do table.remove(arr) end
  return arr
end

local function summarize(p, entities)
  local counts, recipes, ore, total = {}, {}, {}, 0
  for _, e in ipairs(entities) do
    if e.valid and e.type ~= "character" then
      if e.type == "resource" then
        ore[e.name] = (ore[e.name] or 0) + e.amount
      else
        total = total + 1
        local name = e.type == "entity-ghost" and (e.ghost_name .. " (ghost)") or e.name
        counts[name] = (counts[name] or 0) + 1
        if e.type == "assembling-machine" or e.type == "furnace" then
          local ok, recipe = pcall(e.get_recipe)
          if ok and recipe then recipes[recipe.name] = (recipes[recipe.name] or 0) + 1 end
        end
      end
    end
  end
  p.count = total
  -- empty lists are left out: an empty Lua table has no JSON shape of its own
  local function some(t) return #t > 0 and t or nil end
  p.counts = some(top(counts, M.TOP))
  p.recipes = some(top(recipes, 6))
  local res = {}
  for name, amount in pairs(ore) do res[#res + 1] = { name = name, amount = amount } end
  table.sort(res, function(a, b) return a.amount > b.amount end)
  p.resources = some(res)
end

local function draw(p, player, surface, entity)
  local ids = {}
  local function keep(obj) if obj then ids[#ids + 1] = obj.id end end
  local players = { player }
  if p.kind == "box" then
    local a = p.area
    keep(rendering.draw_rectangle{ color = COLOR, width = 3, filled = false, surface = surface, players = players,
                                   left_top = { a[1], a[2] }, right_bottom = { a[3], a[4] } })
    -- above the top-left corner, so it covers nothing inside the box
    keep(rendering.draw_text{ text = "@" .. p.n, surface = surface, players = players, color = COLOR, scale = 3,
                              target = { a[1], a[2] - 1.6 } })
  else
    local target = (entity and entity.valid and entity.type ~= "resource") and entity or { p.x, p.y }
    local radius = 0.6
    if entity and entity.valid then
      local b = entity.selection_box
      radius = math.max(0.6, math.max(b.right_bottom.x - b.left_top.x, b.right_bottom.y - b.left_top.y) * 0.6)
    end
    keep(rendering.draw_circle{ color = COLOR, radius = radius, width = 3, filled = false, surface = surface,
                                players = players, target = target })
    -- centred above the circle: to the side it lands on the neighbouring machine
    local cx, cy = p.x, p.y
    if entity and entity.valid then cx, cy = entity.position.x, entity.position.y end
    keep(rendering.draw_text{ text = "@" .. p.n, surface = surface, players = players, color = COLOR, scale = 3,
                              alignment = "center", vertical_alignment = "bottom", target = { cx, cy - radius - 0.1 } })
  end
  return ids
end

local function erase(p)
  for _, id in ipairs(p.renders or {}) do
    local obj = rendering.get_object_by_id(id)
    if obj and obj.valid then obj.destroy() end
  end
end

--- The pointer as it goes on the wire (no render ids).
local function wire(p)
  local out = {}
  for k, v in pairs(p) do if k ~= "renders" then out[k] = v end end
  return out
end

--- Add a pointer from a selection. Returns the wire form, or nil and a reason.
function M.add(player, surface, area, entities)
  local l = list(player)
  if #l >= M.LIMIT then return nil, "limit" end
  local x0, y0 = area.left_top.x, area.left_top.y
  local x1, y1 = area.right_bottom.x, area.right_bottom.y
  local p = { n = #l + 1, surface = surface.name }
  if (x1 - x0) < M.CLICK and (y1 - y0) < M.CLICK then
    local pos = { x = (x0 + x1) / 2, y = (y0 + y1) / 2 }
    p.kind = "point"
    p.x, p.y = r1(pos.x), r1(pos.y)
    local ok, tile = pcall(surface.get_tile, pos.x, pos.y)
    if ok and tile and tile.valid then p.tile = tile.name end
    -- ore sits at the tile centre with a ±0.1 collision box, so an off-centre click misses it:
    -- look it up by the clicked tile
    local tx, ty = math.floor(pos.x) + 0.5, math.floor(pos.y) + 0.5
    local ore = surface.find_entities_filtered{ type = "resource", area = { { tx - 0.2, ty - 0.2 }, { tx + 0.2, ty + 0.2 } }, limit = 1 }[1]
    local e = under(entities or {}, pos)
    if not (e and inside(e, pos) and e.type ~= "resource") then e = ore or e end
    if e then
      p.entity = describe(e)
      -- ore under a building: the click was about the building, but the patch is worth knowing
      if e.type ~= "resource" and ore then p.resource = ore.name end
    end
    p.renders = draw(p, player, surface, e)
  else
    p.kind = "box"
    p.area = { math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1) }
    p.w, p.h = p.area[3] - p.area[1], p.area[4] - p.area[2]
    summarize(p, entities or {})
    p.renders = draw(p, player, surface)
  end
  l[#l + 1] = p
  return wire(p)
end

--- Same as a real selection, for tests and the UI loop: a position {x,y} is a click, an area a drag.
function M.simulate(player, where)
  local surface = player.surface
  local area
  if where.left_top or (type(where[1]) == "table") then
    local lt, rb = where.left_top or where[1], where.right_bottom or where[2]
    area = { left_top = { x = lt.x or lt[1], y = lt.y or lt[2] }, right_bottom = { x = rb.x or rb[1], y = rb.y or rb[2] } }
  else
    local x, y = where.x or where[1], where.y or where[2]
    area = { left_top = { x = x - 0.05, y = y - 0.05 }, right_bottom = { x = x + 0.05, y = y + 0.05 } }
  end
  local entities = surface.find_entities_filtered{ area = { area.left_top, area.right_bottom } }
  return M.add(player, surface, area, entities)
end

function M.list(player)
  local out = {}
  for _, p in ipairs(list(player)) do out[#out + 1] = wire(p) end
  return out
end

function M.get(player, n)
  return list(player)[n]
end

function M.clear(player)
  for _, p in ipairs(list(player)) do erase(p) end
  storage.kc.pointers[player.index] = {}
end

--- The pointers for the question being sent; clears them.
function M.take(player)
  local out = M.list(player)
  M.clear(player)
  return out
end

--- Put the pointer tool in the player's hand.
function M.give_tool(player)
  if not player.clear_cursor() then return false end
  return player.cursor_stack.set_stack{ name = M.TOOL }
end

--- Take the tool out of the hand, if it is there.
function M.drop_tool(player)
  if not (player and player.valid) then return end
  local c = player.cursor_stack
  if c and c.valid_for_read and c.name == M.TOOL then player.clear_cursor() end
end

--- Short label for the window: "@1 assembling-machine-2 · electronic-circuit", "@2 box 30×20 · 45".
function M.label(p)
  if p.kind == "box" then
    return "@" .. p.n .. " box " .. p.w .. "×" .. p.h .. " · " .. p.count
  end
  local e = p.entity
  if not e then return "@" .. p.n .. " ground (" .. p.x .. ", " .. p.y .. ")" end
  local s = "@" .. p.n .. " " .. (e.ghost and (e.ghost .. " (ghost)") or e.name)
  if e.recipe then s = s .. " · " .. e.recipe end
  return s
end

return M
