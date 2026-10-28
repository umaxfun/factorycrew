local protocol = require("scripts.protocol")

local POLE_RECIPES = { "small-electric-pole", "medium-electric-pole", "big-electric-pole", "substation" }

local function with_recipes(enabled, fn)
  local force = game.forces.player
  local saved = {}
  for _, r in ipairs(POLE_RECIPES) do
    saved[r] = force.recipes[r].enabled
    force.recipes[r].enabled = enabled[r] or false
  end
  local ok, err = pcall(fn)
  for r, v in pairs(saved) do force.recipes[r].enabled = v end
  if not ok then error(err, 0) end
end

local function q(lua)
  local r = protocol.run_query(lua)
  assert(r.ok, r.message)
  return r.data
end

describe("kc.poles (query helper)", function()
  it("picks the small pole when only it is researched", function()
    with_recipes({ ["small-electric-pole"] = true }, function()
      local p = q("return kc.poles()")
      assert(p.cover == "small-electric-pole" and p.reach == "small-electric-pole", serpent.line(p))
    end)
  end)

  it("prefers the larger supply area for cover and the longer wire for reach", function()
    with_recipes({ ["small-electric-pole"] = true, ["medium-electric-pole"] = true, ["big-electric-pole"] = true },
      function()
        local p = q("return kc.poles()")
        assert(p.cover == "medium-electric-pole" and p.reach == "big-electric-pole", serpent.line(p))
      end)
  end)

  it("takes substations for cover once they are researched", function()
    with_recipes({ ["small-electric-pole"] = true, ["medium-electric-pole"] = true, ["big-electric-pole"] = true,
                   substation = true }, function()
      local p = q("return kc.poles()")
      assert(p.cover == "substation" and p.reach == "big-electric-pole", serpent.line(p))
      local sub = p.poles[1]
      assert(sub.name == "substation" and sub.supply == 9 and sub.size == 2 and sub.craftable, serpent.line(sub))
    end)
  end)

  it("ignores poles that are not researched", function()
    with_recipes({ ["small-electric-pole"] = true }, function()
      for _, p in ipairs(q("return kc.poles()").poles) do
        assert(p.name ~= "substation" or p.have > 0, serpent.line(p))
      end
    end)
  end)
end)

describe("kc.nearest_pole (query helper)", function()
  it("finds the closest real pole and ignores ghosts", function()
    local s = game.surfaces[1]
    local far = s.create_entity{ name = "small-electric-pole", position = { 300, 300 }, force = "player" }
    local near = s.create_entity{ name = "medium-electric-pole", position = { 290, 300 }, force = "player" }
    local ghost = s.create_entity{ name = "entity-ghost", inner_name = "small-electric-pole", position = { 281, 300 },
                                   force = "player" }
    local r = q("return kc.nearest_pole(surface, {x = 280, y = 300}, 50)")
    far.destroy(); near.destroy(); ghost.destroy()
    assert(r and r.name == "medium-electric-pole" and r.position.x == 290.5, serpent.line(r))
    assert(q("return kc.nearest_pole(surface, {x = 280, y = 300}, 50)") == nil)
  end)
end)
