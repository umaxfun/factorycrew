"""Game setups for the live-panel tests, shared by level 2 (test_panels_game.py) and the e2e.

Science: production statistics with known amounts of science packs made in the last minute.
Logistics: one robot network whose requester chests ask for iron (in storage, but no robots yet
to bring it), copper and steel (none anywhere in the network). Adding robots later delivers the
iron, which a live panel must pick up without anyone asking again.
"""

SCIENCE = {"automation-science-pack": 120, "logistic-science-pack": 60, "military-science-pack": 15}

# on_flow adds to the force's production statistics as if the packs were crafted now
SCIENCE_SETUP = """
local st = game.forces.player.get_item_production_statistics(game.surfaces[1])
local made = {}
for name, n in pairs(PACKS) do st.on_flow(name, n) made[name] = n end
return made
""".replace("PACKS", "{" + ", ".join(f'["{k}"] = {v}' for k, v in SCIENCE.items()) + "}")

LOGI_X, LOGI_Y = 300, 0
# requested per item, summed over two requester chests; what storage holds
REQUESTS = {"iron-plate": 100 + 50, "copper-plate": 40, "steel-plate": 20}
STORED = {"iron-plate": 1000}

LOGI_SETUP = """
local s, f = game.surfaces[1], game.forces.player
local X, Y = LX, LY
s.request_to_generate_chunks({X, Y}, 2)
s.force_generate_chunk_requests()
local tiles = {}
for x = X - 6, X + 26 do for y = Y - 10, Y + 10 do tiles[#tiles + 1] = {name = "refined-concrete", position = {x, y}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{area = {{X - 6, Y - 10}, {X + 26, Y + 10}}}) do
  if e.type ~= "character" then e.destroy() end
end
local function make(name, x, y) return s.create_entity{name = name, position = {x, y}, force = "player"} end
local eei = make("electric-energy-interface", X, Y)
eei.electric_buffer_size = 1e10 eei.power_production = 1e8 / 60 eei.energy = 1e10
make("substation", X + 2, Y)
make("substation", X + 18, Y)
local rp = make("roboport", X + 8, Y)
rp.energy = 1e8
local store = make("storage-chest", X + 12.5, Y + 4.5)
store.insert{name = "iron-plate", count = 1000}
local function requester(x, y, asks)
  local r = make("requester-chest", x, y)
  local sec = r.get_requester_point().get_section(1) or r.get_requester_point().add_section()
  for i, a in ipairs(asks) do
    sec.set_slot(i, {value = {type = "item", name = a[1], quality = "normal", comparator = "="}, min = a[2]})
  end
  return r
end
requester(X + 14.5, Y - 4.5, {{"iron-plate", 100}, {"copper-plate", 40}})
requester(X + 16.5, Y - 4.5, {{"iron-plate", 50}, {"steel-plate", 20}})
return {roboport = {rp.position.x, rp.position.y}, network = rp.logistic_network and rp.logistic_network.network_id}
""".replace("LX", str(LOGI_X)).replace("LY", str(LOGI_Y))

ADD_ROBOTS = """
local rp = game.surfaces[1].find_entity("roboport", {RX, RY})
return rp.get_inventory(defines.inventory.roboport_robot).insert{name = "logistic-robot", count = 20}
"""

REQUESTER_CONTENTS = """
local out = {}
for _, r in pairs(game.surfaces[1].find_entities_filtered{name = "requester-chest", area = {{LX, LY - 10}, {LX + 26, LY + 10}}}) do
  for _, it in pairs(r.get_inventory(defines.inventory.chest).get_contents()) do
    out[it.name] = (out[it.name] or 0) + it.count
  end
end
return out
""".replace("LX", str(LOGI_X)).replace("LY", str(LOGI_Y))


def add_robots(lua, roboport):
    return lua(ADD_ROBOTS.replace("RX", str(roboport[0])).replace("RY", str(roboport[1])))


def view_rows(view):
    """Every table row of a panel view as a list of cells, plus text blocks as one-cell rows."""
    out = []
    for b in view or []:
        if isinstance(b, dict) and "rows" in b:
            out.extend(r if isinstance(r, list) else [r] for r in (b.get("rows") or []))
        elif isinstance(b, (str, int, float)):
            out.append([b])
        elif isinstance(b, dict):
            out.append([b])
    return out


def cell_items(cell):
    """Item names a cell mentions: an icon cell, or [item=x] rich text."""
    import re
    if isinstance(cell, dict):
        names = [cell[k] for k in ("item", "fluid", "signal") if isinstance(cell.get(k), str)]
        if isinstance(cell.get("text"), str):
            names += re.findall(r"\[item=([\w-]+)", cell["text"])
        return names
    if isinstance(cell, str):
        return re.findall(r"\[item=([\w-]+)", cell) + [w for w in re.findall(r"[a-z]+(?:-[a-z]+)+", cell)]
    return []


def cell_numbers(cell):
    import re
    if isinstance(cell, bool):
        return []
    if isinstance(cell, (int, float)):
        return [float(cell)]
    if isinstance(cell, dict):
        nums = []
        for k in ("text", "bar", "caption"):
            nums += cell_numbers(cell.get(k)) if k in cell else []
        return nums
    if isinstance(cell, str):
        # plain numbers; rich-text tags like [color=1,0.3,0.3] and [item=...] are not values
        bare = re.sub(r"\[[^\]]*\]", " ", cell)
        out = []
        for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*([kKM]?)", bare):
            v = float(m.group(1)) * {"k": 1e3, "K": 1e3, "M": 1e6}.get(m.group(2), 1)
            out.append(v)
        return out
    return []


def rows_about(view, item):
    """Rows of a view that name `item`, with all numbers in them."""
    found = []
    for row in view_rows(view):
        human = item.replace("-", " ")
        texts = [c if isinstance(c, str) else (c.get("text") if isinstance(c, dict) else None) for c in row]
        if any(item in cell_items(c) for c in row) or any(isinstance(t, str) and human in t.lower() for t in texts):
            nums = [n for c in row for n in cell_numbers(c)]
            found.append((row, nums))
    return found
