# Origin

Written for factorycrew from an idea only; no code was ported or read.

- Idea: [projectsbymj/Factorio-Solar-Planner](https://github.com/projectsbymj/Factorio-Solar-Planner),
  commit 166e2729975ad16ad9bf6516e2553030c1478d97 (2025-12-08). It computes sustained power from panel
  and accumulator counts per planet. The repository has NO license (`gh api repos/projectsbymj/Factorio-Solar-Planner
  --jq .license` is null; the repo holds only README.md and solaroutput.py), so only the idea was taken from
  its README. `solaroutput.py` was not opened.
- The ratio formula was derived from the game's piecewise-linear light curve (see the module docstring) and
  checked against a brute-force day in the unit tests and against the per-planet "Optimal ratios" table on
  [wiki.factorio.com/Solar_panel](https://wiki.factorio.com/Solar_panel) (Nauvis 0.84672, Vulcanus 0.72576,
  Gleba 0.6048, Fulgora 0.072576, Aquilo 0.024192, all reproduced exactly).
- The cell packer (guillotine dynamic program over Pareto fronts) is our own. Its vanilla result, 24 panels and
  20 accumulators around one substation in 18x18, is the commonly built substation block (the wiki's 6:5 ratio).
