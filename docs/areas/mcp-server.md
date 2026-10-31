# MCP server `factorio`

The stdio MCP server that gives Kira her `factorio_*` tools. Most tools are one HTTP call to the bridge on 127.0.0.1. `factorio_compute` runs Python on this machine, `factorio_atlas` parses and caches the base's structure here, and `factorio_panel` / `factorio_saved_panels` also use the `kirocrew artifact` CLI (`mcp/factorio_mcp.py`).

## Main files

- `mcp/factorio_mcp.py`: the whole server. It holds the tool list (`TOOLS`, :43), the bridge routes (`ROUTES`, :255), `bridge_post` (:265), compute and its sandbox (:282-413), panels and the saved-panel library (:416-699), atlas (:702-923), the dispatch in `call_tool` (:926) and the JSON-RPC loop in `handle` / `main` (:942, :964).
- `mcp/kc_compute.py`: a 3-line shim, so `import kc_compute as kc` re-exports the package `kc` (`mcp/kc_compute.py:1-3`).
- `mcp/kc/`: the primitive library that `factorio_compute` copies into its sandbox. See [kc library](kc-library.md); atlas parsing is in [../atlas.md](../atlas.md).
- `agent/factorio-companion.json`: how kiro-cli starts the server in a checkout: `/usr/bin/python3 __ROOT__/mcp/factorio_mcp.py --bridge http://127.0.0.1:34198` (`agent/factorio-companion.json:17-21`).
- `app/app.json`: how the app starts it: `python3 mcp/factorio_mcp.py`, with no `--bridge` (`app/app.json:16-21`).

## Tools and how each reaches the bridge

| Tool | Path to the game (`mcp/factorio_mcp.py`) |
|---|---|
| `factorio_query`, `factorio_mark`, `factorio_unmark`, `factorio_list_marks`, `factorio_reply`, `factorio_progress` | `POST /query`, `/mark`, `/unmark`, `/list_marks`, `/reply`, `/progress` with the arguments as they are, plus `_agent: true` (`ROUTES` :255, `call_tool` :937-939) |
| `factorio_compute` | an optional `POST /query` with `data_lua` (:347), then `POST /alive` at the start (:351) and about every 15 s while it runs (:390). The Python itself runs locally |
| `factorio_panel` | one `POST /query` whose Lua calls `kc.panel{...}` or `kc.panel_remove`, with the spec as JSON in a Lua long string (`panel_lua` :426, :445). A panel that works is saved with `kirocrew artifact update`, or `save` when update fails (:454, `library_save` :620). `restore` reads the artifact first (`library_get` :681) |
| `factorio_saved_panels` | `GET /health` for the `run_id` (`current_run_id` :501), then `kirocrew artifact list` and `show --meta`. It does not reach the game (`library_list` :644) |
| `factorio_atlas` | `GET /health`, `POST /atlas` with a 330 s timeout and `have_version` (:717), then `POST /query` batches for live statuses (`STATUS_LUA`, 2000 machines per query, :509, :735) and, for `level=trace`, for trains (`TRAINS_LUA`, `trace_view` :903) |

