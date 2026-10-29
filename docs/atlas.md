# Base atlas: tasks

Kira answers questions about one spot well and about the base badly. A player report: a box over one
furnace in a titanium-tetrachloride block, "why doesn't this work, where is all the rutile". Kira
looked only inside the box, guessed from belt contents twice and was wrong twice. The player expected
her to see three things a player sees: the box is part of a whole block, the block's ratios and belt
throughput cannot work, and the inputs come by train, so the question is where from and whether there
is any there.

The answer is an atlas of the base that Kira zooms through with a tool, the way you use a paper atlas:
an overview map, then a zone, then a block, then the raw entities. Nothing is pushed into her context
every turn; she calls the tool with filters at the level she needs.

## Levels

| level | one entry is | shows |
|---|---|---|
| `map` | a zone | bounds, machines, how it is fed (belts / robots / topped up by robots), main recipes, how many machines stand and why |
| `zone` | a block in the zone | for every ingredient: where it comes from (zone, station, logistic network) and by what (belt, inserter, pipe, chest, robots); what the zone makes; what has no source |
| `block` | a machine group | machines, statuses, rates per second at full speed, the belts/pipes/inserters in and out, belt lane capacity |
| `area` | an entity | the existing `kc.scan` |
| `trace` | a hop | from a block along one item, up or down: block -> station -> train -> loading station -> producer zone, or block -> block |

Words used below:
- **block**: machines with one recipe standing within 6 tiles of each other, never across rails.
- **zone**: blocks standing within 5 tiles of each other, not across rails, and fed the same way (a block fed only by robots never joins a block fed by belts). Zones are the nodes of the base graph.
- **edge**: A feeds B with item X when A makes X, B uses X, and a physical path joins them: an inserter, a belt followed downstream, a plain chest in between, a pipe segment (pumps and valves followed by direction), or one logistic network. Items move only by belts/inserters/chests/robots, fluids only by pipes.
- **logistic network**: a node of its own. It gets an item explicitly when an inserter puts a product into one of its chests; it feeds a block when the block's inserters take from its demand chest and the block does not already get that item physically.
- **feed mode** of a block: `robots` (every ingredient comes from a network), `belts` (none does), `topped` (belts carry most, robots bring some).

## Tool

One MCP tool, `factorio_atlas`, with flat parameters (weak models handle it better than composing Python), and the same functions in `factorio_compute` as `kc.atlas` for calculations.

