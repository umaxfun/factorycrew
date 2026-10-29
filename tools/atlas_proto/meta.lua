local s = game.surfaces["nauvis"]
local used, recipes = {}, {}
for _, m in pairs(s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace"}}) do
  local r = m.get_recipe()
  if not r and m.type == "furnace" and m.previous_recipe then r = m.previous_recipe.name end
  if r then used[type(r) == "string" and r or r.name] = true end
end
for n in pairs(used) do
  local r = prototypes.recipe[n]
  local ing, pro = {}, {}
  for _, i in pairs(r.ingredients) do ing[#ing + 1] = {i.name, i.amount, i.type} end
  for _, p in pairs(r.products) do
    local a = p.amount or ((p.amount_min + p.amount_max) / 2)
    pro[#pro + 1] = {p.name, a * (p.probability or 1), p.type}
  end
  recipes[n] = {energy = r.energy, ing = ing, pro = pro}
end
local drills = {}
for _, d in pairs(s.find_entities_filtered{force = "player", type = "mining-drill"}) do
  local t = d.mining_target
  drills[#drills + 1] = {d.position.x, d.position.y, t and t.name or "-"}
end
local ts = {}
for k, v in pairs(defines.train_state) do ts[tostring(v)] = k end
local st = {}
for k, v in pairs(defines.entity_status) do st[#st + 1] = k end
helpers.write_file("atlas/meta.json", helpers.table_to_json({recipes = recipes, drills = drills, train_state = ts}))
rcon.print("ok")
