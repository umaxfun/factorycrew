-- Helpers for agent queries, available as `kc` in factorio_query's environment.
-- They live in the mod (not in the agent's prompt) so level 1 tests run them on the real API.
local M = {}

--- Whether the force can make `item` now: some enabled recipe produces it.
local function craftable(force, item)
  local recipes = prototypes.get_recipe_filtered{
    { filter = "has-product-item", elem_filters = { { filter = "name", name = item } } },
  }
  for name in pairs(recipes) do
    local r = force.recipes[name]
    if r and r.enabled then return true end
  end
  return false
end

--- Electric poles this force can build now (an enabled recipe makes one, or the player carries some).
--- Returns {cover = name with the largest supply area, reach = name with the longest wire reach,
--- poles = {{name, item, supply, reach, size, craftable, have}, ...} best cover first}.
--- Works for modded poles too: it reads the prototypes, not a list of vanilla names.
function M.poles(force, player)
  force = force or game.forces.player
  player = player or game.connected_players[1]
  local list = {}
  for name, proto in pairs(prototypes.get_entity_filtered{ { filter = "type", type = "electric-pole" } }) do
    local place = proto.items_to_place_this
    local item = place and place[1] and place[1].name
    if item and not proto.hidden then
      local can = craftable(force, item)
      local have = (player and player.valid) and player.get_item_count(item) or 0
      if can or have > 0 then
        list[#list + 1] = {
          name = name, item = item, supply = proto.get_supply_area_distance(), reach = proto.get_max_wire_distance(),
          size = proto.tile_width, craftable = can, have = have,
        }
      end
    end
  end
  table.sort(list, function(a, b)
    if a.supply ~= b.supply then return a.supply > b.supply end
    if a.reach ~= b.reach then return a.reach > b.reach end
    return a.name < b.name
  end)
  local reach
  for _, p in ipairs(list) do
    if not reach or p.reach > reach.reach or (p.reach == reach.reach and p.supply > reach.supply) then reach = p end
  end
  return { cover = list[1] and list[1].name, reach = reach and reach.name, poles = list }
end

--- Place ghosts the way the player's ghost cursor would: an item is refused if it overlaps an
--- entity, another ghost (including one placed earlier in this same call) or unbuildable ground.
--- items: {{name, x, y, direction?}, ...} (position = entity center). Nothing is placed for refused
--- items. Returns {placed = n, failed = {{index, name, x, y, reason}, ...}}.
function M.place_ghosts(surface, items, force)
  force = force or game.forces.player
  local placed, failed = 0, {}
  for i, it in ipairs(items or {}) do
    local reason
    if not (it.name and prototypes.entity[it.name]) then
      reason = "unknown entity " .. tostring(it.name)
    else
      local pos = { x = it.x or (it.position and it.position.x), y = it.y or (it.position and it.position.y) }
      -- snap to the entity's grid as the game does: odd sizes sit on tile centers (x.5),
      -- even sizes (2x2 substation) on tile corners (integers). 298.0 for a pipe means 298.5.
      local proto = prototypes.entity[it.name]
      local function snap(v, size) return (size % 2 == 1) and (math.floor(v) + 0.5) or math.floor(v + 0.5) end
      if pos.x and pos.y then
        local w, h = proto.tile_width, proto.tile_height
        local d = it.direction or 0
        if d == defines.direction.east or d == defines.direction.west then w, h = h, w end
        pos = { x = snap(pos.x, w), y = snap(pos.y, h) }
      end
      local args = { name = it.name, position = pos, direction = it.direction or defines.direction.north,
                     force = force, build_check_type = defines.build_check_type.manual_ghost }
      if not surface.can_place_entity(args) then
        reason = "blocked (overlaps an entity or ghost, or bad ground)"
      else
        local g = surface.create_entity{ name = "entity-ghost", inner_name = it.name, position = pos,
                                         direction = args.direction, force = force }
        if g then placed = placed + 1 else reason = "create_entity refused" end
      end
    end
    if reason then failed[#failed + 1] = { index = i, name = it.name, x = it.x, y = it.y, reason = reason } end
  end
  local out = { placed = placed, failed = failed }
  -- Power check on what was just placed: the hint lands in the tool result, where the agent
  -- reads it, instead of relying on it remembering the "power by default" rule.
  local unpowered = M.unpowered(surface, items, force)
  if unpowered > 0 then
    out.unpowered = unpowered
    out.hint = unpowered .. " electric ghosts are outside every pole's supply area. Power them now, in this turn: " ..
      "kc.poles() for the best pole, kc.nearest_pole() for the grid, place poles with kc.place_ghosts."
  end
  if #failed > 0 then
    out.failed_hint = "Some items were refused; move them (recompute the layout) and place them, do not leave the design partial."
  end
  return out
end

--- How many of `items` (electric entities only) sit outside the supply area of every pole and
--- pole ghost of the force. Supply area is a square of half-size supply_area_distance.
function M.unpowered(surface, items, force)
  force = force or game.forces.player
  local n = 0
  for _, it in ipairs(items or {}) do
    local proto = it.name and prototypes.entity[it.name]
    if proto and proto.type ~= "electric-pole" and proto.electric_energy_source_prototype then
      local x = it.x or (it.position and it.position.x)
      local y = it.y or (it.position and it.position.y)
      local covered = false
      for _, e in pairs(surface.find_entities_filtered{ position = { x, y }, radius = 64, force = force,
                                                         type = { "electric-pole", "entity-ghost" } }) do
        local pname = e.type == "entity-ghost" and e.ghost_name or e.name
        local pp = prototypes.entity[pname]
        if pp.type == "electric-pole" then
          local d = pp.get_supply_area_distance()
          local hw, hh = (proto.tile_width or 1) / 2, (proto.tile_height or 1) / 2
          if math.abs(e.position.x - x) <= d + hw and math.abs(e.position.y - y) <= d + hh then
            covered = true
            break
          end
        end
      end
      if not covered then n = n + 1 end
    end
  end
  return n
end

--- The nearest existing pole of the force within `radius` of `position` (real poles only, not ghosts):
--- {name, position, distance, network_id} or nil. Chain new pole ghosts from here.
function M.nearest_pole(surface, position, radius, force)
  force = force or game.forces.player
  local best, best_d
  for _, e in pairs(surface.find_entities_filtered{
    type = "electric-pole", force = force, position = position, radius = radius or 200,
  }) do
    local dx, dy = e.position.x - position.x, e.position.y - position.y
    local d = math.sqrt(dx * dx + dy * dy)
    if not best_d or d < best_d then best, best_d = e, d end
  end
  if not best then return nil end
  return {
    name = best.name, position = { x = best.position.x, y = best.position.y },
    distance = math.floor(best_d * 10 + 0.5) / 10, network_id = best.electric_network_id,
  }
end

return M