The bridge counts every call flagged `_agent` as a step of Kira's turn (`bridge/factorio_bridge.py:541-543`, :601-605). The tool contracts, from Kira's side, are in [SPEC.md "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio).

## The bridge address

`_bridge_url` (`mcp/factorio_mcp.py:21-35`) is read once at import into `BRIDGE` (:38). The order is: `--bridge URL` from argv, then `FACTORIO_BRIDGE_URL`, then `http_port` in `<app root>/data/config.json` (the file the app backend writes, `app/backend/server.py:10`), then 34198. `--bridge` counts only when a value follows it, because the lookup is `sys.argv[1:-1]` (:28). Per-worktree stands rewrite this argument and check it on running processes ([Dev stand](dev-stand.md), [../worktrees.md](../worktrees.md), `scripts/stand.sh:156-173`).

## factorio_compute and its sandbox

`compute` (`mcp/factorio_mcp.py:340`) builds a temp dir `kc-compute-*`. It writes `data.json` and `main.py` (`PRELUDE` + code + `EPILOGUE`, :284-299). It copies `kc_compute.py`, every `skills/*/lib/*.py` (:361) and every top-level `mcp/kc/*.py` into `kc/` (:366). It then runs `sys.executable -I main.py` with `PATH=/usr/bin:/bin` and `HOME`/`TMPDIR` set to the work dir (:370-376). The result is `result.json`. Output: `ok, result, stdout` (last 64 KB), `stderr` (last 4000 chars), `seconds, sandboxed`. Errors: `timeout`, `python_error`, `result_too_large` over 64 KB, `data_lua_failed` (:348, :399-413). The timeout is 60 s by default and 120 s at most (:282, :344).

- **Seatbelt on macOS**: `sandbox_profile` (:302) denies network, denies writes outside the work dir and `/dev/null`, and denies reads under the home dir. The interpreter's own prefixes and the work dir stay readable (`tests/unit/test_compute.py::test_sandbox_blocks_network_home_and_writes`).
- **Nested sandbox**: under Kiro Crew the server already runs in the gateway's sandbox, and `sandbox-exec` inside it fails. `own_sandbox_works` (:319) probes `/usr/bin/sandbox-exec ... /usr/bin/true` once per process. If the probe fails, the code runs without our profile and the result says `sandboxed: false` (`tests/unit/test_compute.py::test_runs_inside_an_outer_sandbox`). `FC_COMPUTE_NO_SANDBOX` turns our profile off, and so does any OS other than darwin (:329-330).

## Talks to

- Uses: [Bridge](bridge.md), its HTTP API on 127.0.0.1 (`bridge_post` :265, `bridge_health` :530; routes in `bridge/factorio_bridge.py:538-615`).
- Uses: the `kirocrew artifact` CLI of the gateway it runs under, only when it knows the home (`KIROCREW_HOME`, else the install path `<home>/apps/<app>/mcp/`, `kirocrew_home` :468) and `FC_NO_ARTIFACTS` is not set (`_kirocrew` :481).
- Uses: [kc library](kc-library.md) and [Skills](skills.md) (`mcp/kc`, `skills/*/lib`), copied into each compute run (:361-369); [Atlas](atlas.md) (`mcp/kc/atlas.py`) is imported in-process (:704); the mod side of panels is [Panels](panels.md).
- Used by: [Kira agent](kira-agent.md), through kiro-cli for each chat, over newline-delimited JSON-RPC on stdio (`agent/factorio-companion.json`, `app/app.json`).
- Used by: the dispatcher, which shares the artifact naming (`dispatcher/factorio_dispatch.py:382-386`, `tests/unit/test_panel_tool.py::test_dispatcher_and_mcp_agree_on_names`).
- Used by: [App](app.md), its status page, which starts the server and sends `initialize` + `tools/list` (`probe_mcp`, `app/backend/status.py:72-84`).

## Must stay true

- Stdlib only. pytest is for tests only (README.md "Layout"; the imports at `mcp/factorio_mcp.py:8-17`).
- `isError` is `not ok` for every tool, and the payload is one JSON text block (`mcp/factorio_mcp.py:953-956`; asserted on every call in `tests/integration/conftest.py:130-134`).
- The bridge can't be reached → `{"ok": false, "error": "bridge_unreachable"}`; the server never raises (`bridge_post` :277-278).
- Every call to the game that belongs to Kira's turn carries `_agent: true`, and nothing else does (`tests/unit/test_compute.py::test_data_from_the_game_and_result_back`; `bridge/factorio_bridge.py:601-603`).
- Compute code reads nothing outside its work dir, so every library it imports must be copied in (:359-369; `tests/unit/test_compute.py::test_skill_code_is_importable_in_compute`, `::test_every_kc_module_is_reachable_as_kc_attribute`).
- The panel spec reaches the game as JSON in a Lua long string whose closing bracket never occurs in the text (`tests/unit/test_panel_tool.py::test_long_string_never_closes_early`).
- Panel artifact slugs, tags and the code fence are the same in the server and the dispatcher (`tests/unit/test_panel_tool.py::test_dispatcher_and_mcp_agree_on_names`).
- The atlas cache is per requested surface and dropped when `run_id` changes (:702-711; `tests/unit/test_kc_atlas.py::test_mcp_atlas_tool_on_a_fake_bridge`).
- After an app update the running server re-executes itself in place before the next `tools/call` (same pid and stdio, so the chat keeps its connection) and the new code handles that call; requests already read are handed over through a temp file named in `FC_MCP_PENDING_FILE`. It compares `app.json` beside its `mcp/` dir with the version it started with, so outside an installed app (no `app.json` there) it never restarts (`reexec`, `main`; `tests/unit/test_mcp_self_update.py`; checked through the real update route on a dev gateway, README "Releases and updates").

## Easy to break

- kiro-cli does not pass an agent spec's `env` block to the MCP process. A server started without `--bridge` talks to whatever port `FACTORIO_BRIDGE_URL` or `data/config.json` names, which can be the player's bridge (`mcp/factorio_mcp.py:24-27`; [../worktrees.md "Checking only your own stand"](../worktrees.md#checking-only-your-own-stand)).
- kiro-cli starts one server per chat session, and that server keeps the code it started with. After an edit, old tabs run old code until `scripts/stand.sh kc stop` and `up` (`scripts/stand.sh:162-173`).
- Live, compute runs on `/usr/bin/python3` (3.9.6 on this Mac, from `/usr/bin/python3 --version`) because `sys.executable` is the server's interpreter (:370, `agent/factorio-companion.json:17`). `scripts/test-unit.sh` runs Python 3.12, so 3.10+ code in the server, `kc` or skills passes the unit tests and breaks for Kira (`scripts/test-unit.sh:15`).
- A new file the server needs at runtime must also go into `scripts/build-app.sh`. It copies only `factorio_mcp.py`, `kc_compute.py`, `mcp/kc/` and `skills/` (`scripts/build-app.sh:18-23`).
- Anything the server prints to stdout other than a JSON-RPC line breaks the stdio transport (inferred: `main` :964-978 writes responses to stdout).
- `main` reads stdin with its own buffer over fd 0, not `sys.stdin`: on re-exec everything read but not handled must go to the new process, and `sys.stdin` would hide what it read ahead. A tool added by an update is not seen by an open chat until it restarts (the client lists tools once; the server declares no `listChanged`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_compute.py tests/unit/test_panel_tool.py tests/unit/test_kc_atlas.py`: compute (data in, errors, timeout, seatbelt, nested sandbox, kc and skills importable), panel Lua and artifacts against a fake `kirocrew`, and atlas through a fake bridge. A few seconds. The two sandbox tests skip off macOS.
- `./scripts/test-unit.sh tests/unit/test_app_status.py -k mcp_probe`: starts the real server over stdio and lists its tools (`test_mcp_probe_starts_the_real_server`).
- `./scripts/test-integration.sh` (~65 s, Docker, headless 2.1.23): the real server over stdio (`tests/integration/conftest.py` `Mcp`, started with `FACTORIO_BRIDGE_URL`). `test_bridge.py` covers query/mark/unmark/list_marks/reply/progress. `test_atlas_game.py` covers `factorio_atlas`. The other `test_*_game.py` files go through `factorio_query`. Level 2 never calls `factorio_compute`, `factorio_panel` or `factorio_saved_panels` (`grep -o 'mcp.tool("[a-z_]*"' tests/integration/*.py`). Pass `-k <name>` to run one test.
- End to end: `tests/e2e/test_panels_e2e.py` (panels, saved panels, restore) under `./scripts/test-e2e.sh`. `tests/app_e2e/test_app_e2e.py::test_oil_field_nearest_cluster` and `tests/app_e2e/test_uncoded.py` (compute inside the real gateway sandbox) under `./scripts/test-app-e2e.sh`.
- By hand after a change: `./scripts/stand.sh check` reports Kira MCP processes with the wrong `--bridge` or started before the file's last change (`scripts/stand.sh:156-173`, `:203-206`).

## Decisions

- `factorio_query` runs any Lua, with no sandbox; read-only is an agent rule: v1 is single player, and every query shows in the chat. [SPEC.md "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio), last paragraph; "Decided: `load` in the mod runtime"; confirmed by Michael 2026-10-29.
- `--bridge` on the command line comes before the env variable, because the agent spec's env block does not reach the MCP process. [../worktrees.md "Checking only your own stand"](../worktrees.md#checking-only-your-own-stand); `mcp/factorio_mcp.py:22-27`.
- Under an outer sandbox, compute runs without its own profile rather than not at all, because macOS refuses a nested one. [SPEC.md "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio), `factorio_compute`.
- `factorio_atlas` is one tool with flat parameters, because weak models handle that better than composing Python. [../atlas.md "Tool"](../atlas.md#tool).
- When SPEC, README and code disagree, the code wins (confirmed by Michael 2026-10-29).

## Terms

- **bridge**: `bridge/factorio_bridge.py`, the HTTP-to-UDP relay to the game on 127.0.0.1:34198 by default.
- **`_agent`**: a body flag meaning "this call is a step of Kira's turn". The bridge counts these calls and updates the agent liveness from them.
- **seatbelt**: the macOS `sandbox-exec` profile language.
- **outer sandbox**: the gateway's sandbox the server runs in under Kiro Crew; compute then reports `sandboxed: false`.
- **`data_lua`**: Lua that compute runs in the game first. Its return value is the Python variable `data`.
