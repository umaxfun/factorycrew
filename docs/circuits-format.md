# Circuit snapshot format (v1)

The contract between the mod's Lua reader `kc.circuits(surface, opts)` and the Python primitives
`kc.circuits` (simulate, explain, validate). Plain JSON; works for any circuit network anywhere,
not only train stations.

## Signals

A signal is a string key `"<type>/<name>"`, with `@<quality>` appended when the quality is not
`normal`: `"item/iron-plate"`, `"virtual/signal-A"`, `"fluid/water"`, `"item/iron-plate@rare"`.
Types: item, fluid, virtual, entity, recipe, space-location, asteroid-chunk, quality.
Special virtual signals keep their names: `virtual/signal-everything`, `virtual/signal-anything`,
`virtual/signal-each`.
A signal set is `{key: count}` with integer counts; zero counts are omitted. When a snapshot still does not fit `max_bytes`, each network keeps its first 20 signals and gets `"signals_truncated": true`.

## Snapshot

```json
{
  "v": 1,
  "tick": 123456,
  "game_version": "2.0.77",
  "truncated": false,                 // max_entities reached, or cut to fit opts.max_bytes (48000)
  "wires_dropped": true,              // only when "wires" was emptied to fit max_bytes
  "networks": {
    "17": {"color": "red", "signals": {"item/iron-plate": 400, "virtual/signal-A": 1}},
    "18": {"color": "green", "signals": {}}
  },
  "entities": [
    {
      "id": 4242,                     // unit_number
      "name": "decider-combinator", "type": "decider-combinator",
      "x": 10.5, "y": -3.0, "direction": 4,
      "kind": "decider",              // decider | arithmetic | constant | selector | other
      "status": "working",            // entity status name (defines.entity_status); "no_power" etc. means a
                                      // decider/arithmetic/selector outputs nothing
      "connectors": {                 // connector -> network id (string); the reader leaves unwired ones out, the simulator also accepts null
        "in_red": "17", "in_green": null, "out_red": "19", "out_green": null
      },
      "params": { ... },              // per kind, below
      "control": { ... }              // for kind "other": what the entity reads and is controlled by
    }
  ],
  "wires": [                          // optional, for drawing/explaining; networks are enough to simulate
    {"a": 4242, "a_connector": "out_red", "b": 4300, "b_connector": "red", "color": "red"}
  ]
}
```

Connector names: combinators have `in_red`, `in_green`, `out_red`, `out_green`; every other
circuit entity has `red`, `green`. The reader follows wires from the entities it is given (or from
everything in an area, or within `radius` of a `position`) until the networks are closed, then lists every entity on them.

## Conditions

```json
{"first": "item/iron-plate", "comparator": "<", "second": null, "constant": 100,
 "first_networks": {"red": true, "green": true}, "second_networks": {"red": true, "green": true},
 "join": "and"}
```
- `comparator`: one of `<`, `>`, `=`, `>=`, `<=`, `!=` (the game's ≥ ≤ ≠ are normalised).
- Either `second` (a signal) or `constant` (an integer) is set.
- `join` is how this condition joins the PREVIOUS one in a list: `and` or `or` (first one: `and`).
  The game's precedence applies: AND binds tighter than OR.
- `first`/`second` may be null when the slot is empty (the condition is then false).

## params per kind

- **decider**: `{"conditions": [Condition, ...], "outputs": [{"signal": key, "copy_count_from_input": bool, "networks": {"red": bool, "green": bool}, "constant": int}]}`.
  `copy_count_from_input` false outputs `constant` (default 1).
- **arithmetic**: `{"first": key|null, "first_constant": int|null, "first_networks": {...}, "operation": "*", "second": key|null, "second_constant": int|null, "second_networks": {...}, "output": key|null}`.
  Operations: `+ - * / % ^ << >> AND OR XOR` (integer, 32-bit wrap like the game).
- **constant**: `{"enabled": bool, "sections": [{"active": bool, "multiplier": number, "signals": {key: count}}]}`.
- **selector**: `{"mode": "select"|"count"|"random"|"stack-size"|"rocket-capacity"|"quality-filter"|"quality-transfer"|"quality-select"|"time", ...mode fields as the game has them}` (`quality-select` and `time` exist on 2.1).

## control (kind "other")

What the entity does with the network, normalised:
```json
{"enable_condition": Condition|null, "read": {"contents": true, "hand": "pulse"|"hold"|null, ...},
 "set": {"filters": true, "stack_size": "virtual/signal-S", "trains_limit": "virtual/signal-L", ...},
 "raw": { ... }}
```
`read`/`set` keys are the control behavior flags the game exposes, lowercased; `raw` keeps
anything type-specific (train stop: send_to_train, read_from_train, read_stopped_train,
read_trains_count, set_trains_limit, priority; inserter; lamp; belt; chest; pump; roboport;
rail signal; display panel; speaker...).

## Semantics the simulator follows

- A network's value at tick t+1 is the sum of everything put on it during tick t: combinator
  outputs computed from their inputs at tick t, constant combinators, and the other entities'
  contributions.
- A combinator's input = the sum of its `in_red` and `in_green` networks (each signal may be read
  from red only, green only or both, per `*_networks`). Each combinator adds one tick of delay.
- Other entities' contributions are not readable directly. The simulator takes them as fixed
  "exogenous" signals: observed network value minus what the combinators and constants on it
  output in the snapshot's steady state. Changing them (e.g. "the chest now holds 0 plates") is
  how a scenario is tested.