| parameter | levels | meaning |
|---|---|---|
| `level` | all | `map`, `zone`, `block`, `area`, `trace` |
| `id` / `at` | zone, block, trace | `Z12`, `B480`, `S11`, or a point `{x, y}` (a pointer's position) |
| `item` | all | keep entries that make or use this item |
| `status` | map, zone, block | `stuck` (anything but working/full output), `low_power`, `no_ingredients`, ... |
| `feed` | map, zone | `belts`, `robots`, `topped` |
| `near` | map | `{x, y, r}` |
| `kind` | map | `zone` (default), `station`, `train`, `network` |
| `part` | zone | `inputs`, `outputs`, `missing`, `blocks` |
| `dir`, `depth` | trace | `up` / `down`, hops |
| `min_machines`, `limit` | map, zone | size cut and row cap |
| `refresh` | all | rebuild from a fresh dump |

Data path: the full dump of a large base is several MB, and a query result is capped at 64 KB. So the mod writes the dump into the game's `script-output`, the MCP reads the file, builds the atlas in Python and caches it.

## Reading the base without stalling the game

Measured inside the game on the reported save (123 939 player entities), headless 2.0.77 under x86 emulation on Apple Silicon, so a native client should be faster; how much is not measured yet. Scripts: `tools/atlas_proto/bench.lua`, `bench2.lua`.

| phase | time |
|---|---|
| `find_entities_filtered{force="player"}`, whole surface | 294 ms |
| name, position, direction of every entity | 376 ms |
| recipe and status of all 6137 machines | 24 ms |
| contents of all 18 661 belt pieces | 82 ms |
| fluid segments of every machine's connections | 40 ms |
| `helpers.table_to_json` of the static records (4.5 MB) | **7 964 ms** |
| the same records as hand-built text (name id, x·2, y·2, direction; `table.concat`), 1.7 MB | 579 ms |
| `helpers.write_file` | 13–25 ms |
| one 32×32 chunk (408 entities) | 15 ms |
| status of every machine (live state of the whole base) | 44 ms |

What follows from it:
- `table_to_json` is the only slow part. The dump is hand-built text: names as numeric ids with a name table, coordinates as integers (doubled, so half-tiles stay exact), one line per entity. That is 14 times faster for the same data.
- The dump is spread over ticks: about 40 ms per 10 000 entities here, so a budget of 2–3 ms per tick reads the whole base in the background in 2–4 s of game time and never freezes it. UPS drops a little meanwhile; how much on this base in the live client is not measured yet.
- In multiplayer `helpers.write_file` writes on every client unless `for_player` is given: always pass the asking player's index.

Two kinds of data, kept apart:
1. **Structure**: what stands where, recipes, directions, pickup/drop points, stop names, fluid segments, logistic network ids. It changes only when the player builds. It is cached and updated by chunk: the mod marks a chunk dirty on build (player, robot, `script_raised_built`), removal (mined by player or robot, died, `script_raised_destroy`), rotation, flip, settings paste, and closing a machine's GUI (the way a recipe usually changes). The next atlas call re-reads only dirty chunks. A full rebuild runs on save load and on mod changes (`on_configuration_changed`). Whether these events catch every way a recipe or filter can change is not verified, so a rare full background rebuild and `refresh=true` stay as a safety net.
2. **State**: statuses, belt and chest contents, train positions and cargo, network stock. It changes every tick, is never cached, and is read live for what the question touches: milliseconds for one zone, 44 ms for every machine's status.

The Python side builds the whole atlas from scratch in about 6 s, so after a build it rebuilds only the zones that touch dirty chunks; otherwise the first question after building waits.

## What the prototype measured

The prototype is in `tools/atlas_proto/`: a headless 2.0.77 game with the player's save and mods, dumped over RCON. It is a throwaway sketch: the tasks below rewrite it into `mcp/kc/atlas.py` and the mod. Run: `FC_ATLAS_DIR=<write-data dir of the headless game> python3 tools/atlas_proto/overview.py`.

On the reported save (27 mods, Nullius): 123 940 entities on nauvis, 114 stops, 25 trains.
- Dump: 6.1 MB JSON. Python loads it and builds the belt graph in 2.7 s; the full graph takes about 3 s more.
- 917 blocks, 175 zones. The overview of the 128 zones with 3+ machines is 11 148 characters, about 2.8k tokens.
- 1660 edges: inserter 1257, belt 1105, robots 1366, pipe 954, chest 97 (an edge can carry several kinds).
- Feed modes: belts 756 blocks, robots 121, topped 40. One logistic network: it gets 236 items explicitly from 93 zones and hands out 192 items to 75 zones. 9 items are handed out but nothing in the base puts them in (hard-glass, optical-cable, processor-1, sensor-2, heat-pipe-2, thermal-tank-1, box-eutectic-salt, limestone, water-canister).
- The reported block: 32 `nullius-medium-furnace-2` on `nullius-titanium-tetrachloride`, all `item_ingredient_shortage`. A box over one of them finds all 32. Need at full speed: graphite 44.8/s, rutile 25.6/s, chlorine 512/s. Graphite and rutile are side-loaded from both sides onto one express belt at (-337.5, -105.5), so each gets one lane, 22.5/s: graphite caps the block at 50%. Rutile comes by train 371 (one wagon) from a station whose 10 flotation cells make 8.5/s at most, a third of the need, and stand at `low_power`.
- The robot mall separates from the belt mall once zones are cut by feed mode (41 blocks, 49 machines, all fed by robots).

Pitfalls the prototype hit, so the real code avoids them:
- Connectivity alone glues the base into one component (628 blocks joined by pipes and belts). Chains only make sense per zone.
- Without the item check, one shared belt joins everything on it. Without the item/fluid check, a fluid "travels" by belt.
- Robots join everything in a network, so the network must be a node, not a shortcut between blocks.
- A machine's fluidbox has no fluid segment id in 2.0; take the segment from its pipe connections' targets. Pumps: link input segment to output segment; a side with no target next to a stop is a wagon.
- Lua tables with nil holes or empty tables come out of `helpers.table_to_json` as objects (`{}`, `{"2": ...}`): pad or normalise on the Python side.
- Underground belt reach must come from the prototype (express is 15 here); a fixed guess broke a trace.
- Nullius names chests `demand` (requester), `supply` (passive provider), `storage`. Use prototype types and logistic modes, not names.
- A chest between two stops was given to the wrong one (rutile lost its source). Euclidean nearest worked on this save; robust is the stop whose track the unloading inserter reaches.
- Drills have no status in the first dump; the overview's "stuck" printed the most common status instead of the most common reason to stand.

## Fixture

The reported save with its mods is the main fixture. It is the player's own save, not committed (save 17 MB, mods 608 MB). Tests read it from `FC_ATLAS_SAVE` (the save zip) and `FC_ATLAS_MODS` (a mods dir with `mod-list.json`); this checkout's `kc-companion` replaces the copy in that dir. Tests that need it skip with a clear message when the variables are unset. Small synthetic saves with planted faults (task 5) are committed and need no mods.

## Tasks

Each task extends the chain that already works end to end; none is done until an e2e makes Kira use it through the full game -> Kiro Crew -> game loop on the real client. Unit tests on the fixture dump come first and are not enough on their own.

### 1. One zone, end to end

Scope: the mod writes the structure dump (`kc.atlas_dump`: entities with recipe/speed/pickup/drop/stop names, stops, train schedules, recipes in use, drill targets, fluid segments, logistic network ids) into `script-output` as hand-built text (see "Reading the base"), spread over ticks with a per-tick time budget, written for the asking player only. State (statuses, belt contents, trains, stock) is not in the dump: `factorio_atlas` reads it live for the entries it returns. `mcp/kc/atlas.py` parses the dump and builds blocks, zones and edges; `factorio_atlas(level="zone", at=...)` and `level="block"` answer; a short section in `agent/factorio-companion.md` says when to use it ("a pointer or a question about a factory, not one machine: start from `zone` at the pointer"). This task does a full dump on every refresh; dirty chunks are task 6.

Done when:
- `scripts/atlas-fixture.sh dump` loads `FC_ATLAS_SAVE` in headless 2.0.77 with `FC_ATLAS_MODS` and writes the dump; its entity count equals `count_entities_filtered{force="player"}` minus the moving and temporary types the dump leaves out (robots, ghosts, rolling stock, item proxies...), and Python parses it back to the same names, positions and directions as a reference read. **Done**: 96 929 = 123 939 − 27 010; `test_dump_matches_a_reference_read_of_the_same_save` (positions are half-tile rounded: 474 off-grid script entities of Nullius only match after rounding).
- No single tick of the dump takes more than its budget (3 ms by default), checked by the mod's own profiler on the fixture; the whole dump finishes and reports how many ticks it took. **Measured** natively (the Mac client, `dump_now` with a profile file): 3353 steps, 6 over 3 ms, the worst 3.4 ms (a lower entity budget did not remove these few; they are single chunk lookups). Under x86 emulation the worst step is 7.5 ms.
- In the live client on the fixture save, UPS before and during a full dump are measured and written into this file. If it drops below 50 the budget is lowered until it does not. **Done**: see "Task 1 measurements".
- `table_to_json` is not used for the dump (a test greps the dump path). **Done**: `test_the_dump_never_builds_json`.

Task 1 measurements (real 2.0.77 client on the Mac, the fixture save, `tests/app_e2e/test_atlas.py`):
- What costs frames is looking at chunks, empty or not (7888 chunks, 1778 with entities of ours). Sweep at a fixed entity budget: 24 chunks per tick gave 34–53 UPS, 2 per tick a flat 60; the entity budget (10–200) and the send size (1000–4000 bytes per tick) barely mattered.
- Other causes found and fixed: numbers turned into text with `..`/`table.concat` cost ~6 µs each (3× slower dump); one busy chunk (up to ~900 entities) in a single tick; sending ~500 KB/s made the client stop answering at all (paced to 4000 bytes per tick).
- Final settings: 3 chunk lookups per tick, 150 cost units per tick (a fluid machine counts 5), 4000 bytes per tick. UPS 60 before; during a full dump min 53.6 over 5-second windows (`[60, 60, 59, 60, 59, 54, 56, 58, 60, 60, 60]`), the dump 57.5 s, 3356 ticks. 2-second windows read lower (46–47) because each reading waits up to ~170 ms for the game's 10-tick UDP poll.
- e2e on the reported question passed: a box on one furnace, "why doesn't @1 work? where is all the rutile?" → "part of a block of 32 TiCl4 furnaces (-375,-136 to -348,-112). All 32 are out of rutile. Graphite and chlorine are fine. The rutile is in train 371. It is still loading at the red rutile stop (243, 313): 1861 of 2500 so far. It only leaves when the wagon is full. The 6 unload chests at the blue stop (-355, -101) are empty." (84 s).
- Unit tests on the fixture dump: `zone at (-357.5, -122.5)` is one block of 32 machines on `nullius-titanium-tetrachloride`; its inputs are graphite <- the graphite unloading stop, rutile <- the rutile unloading stop, chlorine <- a zone by pipe; nothing in `missing`. The block found from a box over one furnace and from a box over the opposite corner is the same 32.
- Unit tests on prototype pitfalls: fluids never on belt edges, items never on pipe edges; a shared belt does not join blocks whose products are not each other's ingredients; underground reach comes from the prototype.
- e2e on the real client with the fixture loaded: a box pointer over one furnace, "why doesn't @1 work". Kira calls `factorio_atlas`; the reply names the whole block (32 machines) and says which input is short and that it comes by train.

Depends on: nothing. Open: the real-client stand must start with the fixture's mods (`stand.sh` today starts only this mod); the Docker e2e runs 2.1, and the fixture's mods may not load there.

### 2. Rates, lanes and trace

Scope: `block` shows need and supply per ingredient per second (crafting speed from the entity, so modules and beacons count), belt tier throughput, and lane limits from side-loading; `level="trace"` follows an item up or down through blocks, stations, trains (schedule, wagons, state), loading stations and the producer zone.

Done when:
- Unit tests on the fixture: the tetrachloride block reports graphite 44.8/s needed against 22.5/s available on one lane at (-337.5, -105.5), and rutile 25.6/s. `trace item=rutile dir=up` from it returns the rutile unloading stop -> train 371 (1 wagon) -> the rutile loading stop -> the flotation zone with 10 machines, 8.5/s at most, `low_power`.
- A planted case in a synthetic save (task 5) where the lane limit is the only cause is found by the same code.
- e2e: "why is the tetrachloride block slow". Kira uses `block` and `trace`; the reply gives the rutile source shortfall with both numbers and the graphite lane limit.

Depends on: 1.

Status: unit tests on the fixture and the e2e are done; the planted lane-only case waits for the synthetic saves of task 5. The e2e reply (real client, 109 s): "Block: 32 furnaces on titanium tetrachloride ... Full speed needs: graphite 44.8/s, rutile 25.6/s, chlorine 512/s. 1. Graphite: 0/s. Its stop (-355,-107) has limit 0, set by a wire. No train has it in its schedule. Main cause. 2. Rutile: the mine block at (246,345) makes only 8.5/s, and 6 of its 10 machines have low power. Train 371 waits at the rutile pickup stop with 1797/2500. That stop's limit is 0 too. 3. Belt: graphite and rutile get one lane each of one express belt (22.5/s each). 4. Chlorine: 23 sodium machines make 431/s of the 512/s needed. Fine now, short at full speed. Fix 1 first ..." It found a cause nobody had named (the graphite stop disabled by a wire).

### 3. Map level and filters

Scope: `level="map"` with `item`, `status`, `feed`, `near`, `min_machines`, `limit`, and `kind=station|train|network`; fix the overview's stuck reason and drill statuses.

Done when:
- Unit tests on the fixture: unfiltered `map` with `min_machines=3` stays under 3.5k tokens; `map item=nullius-glass` returns every zone with a glass block and nothing else; `map status=low_power` is sorted by machines standing; `kind=station item=nullius-rutile` returns both rutile stops; `stuck` shows the most common reason to stand, never `working` or `full_output`.
- e2e: "where is glass made here". Kira uses `map item=...`, answers with the zones and their coordinates and marks them.

Depends on: 1.

Status: done. Unfiltered map on the fixture: 111 zones of 3+ machines, ~2.2k tokens. Live statuses of the whole base come in batches of 2000 machines (positions grouped by name, statuses back as numbers). e2e (real client, 97 s): "Glass is made in 3 places on nauvis (58 machines), all marked: Z10 (103,38)-(185,140): 26 machines; Z37 (-51,78)-(-6,105): 26 machines, closest to you; Z71 (103,37)-(113,61): 6 machines. All three zones are short on power (low_power)."

### 4. Feed modes and logistic networks

Scope: feed mode per block, zones cut by it, network nodes (`kind=network`: what goes in explicitly, what goes out, items handed out that nothing puts in).

Done when:
- Unit tests on the fixture: the zone at (226, -25.5) is fed only by robots and holds about 41 blocks; the zone at (214, -5) is fed by belts with robots topping up graphite, iron chests and steel; the network's "nothing puts it in" list contains hard-glass; no network edge for an item the block already gets by belt.
- e2e: "what does the robot mall lack". Kira names the machines waiting for ingredients and the items nobody puts into the network.

Depends on: 1.

Status: done. On the fixture the robot mall is zone Z29 (50 blocks, 58 machines, all robot-fed; the prototype's 41 grew because blocks are now joined by gap across 32-tile cells); blocks: belts 737, robots 123, topped 45. e2e (real client, 90 s), a click in the robot mall: "Robot mall Z29 on nauvis: 58 machines at (175,-36)-(247,-17), all fed by robots from network N8 ... It lacks: Compressed argon: nothing in the base makes it. The lamp-2 machine stands. 8 items robots take but nothing puts into N8 (living on stock): eutectic salt box, hard glass, heat pipe 2, optical cable, processor 1, sensor 2, steel beam, thermal tank 1. Power: 5 machines are on low power. Status: 34 full output, 18 short on items, 5 low power, 1 short on fluid."

### 5. Selection versus block, on synthetic saves

Scope: when a box covers part of a block, Kira compares the selected machines with the rest: their own cause, or the whole block's. Three small committed saves with one planted fault each, so these run on the Docker e2e without mods.

Done when:
- Save A: the box covers 3 machines whose inserter is turned around; the reply names that cause and says the rest of the block works.
- Save B: the box covers 3 machines of a block that starves because two inputs share one belt lane; the reply says the whole block starves and gives need and lane numbers.
- Save C: the box is on the side of a block fed from a train stop whose loading station is empty; the reply follows the train to that station.
- Each save without its fault (control) gets no false cause.

Depends on: 2.

### 6. Freshness

Scope: the structure stays cached between calls and is updated by chunk. The mod marks chunks dirty on the events listed in "Reading the base" and keeps the set in `storage`; an atlas call first re-reads the dirty chunks (spread over ticks like the full dump) and the MCP rebuilds only the zones that touch them. A full rebuild runs on save load, on `on_configuration_changed`, on `refresh=true`, and as a rare background safety net. State is still read live on every call.

Done when:
- A second `factorio_atlas` call with no dirty chunks answers in under 200 ms, state read included, on the fixture.
- In a test, building a machine (player, robot, `script_raised_built`), mining it, rotating an inserter, pasting settings, and changing a recipe through the machine's GUI each mark exactly the chunks touched; the next call shows the change and re-reads only those chunks (counted).
- A change made with no event (a script setting a recipe directly) is caught by `refresh=true`, and every answer says how old its structure data is.
- After one dirty chunk, the Python rebuild takes under 0.5 s on the fixture (against about 6 s from scratch).
- The dirty set survives save and load; mod changes trigger a full rebuild.

Depends on: 1.
