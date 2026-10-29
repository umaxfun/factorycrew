# Origin of skills/power

Written from scratch for factorycrew (MIT). No code was ported, read into, or translated.

Idea only, from two upstreams without a usable license (checked in docs/research/01-outposts.md,
item 5: "neither repo has a usable license (demipixel: none; PlannerCore: none)"):

- demipixel/factorio-generators, `power.js`: put each pole where it powers the most unpowered
  entities, then bridge separate groups in wire-reach hops.
- Ben-Ramchandani/PlannerCore, `PB_stages.lua`: the same greedy cover + joining stage for
  outpost planners.

What is ours: the gain-bucket greedy with a "wires to an already chosen pole" tie-break, the
redundant-pole prune, the A* over pole positions that gives the fewest join/link poles (an
admissible lower bound from the remaining distance over the hop length), the half-tile slack, and
check().

The wiring rule (distance at most the shorter of the two poles' wire reaches) is the one
`kc_compute.unwired` (mcp/kc_compute.py) implements; `power.check(..., slack=0)` gives the same
verdict for the same poles, and the planner itself plans every link `slack` (0.5) under it.
