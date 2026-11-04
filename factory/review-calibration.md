# Review calibration

What the AI review must know about this system that the code does not say. The review reads
this file from the target branch on every run. It is a gate path: a rule lands only through a
merge request a person approves.

Each entry has its reason and the case it came from (a merge request, an override, an
incident). A rule whose code is gone is flagged by the factory's diagnose and removed.

## Trust boundaries

Where untrusted input enters and what is trusted behind it. A security finding must cross one
of these, or show a new one.

- The bridge's HTTP API: bound to 127.0.0.1, no authentication; whoever reaches the port runs
  Lua in the game. Only a test container binds `0.0.0.0`, published on host loopback
  (`bridge/factorio_bridge.py` `Settings.http_host`, `--http-host`; docs/areas/bridge.md "Must
  stay true", "Easy to break").
- UDP between the mod and the bridge: loopback ports 34199 and 34200; the bridge trusts what
  arrives there as the game (`mod/kc-companion/scripts/protocol.lua`, docs/areas/mod-core.md).
- `factorio_query` and live panels: Kira's Lua runs in the game with no sandbox; read-only is
  an agent rule (docs/decisions.md, "Queries run arbitrary Lua with no sandbox").
- `factorio_compute`: Kira's Python runs in a sandbox with no network, no home and no writes
  (docs/areas/mcp-server.md; `tests/unit/test_compute.py::test_sandbox_blocks_network_home_and_writes`).
- The player's question and Kira's chat: text from the player. It must never reach an automatic
  error report; only a bug report the player confirmed carries it (docs/areas/reports.md "Must
  stay true", docs/decisions.md).
- `report/settings.json` fetched from the `release` branch: used only when its DSN points at
  `*.sentry.io` (`report/kc_report.py` `DSN_RE`; docs/areas/reports.md).
- `KIRO_API_KEY`: a secret of the environment `factory`, reachable only from a workflow file a
  job declares it in. The crew's GitHub App `factorycrew-crew` has no `workflows` permission, so
  GitHub refuses any push of its that adds or changes a file under `.github/workflows/`; only
  the repository owner can, and `.github/workflows/**` is a gate path (the App's permissions,
  `factory.yml` `gate_paths`). Report a new path to the key that does not go through a workflow
  file, not this one. Case: the second review round of the factory setup pull request.
- The turn hook: `app/hooks/turn_hook.py` posts Kiro Crew's hook events to the bridge's `/hook`;
  the bridge acts only on a turn whose prompt carries the dispatcher's header for the tab's
  current question (docs/areas/bridge.md "Turn contract").

## Do not report

Patterns the review has flagged that are correct here.

- Arbitrary Lua in `factorio_query` and in panel bodies: decided by the repository owner, and a
  sandbox is not wanted now (docs/decisions.md). Report only a new path that runs Lua the agent
  or the player did not write. Case: the owner's decisions comment on PR #4.
- The Sentry DSN in `report/settings.json`: a send-only ingest key, shipped by design
  (docs/decisions.md, "The Sentry DSN ships in the app"). Case: the same comment.
- `agent/factorio-companion.json` has no `model` field: on purpose (docs/decisions.md, "Kira's
  agent spec pins no model"). Case: the same comment.

## Always report

Places where a change is riskier than it looks.

- `mod/kc-companion/scripts/protocol.lua` with `bridge/factorio_bridge.py`: the two ends of the
  UDP protocol; a change to one end alone breaks every player whose mod and app versions differ
  for a moment (AGENTS.md "Rules").
- The mod's `storage` layout (`mod/kc-companion/control.lua` `init_storage`): players' saves; a
  new field read on an old save needs a guard (docs/areas/mod-core.md "Easy to break").
- Dispatcher chat keys and cron identity (`dispatcher/factorio_dispatch.py`): a new key or cron
  id makes every existing chat refuse messages (docs/areas/dispatcher.md).
- The Release workflow (`.github/workflows/release.yml`, `scripts/release.sh`) and the
  `release` branch: players update from it (AGENTS.md "Rules").
- A new MCP tool that calls the bridge without `_agent`: the idle clock does not restart, and a
  long turn can end as "Kira did not answer" (docs/areas/bridge.md "Easy to break").
