-- Level 1 tests of kc.circuits: build small circuits, let them run a few ticks, read them back.
-- Each test also writes its snapshot to script-output/kc-circuits/*.json: the recorded fixtures the
-- Python tests of kc.circuits read (copied into tests/unit/fixtures/circuits/).
local protocol = require("scripts.protocol")
local circuits = require("scripts.circuits")

local W = defines.wire_connector_id
local V21 = (script.active_mods.base or ""):sub(1, 3) == "2.1"

local function record(name, data)
  local ok = pcall(helpers.write_file, "kc-circuits/" .. name .. ".json", helpers.table_to_json(data), false)
  return ok
end

local function clear(s, x0, y0, x1, y1)
  for _, e in pairs(s.find_entities_filtered{ area = { { x0, y0 }, { x1, y1 } } }) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  local tiles = {}
  for x = x0, x1 - 1 do for y = y0, y1 - 1 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
  s.set_tiles(tiles)
end

local function make(s, name, x, y, extra)
  local spec = { name = name, position = { x, y }, force = "player" }
  for k, v in pairs(extra or {}) do spec[k] = v end
  local e = s.create_entity(spec)
  assert(e, "could not create " .. name)
  return e
end

--- Power for the combinators: an energy interface and a substation covering the box.
local function power(s, x, y)
  local ei = make(s, "electric-energy-interface", x, y)
  pcall(function() ei.electric_buffer_size = 1e10 end)
  pcall(function() ei.power_production = 1e9 end)
  make(s, "substation", x + 2, y)
end

local function wire(a, ca, b, cb)
  local ok = a.get_wire_connector(ca, true).connect_to(b.get_wire_connector(cb, true), false)
  assert(ok, "wire " .. a.name .. " -> " .. b.name)
end

local function sig(t, n) return { type = t, name = n, quality = "normal" } end

local function by_name(snap, name)
  for _, e in ipairs(snap.entities) do if e.name == name then return e end end
  error("no " .. name .. " in " .. serpent.line(snap))
end

local function copy(x) -- serpent prints a table met twice as a placeholder
  if type(x) ~= "table" then return x end
  local t = {}
  for k, v in pairs(x) do t[k] = copy(v) end
  return t
end
local function line(x) return serpent.line(copy(x), { comment = false }) end

local function eq(a, b, what)
  assert(line(a) == line(b), (what or "") .. ": " .. line(a) .. " ~= " .. line(b))
end

local function set_constant(cc, list, section)
  local cb = cc.get_or_create_control_behavior()
  local idx = section or 1
  local sec = (cb.sections_count >= idx) and cb.get_section(idx) or cb.add_section()
  for i, v in ipairs(list) do sec.set_slot(i, { value = v[1], min = v[2] }) end
  return cb, sec
end

describe("kc.circuits", function()
  it("reads a constant -> decider -> lamp circuit with networks and wires", function()
    local s = game.surfaces[1]
    clear(s, 600, 600, 620, 620)
    power(s, 608, 609)
    local cc = make(s, "constant-combinator", 603.5, 603.5)
    local ccb, sec1 = set_constant(cc, { { sig("item", "iron-plate"), 400 }, { sig("virtual", "signal-A"), 5 } })
    local sec2 = ccb.add_section()
    sec2.set_slot(1, { value = sig("virtual", "signal-B"), min = 9 })
    sec2.active = false
    local sec3 = ccb.add_section()
    sec3.set_slot(1, { value = sig("item", "copper-plate"), min = 3 })
    sec3.multiplier = 2
    local dec = make(s, "decider-combinator", 605.5, 604)
    dec.get_or_create_control_behavior().parameters = {
      conditions = {
        { first_signal = sig("item", "iron-plate"), comparator = ">", constant = 100 },
        { first_signal = sig("virtual", "signal-A"), comparator = "=", constant = 5, compare_type = "and" },
        { first_signal = sig("virtual", "signal-B"), comparator = "≥", second_signal = sig("virtual", "signal-A"),
          compare_type = "or", first_signal_networks = { red = true, green = false } },
      },
      outputs = {
        { signal = sig("virtual", "signal-L"), copy_count_from_input = false, constant = 1 },
        { signal = sig("item", "iron-plate"), copy_count_from_input = true, networks = { red = true, green = false } },
      },
    }
    local lamp = make(s, "small-lamp", 607.5, 603.5)
    local lcb = lamp.get_or_create_control_behavior()
    lcb.circuit_enable_disable = true
    lcb.circuit_condition = { first_signal = sig("virtual", "signal-L"), comparator = ">", constant = 0 }
    lcb.use_colors = true
    lcb.color_mode = defines.control_behavior.lamp.color_mode.color_mapping
    wire(cc, W.circuit_red, dec, W.combinator_input_red)
    wire(dec, W.combinator_output_green, lamp, W.circuit_green)

    after_ticks(5, function()
      -- through the query environment, the way Kira calls it
      local r = protocol.run_query("return kc.circuits(surface, {entities = {" .. lamp.unit_number .. "}})")
      assert(r.ok, r.message)
      local snap = r.data
      log("kc-circuits query surface: " .. tostring(game.players[1] and game.players[1].surface.name) ..
          " lamp by unit: " .. tostring(game.get_entity_by_unit_number(lamp.unit_number)))
      assert(snap.v == 1 and snap.tick == game.tick and snap.truncated == false, line(snap))
      assert(snap.game_version == script.active_mods.base, snap.game_version)
      eq(#snap.entities, 3, "followed the wires from the lamp")
      eq(snap.entities[1].id, lamp.unit_number, "seed first")
      eq(#snap.wires, 2, "wires")

      local c, d, l = by_name(snap, "constant-combinator"), by_name(snap, "decider-combinator"), by_name(snap, "small-lamp")
      eq({ c.kind, d.kind, l.kind }, { "constant", "decider", "other" })
      eq({ c.x, c.y, d.x, d.y, d.direction }, { 603.5, 603.5, 605.5, 604, 0 })
      local red = c.connectors.red
      assert(red and d.connectors.in_red == red and d.connectors.in_green == nil, line({ c.connectors, d.connectors }))
      local green = d.connectors.out_green
      assert(green and l.connectors.green == green and d.connectors.out_red == nil, line(d.connectors))
      eq(c.params, { enabled = true, sections = {
        { active = true, multiplier = 1, signals = { ["item/iron-plate"] = 400, ["virtual/signal-A"] = 5 } },
        { active = false, multiplier = 1, signals = { ["virtual/signal-B"] = 9 } },
        { active = true, multiplier = 2, signals = { ["item/copper-plate"] = 3 } },
      } }, "constant")
      local both, red_only = { red = true, green = true }, { red = true, green = false }
      eq(d.params.conditions, {
        { first = "item/iron-plate", comparator = ">", constant = 100, first_networks = both, second_networks = both, join = "and" },
        { first = "virtual/signal-A", comparator = "=", constant = 5, first_networks = both, second_networks = both, join = "and" },
        { first = "virtual/signal-B", comparator = ">=", second = "virtual/signal-A", first_networks = red_only,
          second_networks = both, join = "or" },
      }, "decider conditions")
      eq(d.params.outputs, {
        { signal = "virtual/signal-L", copy_count_from_input = false, networks = both, constant = 1 },
        { signal = "item/iron-plate", copy_count_from_input = true, networks = red_only, constant = 1 },
      }, "decider outputs")
      eq(snap.networks[red], { color = "red", signals = { ["item/iron-plate"] = 400, ["virtual/signal-A"] = 5,
                                                          ["item/copper-plate"] = 6 } }, "red network")
      eq(snap.networks[green], { color = "green", signals = { ["virtual/signal-L"] = 1, ["item/iron-plate"] = 400 } },
         "green network")
      local ctl = l.control
      eq(ctl.enable_condition, { first = "virtual/signal-L", comparator = ">", constant = 0, first_networks = both,
                                 second_networks = both, join = "and" }, "lamp enable")
      assert(ctl.raw.circuit_enable_disable == true and ctl.raw.use_colors == true, line(ctl.raw))
      assert(ctl.raw.color_mode == "color_mapping" and ctl.raw.circuit_condition.fulfilled == true, line(ctl.raw))
      local w = snap.wires
      table.sort(w, function(a, b) return a.color < b.color end)
      eq(w[1], { a = dec.unit_number, a_connector = "out_green", b = lamp.unit_number, b_connector = "green", color = "green" })
      eq(w[2], { a = cc.unit_number, a_connector = "red", b = dec.unit_number, b_connector = "in_red", color = "red" })

      -- JSON round trip: what factorio_query hands the Python side
      local back = helpers.json_to_table(helpers.table_to_json(snap))
      assert(back and back.networks[red].signals["item/iron-plate"] == 400, "json round trip")
      -- follow = false keeps just the seed; area finds all three
      local one = circuits.read(s, { entities = { lamp }, follow = false })
      eq(#one.entities, 1, "follow=false")
      local area = circuits.read(s, { area = { { 600, 600 }, { 610, 610 } } })
      eq(#area.entities, 3, "area")
      eq(area.entities[1].name, "constant-combinator", "area seeds sorted by y, x")
      local around = circuits.read(s, { position = { x = 605.5, y = 604 }, radius = 1, follow = false })
      eq(#around.entities, 1, "position + radius")
      record("constant-decider-lamp", snap)
    end)
  end)

  it("reads an arithmetic chain and its computed values", function()
    local s = game.surfaces[1]
    clear(s, 640, 600, 660, 620)
    power(s, 646, 609)
    local cc = make(s, "constant-combinator", 641.5, 603.5)
    set_constant(cc, { { sig("virtual", "signal-A"), 6 } })
    local a1 = make(s, "arithmetic-combinator", 643.5, 604)
    a1.get_or_create_control_behavior().parameters = {
      first_signal = sig("virtual", "signal-A"), operation = "*", second_constant = 7,
      output_signal = sig("virtual", "signal-B"), first_signal_networks = { red = false, green = true },
    }
    local a2 = make(s, "arithmetic-combinator", 645.5, 604)
    a2.get_or_create_control_behavior().parameters = {
      first_signal = sig("virtual", "signal-B"), operation = "/", second_constant = 2, output_signal = sig("virtual", "signal-C"),
    }
    local a3 = make(s, "arithmetic-combinator", 647.5, 604)
    a3.get_or_create_control_behavior().parameters = {
      first_constant = 100, operation = "-", second_signal = sig("virtual", "signal-C"),
      output_signal = sig("virtual", "signal-D"),
    }
    local a4 = make(s, "arithmetic-combinator", 649.5, 604)
    a4.get_or_create_control_behavior().parameters = {
      first_signal = sig("virtual", "signal-each"), operation = "<<", second_constant = 1,
      output_signal = sig("virtual", "signal-each"),
    }
    wire(cc, W.circuit_green, a1, W.combinator_input_green)
    wire(a1, W.combinator_output_red, a2, W.combinator_input_red)
    wire(a2, W.combinator_output_red, a3, W.combinator_input_red)
    wire(a3, W.combinator_output_red, a4, W.combinator_input_red)

    after_ticks(8, function()
      local snap = circuits.read(s, { entities = { cc } })
      eq(#snap.entities, 5, "chain")
      local both = { red = true, green = true }
      local p1 = snap.entities[2].params
      eq(p1, { first = "virtual/signal-A", first_networks = { red = false, green = true }, operation = "*",
               second_constant = 7, second_networks = both, output = "virtual/signal-B" }, "a1")
      local p3 = snap.entities[4].params
      eq(p3, { first_constant = 100, first_networks = both, operation = "-", second = "virtual/signal-C",
               second_networks = both, output = "virtual/signal-D" }, "a3")
      eq(snap.entities[5].params.operation, "<<")
      eq(snap.entities[5].params.output, "virtual/signal-each")
      local function net(e, c) return snap.networks[e.connectors[c]].signals end
      eq(net(snap.entities[2], "out_red"), { ["virtual/signal-B"] = 42 }, "A*7")
      eq(net(snap.entities[3], "out_red"), { ["virtual/signal-C"] = 21 }, "B/2")
      eq(net(snap.entities[4], "out_red"), { ["virtual/signal-D"] = 79 }, "100-C")
      assert(snap.entities[5].connectors.out_red == nil, "a4 output unwired")
      record("arithmetic-chain", snap)
    end)
  end)

  it("reads a station: train stop, chests, inserters and a belt with their conditions", function()
    local s = game.surfaces[1]
    clear(s, 680, 600, 710, 620)
    local stop = make(s, "train-stop", 691, 601, { direction = defines.direction.north })
    local scb = stop.get_or_create_control_behavior()
    scb.circuit_enable_disable = true
    scb.circuit_condition = { first_signal = sig("item", "iron-plate"), comparator = "<", constant = 1000 }
    scb.send_to_train = true
    scb.read_from_train = true
    scb.read_stopped_train = true
    scb.stopped_train_signal = sig("virtual", "signal-T")
    scb.read_trains_count = true
    scb.trains_count_signal = sig("virtual", "signal-C")
    scb.set_trains_limit = true
    scb.trains_limit_signal = sig("virtual", "signal-L")
    scb.set_priority = true
    scb.priority_signal = sig("virtual", "signal-P")
    local chest = make(s, "steel-chest", 686.5, 604.5)
    chest.insert{ name = "iron-plate", count = 200 }
    local chest2 = make(s, "iron-chest", 688.5, 604.5)
    chest2.insert{ name = "coal", count = 10 }
    local ins = make(s, "fast-inserter", 686.5, 603.5)
    local icb = ins.get_or_create_control_behavior()
    icb.circuit_enable_disable = true
    icb.circuit_condition = { first_signal = sig("item", "iron-plate"), comparator = "<", constant = 500 }
    icb.circuit_read_hand_contents = true
    icb.circuit_hand_read_mode = defines.control_behavior.inserter.hand_read_mode.hold
    icb.circuit_set_stack_size = true
    icb.circuit_stack_control_signal = sig("virtual", "signal-S")
    local belt = make(s, "transport-belt", 684.5, 603.5)
    local bcb = belt.get_or_create_control_behavior()
    bcb.read_contents = true
    bcb.read_contents_mode = defines.control_behavior.transport_belt.content_read_mode.hold
    local limit = make(s, "constant-combinator", 690.5, 604.5)
    set_constant(limit, { { sig("virtual", "signal-L"), 2 } })
    local pole = make(s, "medium-electric-pole", 688.5, 603.5)
    wire(chest, W.circuit_red, ins, W.circuit_red)
    wire(ins, W.circuit_red, pole, W.circuit_red)
    wire(pole, W.circuit_red, stop, W.circuit_red)
    wire(limit, W.circuit_red, pole, W.circuit_red)
    wire(chest2, W.circuit_green, pole, W.circuit_green)
    wire(belt, W.circuit_green, chest2, W.circuit_green)

    after_ticks(3, function()
      local snap = circuits.read(s, { entities = { stop } })
      eq(#snap.entities, 7, "station: " .. line(snap.entities))
      local st = by_name(snap, "train-stop").control
      eq(st.set, { trains_limit = "virtual/signal-L", priority = "virtual/signal-P" }, "stop set")
      eq(st.read, { train_contents = true, stopped_train = "virtual/signal-T", trains_count = "virtual/signal-C" },
         "stop read")
      assert(st.raw.send_to_train == true and st.raw.read_from_train == true, line(st.raw))
      eq(st.enable_condition.first, "item/iron-plate")
      local ic = by_name(snap, "fast-inserter").control
      eq(ic.read, { hand = "hold" }, "inserter read")
      eq(ic.set, { stack_size = "virtual/signal-S" }, "inserter set")
      eq({ ic.enable_condition.comparator, ic.enable_condition.constant }, { "<", 500 })
      eq(by_name(snap, "steel-chest").control.read, { contents = true }, "chest reads contents")
      eq(by_name(snap, "transport-belt").control.read, { contents = "hold" }, "belt")
      local pc = by_name(snap, "medium-electric-pole")
      eq(pc.kind, "other")
      local red = snap.networks[by_name(snap, "train-stop").connectors.red]
      eq(red.color, "red")
      eq(red.signals["item/iron-plate"], 200, "chest contents on the red network")
      eq(red.signals["virtual/signal-L"], 2, "trains limit")
      local green = snap.networks[pc.connectors.green]
      eq(green.signals["item/coal"], 10, "green: " .. line(green))
      eq(#snap.wires, 6, "wires")
      record("station", snap)
    end)
  end)

  it("reads selector combinators in several modes", function()
    local s = game.surfaces[1]
    clear(s, 720, 600, 740, 620)
    power(s, 726, 610)
    local cc = make(s, "constant-combinator", 721.5, 603.5)
    set_constant(cc, { { sig("virtual", "signal-A"), 3 }, { sig("virtual", "signal-B"), 7 }, { sig("virtual", "signal-C"), 5 } })
    local s1 = make(s, "selector-combinator", 723.5, 604)
    s1.get_or_create_control_behavior().parameters = { operation = "select", select_max = true, index_constant = 0 }
    local s2 = make(s, "selector-combinator", 725.5, 604)
    s2.get_or_create_control_behavior().parameters = { operation = "count", count_signal = sig("virtual", "signal-N") }
    local s3 = make(s, "selector-combinator", 727.5, 604)
    s3.get_or_create_control_behavior().parameters = { operation = "random", random_update_interval = 10 }
    local s4 = make(s, "selector-combinator", 729.5, 604)
    s4.get_or_create_control_behavior().parameters = { operation = "stack-size" }
    for _, sc in ipairs({ s1, s2, s3, s4 }) do wire(cc, W.circuit_red, sc, W.combinator_input_red) end
    local probe = make(s, "small-lamp", 723.5, 607.5)
    wire(s1, W.combinator_output_red, probe, W.circuit_red)
    local probe2 = make(s, "small-lamp", 725.5, 607.5)
    wire(s2, W.combinator_output_red, probe2, W.circuit_red)

    after_ticks(4, function()
      local snap = circuits.read(s, { entities = { cc } })
      eq(#snap.entities, 7, "selectors")
      local function sel(e)
        for _, r in ipairs(snap.entities) do if r.id == e.unit_number then return r end end
      end
      eq(sel(s1).kind, "selector")
      local p1 = sel(s1).params
      local cmp = p1.index_comparator
      p1.index_comparator = nil
      eq(p1, { mode = "select", select_max = true, index_constant = 0 }, "select")
      -- index_comparator is in the 2.1.24 API only; 2.1.23 does not return it for a default selector
      assert(V21 or cmp == nil, "index_comparator on 2.0: " .. tostring(cmp))
      p1.index_comparator = cmp
      eq(sel(s2).params, { mode = "count", count_signal = "virtual/signal-N" }, "count")
      eq(sel(s3).params, { mode = "random", random_update_interval = 10 }, "random")
      eq(sel(s4).params, { mode = "stack-size" }, "stack-size")
      eq(snap.networks[sel(s1).connectors.out_red].signals, { ["virtual/signal-B"] = 7 }, "max selected")
      eq(snap.networks[sel(s2).connectors.out_red].signals, { ["virtual/signal-N"] = 3 }, "count")
      record("selectors", snap)
    end)
  end)

  it("reads the control behavior of every circuit entity kind present", function()
    local s = game.surfaces[1]
    clear(s, 760, 600, 880, 640)
    power(s, 790, 630)
    local hub = make(s, "constant-combinator", 761.5, 601.5)
    set_constant(hub, { { sig("virtual", "signal-A"), 1 } })
    local placed, x = {}, 764
    local skipped = {}
    local function try(name, setup)
      if not prototypes.entity[name] then skipped[#skipped + 1] = name .. " (no prototype)"; return end
      local okc, e = pcall(s.create_entity, { name = name, position = { x, 610 }, force = "player" })
      x = x + 7
      if not okc or not e then skipped[#skipped + 1] = name .. " (not placeable here)"; return end
      local conn = e.get_wire_connector(W.circuit_red, true)
      if not conn then skipped[#skipped + 1] = name .. " (no circuit connector)"; e.destroy(); return end
      local cb = e.get_or_create_control_behavior()
      if cb and setup then
        local ok, err = pcall(setup, cb, e)
        assert(ok, name .. ": " .. tostring(err))
      end
      wire(hub, W.circuit_red, e, W.circuit_red)
      placed[name] = e
    end
    local cond = { first_signal = sig("virtual", "signal-A"), comparator = ">", constant = 0 }
    try("pump", function(cb) cb.circuit_enable_disable = true; cb.circuit_condition = cond end)
    try("offshore-pump", function(cb) cb.circuit_enable_disable = true; cb.circuit_condition = cond end)
    try("power-switch", function(cb) cb.circuit_enable_disable = true; cb.circuit_condition = cond end)
    try("accumulator", function(cb) cb.read_charge = true; cb.output_signal = sig("virtual", "signal-E") end)
    try("roboport", function(cb) cb.read_robot_stats = true; cb.read_logistics = false end)
    try("display-panel", function(cb)
      local m = { text = "low", icon = sig("virtual", "signal-A"), condition = cond }
      if not pcall(function() cb.set_message(1, m) end) then cb.set_record(1, m) end
    end)
    try("programmable-speaker", function(cb)
      cb.circuit_condition = cond
      cb.circuit_parameters = { signal_value_is_pitch = true, instrument_id = 1, note_id = 2, stop_playing_sounds = false }
    end)
    try("storage-tank", function(cb) pcall(function() cb.read_contents = true end) end)
    try("assembling-machine-1", function(cb) cb.circuit_set_recipe = true; cb.circuit_read_working = true;
      cb.circuit_working_signal = sig("virtual", "signal-W") end)
    try("electric-mining-drill", function(cb) cb.circuit_read_resources = true end)
    try("splitter", function(cb) cb.set_filter = true end)
    try("radar")
    try("agricultural-tower", function(cb) cb.read_contents = true end)
    try("asteroid-collector", function(cb) cb.set_filter = true end)
    try("cargo-landing-pad")
    -- a rail signal needs a rail: lay a straight rail first
    local rail = s.create_entity{ name = "straight-rail", position = { 795, 605 }, force = "player" }
    if rail then
      local rs = s.create_entity{ name = "rail-signal", position = { 796.5, 605.5 }, force = "player",
                                  direction = defines.direction.south }
      if rs then
        local cb = rs.get_or_create_control_behavior()
        cb.close_signal = true
        cb.circuit_condition = cond
        cb.read_signal = true
        wire(hub, W.circuit_red, rs, W.circuit_red)
        placed["rail-signal"] = rs
      end
    end

    -- a wall gets its circuit connector next to a gate
    local wall = s.create_entity{ name = "stone-wall", position = { 770.5, 625.5 }, force = "player" }
    for _, d in ipairs({ defines.direction.north, defines.direction.east }) do
      if wall and not placed["stone-wall"] then
        local g = s.create_entity{ name = "gate", position = { 771.5, 625.5 }, force = "player", direction = d }
        if g and wall.get_wire_connector(W.circuit_red, true) then
          local cb = wall.get_or_create_control_behavior()
          cb.open_gate = true
          cb.circuit_condition = cond
          cb.read_sensor = true
          cb.output_signal = sig("virtual", "signal-G")
          wire(hub, W.circuit_red, wall, W.circuit_red)
          placed["stone-wall"] = wall
        elseif g then
          g.destroy()
        end
      end
    end
    if not placed["stone-wall"] then skipped[#skipped + 1] = "stone-wall (no connector next to a gate)" end

    after_ticks(2, function()
      local names = {}
      for n in pairs(placed) do names[#names + 1] = n end
      table.sort(names)
      log("kc-circuits zoo placed: " .. table.concat(names, ", ") .. " | skipped: " .. table.concat(skipped, ", "))
      local snap = circuits.read(s, { entities = { hub } })
      local got = {}
      for _, e in ipairs(snap.entities) do got[e.name] = e end
      for name in pairs(placed) do assert(got[name], "missing " .. name .. " in " .. line(snap.entities)) end
      local both = { red = true, green = true }
      local function ctl(n) return got[n] and got[n].control end
      if ctl("pump") then eq(ctl("pump").enable_condition.first, "virtual/signal-A", "pump") end
      if ctl("power-switch") then eq(ctl("power-switch").enable_condition.comparator, ">", "power switch") end
      if ctl("accumulator") then eq(ctl("accumulator").read, { charge = "virtual/signal-E" }, "accumulator") end
      if ctl("roboport") then
        local rr = ctl("roboport")
        assert(rr.read.robot_stats == true and rr.read.logistics == nil, line(rr))
        assert(rr.raw.available_logistic_output_signal and rr.raw.roboport_count_output_signal, line(rr.raw))
      end
      if ctl("display-panel") then
        local m = (ctl("display-panel").raw.messages or ctl("display-panel").raw.records)[1]
        eq(m, { text = "low", icon = "virtual/signal-A", condition = { first = "virtual/signal-A", comparator = ">",
          constant = 0, first_networks = both, second_networks = both, join = "and", fulfilled = m.condition.fulfilled } },
          "display panel")
      end
      if ctl("programmable-speaker") then
        local sp = ctl("programmable-speaker")
        eq(sp.enable_condition.first, "virtual/signal-A", "speaker condition")
        eq(sp.raw.circuit_parameters, { signal_value_is_pitch = true, instrument_id = 1, note_id = 2,
                                        stop_playing_sounds = false }, "speaker")
      end
      if ctl("stone-wall") then
        local wc = ctl("stone-wall")
        assert(wc.raw.open_gate == true and wc.raw.circuit_condition.first == "virtual/signal-A", line(wc))
        eq(wc.read, { sensor = "virtual/signal-G" }, "wall")
      end
      if ctl("assembling-machine-1") then
        eq(ctl("assembling-machine-1").set.recipe, true)
        eq(ctl("assembling-machine-1").read.working, "virtual/signal-W")
      end
      if ctl("electric-mining-drill") then eq(ctl("electric-mining-drill").read.resources, "this_miner") end
      if ctl("rail-signal") then
        local r = ctl("rail-signal")
        assert(r.raw.close_signal == true and r.read.signal == true and r.raw.circuit_condition, line(r))
      end
      local back = helpers.json_to_table(helpers.table_to_json(snap))
      assert(back, "serialisable")
      record("entity-zoo", snap)
    end)
  end)

  it("reads 2.1 decider else-outputs and narrowed wire selections (absent on 2.0)", function()
    local s = game.surfaces[1]
    clear(s, 900, 600, 920, 620)
    power(s, 908, 609)
    local cc = make(s, "constant-combinator", 901.5, 603.5)
    set_constant(cc, { { sig("virtual", "signal-A"), 1 } })
    local dec = make(s, "decider-combinator", 903.5, 604)
    local params = {
      conditions = { { first_signal = sig("virtual", "signal-A"), comparator = ">", constant = 5 } },
      outputs = { { signal = sig("virtual", "signal-X"), copy_count_from_input = false, constant = 1 } },
    }
    if V21 then
      params.else_outputs = { { signal = sig("virtual", "signal-Y"), copy_count_from_input = false, constant = 2 } }
    end
    dec.get_or_create_control_behavior().parameters = params
    local ins = make(s, "inserter", 905.5, 603.5)
    local icb = ins.get_or_create_control_behavior()
    icb.circuit_enable_disable = true
    icb.circuit_condition = { first_signal = sig("virtual", "signal-Y"), comparator = ">", constant = 0 }
    if V21 then icb.input_networks = { red = true, green = false } end
    wire(cc, W.circuit_red, dec, W.combinator_input_red)
    wire(dec, W.combinator_output_red, ins, W.circuit_red)
    after_ticks(4, function()
      local snap = circuits.read(s, { entities = { dec } })
      local d, i = by_name(snap, "decider-combinator"), by_name(snap, "inserter")
      local out = snap.networks[d.connectors.out_red].signals
      if V21 then
        eq(d.params.else_outputs, { { signal = "virtual/signal-Y", copy_count_from_input = false,
                                      networks = { red = true, green = true }, constant = 2 } }, "else outputs")
        eq(out, { ["virtual/signal-Y"] = 2 }, "else branch output")
        eq(i.control.raw.input_networks, { red = true, green = false }, "inserter reads red only")
      else
        assert(d.params.else_outputs == nil and i.control.raw.input_networks == nil, line(snap))
        eq(next(out), nil, "no output when false")
      end
      record("decider-else", snap)
    end)
  end)

  it("stops at the entity limit and the byte budget and says so", function()
    local s = game.surfaces[1]
    clear(s, 820, 600, 860, 620)
    local prev
    for i = 0, 29 do
      local cc = make(s, "constant-combinator", 820.5 + i, 605.5)
      set_constant(cc, { { sig("virtual", "signal-A"), i + 1 } })
      if prev then wire(prev, W.circuit_red, cc, W.circuit_red) end
      prev = cc
    end
    after_ticks(2, function()
      local all = circuits.read(s, { entities = { prev } })
      eq({ #all.entities, all.truncated }, { 30, false })
      local net = all.networks[all.entities[1].connectors.red]
      eq(net.signals["virtual/signal-A"], 465, "sum 1..30")
      local cut = circuits.read(s, { entities = { prev }, limit = 10 })
      eq({ #cut.entities, cut.truncated }, { 10, true }, "limit")
      for _, w in ipairs(cut.wires) do
        local ok = {}
        for _, e in ipairs(cut.entities) do ok[e.id] = true end
        assert(ok[w.a] and ok[w.b], "wire to a dropped entity")
      end
      local small = circuits.read(s, { entities = { prev }, max_bytes = 3000 })
      assert(small.truncated and #helpers.table_to_json(small) <= 3000, #helpers.table_to_json(small))
    end)
  end)
end)
