local protocol = require("scripts.protocol")
local gui = require("scripts.gui")
local pointers = require("scripts.pointers")

-- Pointers made the way the tool makes them (pointers.add with the selection's area and entities),
-- far from the spawn so other suites' entities do not get in.
local player, surface
local X, Y = 400, 400

local function find(el, name)
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find(c, name)
    if f then return f end
  end
end

local function select(x0, y0, x1, y1)
  local area = { left_top = { x = x0, y = y0 }, right_bottom = { x = x1, y = y1 } }
  return pointers.add(player, surface, area, surface.find_entities_filtered{ area = { area.left_top, area.right_bottom } })
end

local function click(x, y) return select(x - 0.05, y - 0.05, x + 0.05, y + 0.05) end

local function renders(n)
  local p = pointers.get(player, n)
  local live = 0
  for _, id in ipairs(p.renders) do
    local o = rendering.get_object_by_id(id)
    if o and o.valid then live = live + 1 end
  end
  return live
end

describe("pointers", function()
  before_each(function()
    player = game.players[1]
    surface = game.surfaces[1]
    protocol.init_storage()
    storage.kc.tabs, storage.kc.tab_order, storage.kc.ui = {}, {}, {}
    if player then pointers.clear(player) end
    surface.request_to_generate_chunks({ X, Y }, 2)
    surface.force_generate_chunk_requests()
    for _, e in pairs(surface.find_entities_filtered{ area = { { X - 40, Y - 40 }, { X + 40, Y + 40 } } }) do
      if e.type ~= "character" then e.destroy() end
    end
    surface.set_tiles({ { name = "grass-1", position = { X, Y } } })
  end)

  it("has a player to point with", function()
    assert(player and player.valid, "no LuaPlayer in this run: the pointer tests below did nothing")
  end)

  it("a click on a machine is that machine, with its recipe, and the ore under it", function()
    if not player then return end
    surface.create_entity{ name = "iron-ore", position = { X + 1.5, Y + 1.5 }, amount = 500 } -- the clicked tile
    local am = surface.create_entity{ name = "assembling-machine-2", position = { X + 0.5, Y + 0.5 }, force = "player" }
    am.set_recipe("iron-gear-wheel")
    local p = click(X + 1.2, Y + 1.1) -- off-centre, still inside the 3×3 machine
    assert(p and p.kind == "point" and p.n == 1, serpent.line(p))
    assert(p.entity.name == "assembling-machine-2" and p.entity.recipe == "iron-gear-wheel", serpent.line(p))
    assert(p.entity.x == X + 0.5 and p.entity.y == Y + 0.5, serpent.line(p.entity))
    assert(p.entity.status, "no status: " .. serpent.line(p.entity))
    assert(p.resource == "iron-ore", serpent.line(p))
    assert(renders(1) == 2, "the circle and the @1 label are not drawn")
  end)

  it("a click on ore is the ore with its amount", function()
    if not player then return end
    surface.create_entity{ name = "stone", position = { X + 6.5, Y + 0.5 }, amount = 321 }
    local p = click(X + 6.9, Y + 0.2)
    assert(p.entity and p.entity.name == "stone" and p.entity.amount == 321 and p.resource == nil, serpent.line(p))
  end)

  it("a click on bare ground is the ground with its tile", function()
    if not player then return end
    local p = click(X + 0.5, Y + 0.5)
    assert(p.kind == "point" and p.entity == nil and p.tile == "grass-1", serpent.line(p))
  end)

  it("a drag is a box with counts, recipes and ore", function()
    if not player then return end
    for i = 0, 2 do
      local am = surface.create_entity{ name = "assembling-machine-1", position = { X + 0.5 + i * 3, Y + 10.5 }, force = "player" }
      am.set_recipe("copper-cable")
    end
    for i = 0, 4 do surface.create_entity{ name = "transport-belt", position = { X + 0.5 + i, Y + 13.5 }, force = "player" } end
    surface.create_entity{ name = "copper-ore", position = { X + 5.5, Y + 15.5 }, amount = 700 }
    local p = select(X - 1, Y + 8, X + 10, Y + 17)
    assert(p.kind == "box" and p.w == 11 and p.h == 9, serpent.line(p))
    assert(p.count == 8, serpent.line(p))
    assert(p.counts[1].name == "transport-belt" and p.counts[1].n == 5, serpent.line(p.counts))
    assert(p.recipes[1].name == "copper-cable" and p.recipes[1].n == 3, serpent.line(p.recipes))
    assert(p.resources[1].name == "copper-ore" and p.resources[1].amount == 700, serpent.line(p.resources))
  end)

  it("numbers pointers, caps them, and the next question takes and clears them", function()
    if not player then return end
    for i = 1, pointers.LIMIT do assert(click(X + i, Y).n == i) end
    local extra, why = click(X, Y + 5)
    assert(extra == nil and why == "limit")
    local ids = pointers.get(player, 1).renders
    local msg = protocol.ask("General", "build like @1 at @2", player, pointers.take(player))
    assert(#msg.pointers == pointers.LIMIT and msg.pointers[2].n == 2, serpent.line(msg.pointers))
    assert(msg.pointers[1].renders == nil, "render ids leaked onto the wire")
    assert(#pointers.list(player) == 0)
    for _, id in ipairs(ids) do
      local o = rendering.get_object_by_id(id)
      assert(not (o and o.valid), "drawing left on the ground after send")
    end
    assert(helpers.json_to_table(protocol.encode(msg)[1]).pointers[1].kind == "point")
    -- without pointers the ask has no pointers field
    assert(protocol.ask("General", "hi", player, pointers.take(player)).pointers == nil)
  end)

  it("the window lists pointers, puts @n into the question and sends them with it", function()
    if not player then return end
    if player.gui.screen.kc_main then player.gui.screen.kc_main.destroy() end
    local sent = {}
    local function send(m) sent[#sent + 1] = m end
    gui.on_pointer(player, click(X + 0.5, Y + 0.5))
    local root = player.gui.screen.kc_main
    assert(root, "a pointer did not open the window")
    local field = find(root, "kc_input")
    assert(field.text == "@1 ", field.text)
    field.text = "why is @1 empty"
    gui.on_pointer(player, select(X - 3, Y - 3, X + 3, Y + 3))
    assert(field.text == "why is @1 empty @2 ", field.text)
    assert(find(root, "kc_pointers_row").visible)
    gui.on_confirmed({ element = field, player_index = player.index }, send)
    assert(#sent == 1 and #sent[1].pointers == 2 and sent[1].text == "why is @1 empty @2", serpent.line(sent[1]))
    assert(not find(player.gui.screen.kc_main, "kc_pointers_row").visible, "pointers row stayed after send")
    root.destroy()
  end)

  it("the Point button puts the tool in the hand", function()
    if not player then return end
    if not player.character then return end -- headless: no hand to hold it
    assert(pointers.give_tool(player))
    assert(player.cursor_stack.valid_for_read and player.cursor_stack.name == "kc-pointer")
    pointers.drop_tool(player) -- what a selection and Q do
    assert(not player.cursor_stack.valid_for_read, "the tool stayed in the hand")
    local inv = player.get_main_inventory() -- nil in the headless run
    assert(not inv or inv.get_item_count("kc-pointer") == 0, "the tool went into the inventory")
  end)
end)
