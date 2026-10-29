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

Data path: the full dump of a large base is several MB of JSON, and a query result is capped at 64 KB. So the mod writes the dump into the game's `script-output`, the MCP reads the file, builds the atlas in Python and caches it.

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

Scope: the mod writes the dump (`kc.atlas_dump`, entities with recipe/status/speed/pickup/drop/belt contents/stop names, stops, trains with schedules, recipes in use, drill targets, fluid segments, logistic network ids) into `script-output`; `mcp/kc/atlas.py` builds blocks, zones and edges; `factorio_atlas(level="zone", at=...)` and `level="block"` answer; a short section in `agent/factorio-companion.md` says when to use it ("a pointer or a question about a factory, not one machine: start from `zone` at the pointer").

Done when:
- `scripts/atlas-fixture.sh dump` loads `FC_ATLAS_SAVE` in headless 2.0.77 with `FC_ATLAS_MODS` and writes the dump; its entity count equals `count_entities_filtered{force="player"}` minus characters.
- The time the game is frozen by `kc.atlas_dump` on the fixture is measured and written into this file. If it is over 1 s, the dump is spread over ticks before task 1 closes.
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

### 3. Map level and filters

Scope: `level="map"` with `item`, `status`, `feed`, `near`, `min_machines`, `limit`, and `kind=station|train|network`; fix the overview's stuck reason and drill statuses.

Done when:
- Unit tests on the fixture: unfiltered `map` with `min_machines=3` stays under 3.5k tokens; `map item=nullius-glass` returns every zone with a glass block and nothing else; `map status=low_power` is sorted by machines standing; `kind=station item=nullius-rutile` returns both rutile stops; `stuck` shows the most common reason to stand, never `working` or `full_output`.
- e2e: "where is glass made here". Kira uses `map item=...`, answers with the zones and their coordinates and marks them.

Depends on: 1.

### 4. Feed modes and logistic networks

Scope: feed mode per block, zones cut by it, network nodes (`kind=network`: what goes in explicitly, what goes out, items handed out that nothing puts in).

Done when:
- Unit tests on the fixture: the zone at (226, -25.5) is fed only by robots and holds about 41 blocks; the zone at (214, -5) is fed by belts with robots topping up graphite, iron chests and steel; the network's "nothing puts it in" list contains hard-glass; no network edge for an item the block already gets by belt.
- e2e: "what does the robot mall lack". Kira names the machines waiting for ingredients and the items nobody puts into the network.

Depends on: 1.

### 5. Selection versus block, on synthetic saves

Scope: when a box covers part of a block, Kira compares the selected machines with the rest: their own cause, or the whole block's. Three small committed saves with one planted fault each, so these run on the Docker e2e without mods.

Done when:
- Save A: the box covers 3 machines whose inserter is turned around; the reply names that cause and says the rest of the block works.
- Save B: the box covers 3 machines of a block that starves because two inputs share one belt lane; the reply says the whole block starves and gives need and lane numbers.
- Save C: the box is on the side of a block fed from a train stop whose loading station is empty; the reply follows the train to that station.
- Each save without its fault (control) gets no false cause.

Depends on: 2.

### 6. Freshness

Scope: cache the built atlas; rebuild on `refresh=true` or when the dump is older than a set age; later, only the chunks that changed.

Done when:
- A second `factorio_atlas` call on the same dump answers in under 200 ms.
- After ghosts are built in a test, `refresh=true` shows the new block, and a call without it says how old the data is.

Depends on: 1.
