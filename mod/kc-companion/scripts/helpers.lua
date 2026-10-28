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
