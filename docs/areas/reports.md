# Error and bug reports

`report/` sends what went wrong on a player's machine to Sentry: automatic errors from every Python part and from the mod, and bug reports the player or Kira sends on purpose. It never blocks a caller and works offline through an outbox. The design, the reasons and what each part reports are in [Error reports and bug reports through Sentry](../sentry.md); this doc adds what a change to the code needs.

## Main files

- `report/kc_report.py`: the whole sender, stdlib only (`report/kc_report.py:1-40`).
  - `Reporter`: settings (`telemetry`, `dsn`, `_refresh_settings`), `capture_exception`, `capture_message`, `send_report`, `bug_report`, the outbox (`_queue`, `_trim`, `files`) and the sender thread (`start`, `_loop`, `flush`) (`:106-470`).
  - Module functions `configure(part)`, `capture_exception`, `capture_message`, `bug_report`, `send_report`, which never raise when not configured (`:472-500`, `:692`; `tests/unit/test_report.py::test_module_functions_never_raise_when_not_configured`).
  - `session_files`, `recent_slots`: one chat's raw session for a bug report (`:569-655`).
  - `install_hooks`, `asyncio_handler`: uncaught exceptions of threads and of the bridge's loop (`:657-682`).
  - Limits: `TIMEOUT` 5 s, `RETRY_STEPS` 1/5/30/60 min, `MAX_FILES` 50, `MAX_BYTES` 50 MB, `AUTO_MAX_AGE` 30 days, `RATE_WINDOW` 1 h, `MAX_AUTO_PER_HOUR` 30, `BUG_REPORT_MAX` 15 MB (`:49-60`).
- `report/settings.json`: the shipped DSN. `report/environment`: exists only in a build, written by `scripts/build-app.sh:27-28`.

## Talks to

- Used by: [Bridge](bridge.md), through `report.configure("bridge")`, `report_exception`, `Bridge.report_lua` and `Bridge.bug_fallback` (`bridge/factorio_bridge.py:35-45`, `:72`, `:453`, `:470`, `:954-955`).
- Used by: [Dispatcher](dispatcher.md), through `capture_exception`, the `KiroCrewTurnError` warning and `bug_report` (`dispatcher/factorio_dispatch.py:50-60`, `:113`, `:519-520`, `:781`, `:869-870`).
- Used by: [MCP server](mcp-server.md), through a tool that raised, `ComputeError` and `factorio_feedback` (`mcp/factorio_mcp.py:43-51`, `:453`, `:825`, `:1207-1208`).
- Used by: [App](app.md) backend, which loads the module through the bridge (`fb.report`), configures it for itself and the bridge in its process, serves the switch (`/api/settings`, shown by `Reports` in `app/ui/index.mjs`) and the app page's report (`/api/bug_report`, `BugReport`) (`app/backend/server.py:31`, `:45-86`, `:152`, `:181-182`).
- Used by: [Mod core](mod-core.md), indirectly: the mod queues `lua_error` and sends `bug_report` over UDP, and the bridge reports them (`mod/kc-companion/scripts/protocol.lua:52-70`, `:203`).
- Uses: Sentry's envelope endpoint over HTTPS, and `report/settings.json` on the `release` branch at most once a day (`report/kc_report.py:45`, `:157`).
- Copied by: `scripts/build-app.sh` into the app (`scripts/build-app.sh:22-28`), and `scripts/kc.sh install` to `<home>/crons/report/` for an installed dispatcher (`scripts/kc.sh:54-57`).

## Must stay true

- An automatic error carries versions, type, message and stack, and no OS user name in any path (`tests/unit/test_report.py::test_an_error_goes_as_an_event_with_versions_and_no_user_name`, `::test_paths_of_any_user_are_scrubbed`).
- A release sends no host name; a dev build does (`tests/unit/test_report.py::test_release_sends_no_host_name_and_dev_does`).
- The same automatic error goes at most once an hour (`tests/unit/test_report.py::test_the_same_error_goes_once_an_hour`).
- With `telemetry` off no automatic error is written and queued ones are dropped, also in a running part; bug reports still go (`tests/unit/test_report.py::test_telemetry_off_sends_no_error_and_drops_queued_ones_but_reports_still_go`, `::test_switching_off_while_running_drops_queued_errors_before_sending`).
- Offline, reports wait in the outbox and all go when the network is back; 5xx is retried, 400/401/403/413 dropped, 429 waits and drops automatic errors only (`tests/unit/test_report.py::test_offline_reports_wait_and_all_go_when_the_network_is_back`, `::test_server_errors_are_retried_and_refusals_dropped`, `::test_rate_limit_waits_and_drops_automatic_errors_but_keeps_reports`).
- A full outbox drops automatic errors before bug reports (`tests/unit/test_report.py::test_outbox_keeps_reports_over_automatic_errors_when_full`).
- A DSN fetched from the `release` branch is used only if it points at `*.sentry.io`; `<KIROCREW_HOME>/factorio/report.json` overrides the shipped DSN (`tests/unit/test_report.py::test_a_fetched_dsn_must_point_at_sentry`, `::test_this_machines_report_json_overrides_the_shipped_dsn`).
- No automatic error carries the player's question or Kira's chat; only a bug report the player sent does (`tests/integration/test_reports_game.py::test_kiras_failing_query_goes_with_its_code` checks it for a failing query with a question queued; the panel and mod-handler tests do not, `grep -n assert_no_player_text tests/integration/test_reports_game.py`).
- Kira's `factorio_feedback` sends nothing until the player confirms the draft (`tests/unit/test_bug_report.py::test_kiras_feedback_sends_nothing_until_the_draft_is_confirmed`, `tests/e2e/test_reports_e2e.py::test_kira_drafts_a_bug_report_and_sends_it_only_after_yes`).
- A bug report from the window still goes when no dispatcher takes it in 15 s (`tests/integration/test_reports_game.py::test_a_bug_report_from_the_mod_reaches_sentry_without_a_dispatcher`).
- A zip over 15 MB keeps the end of its biggest file (`tests/unit/test_bug_report.py::test_a_report_over_the_limit_keeps_the_end_of_the_biggest_file`).
- Stdlib only, runs on the macOS system `python3` 3.9, like the bridge and the dispatcher that load it (`report/kc_report.py:1-40`, [Bridge](bridge.md) "Must stay true").

## Easy to break

- Tests that forget `FC_SENTRY_DSN` send planted errors to the real project. The test scripts export it empty unless given, and the e2e scripts also write `{"dsn": ""}` to the stand's `factorio/report.json`, since the gateway may not pass env to crons and MCP servers (`scripts/test-unit.sh:10-12`, `scripts/test-integration.sh:8-10`, `scripts/test-e2e.sh:72`, `report/kc_report.py:144-155`).
- Turning the switch writes `data/config.json`, whose mtime the dispatcher watches: its wake exits and the next one starts within 30 s, and a turn it was following loses its live trace (`dispatcher/factorio_dispatch.py` `source_stamp`).
- The exception message is sent, trimmed to 500 characters: an exception raised with player text in it carries that text (`report/kc_report.py:200-218`).
- A new file in `report/` must be added to both copies, or the app and the installed dispatcher run without it (`scripts/build-app.sh:26`, `scripts/kc.sh:56`).
- Each part finds `report/` one level up, or next to itself for the installed dispatcher; a part that cannot find it runs with `report = None` and sends nothing, silently (`bridge/factorio_bridge.py:35-45`, `dispatcher/factorio_dispatch.py:50-60`).
- `SETTINGS_URL` points at `apps/factorycrew/report/settings.json` on the `release` branch; moving that file there breaks DSN rotation for every installed app (`report/kc_report.py:45`).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_report.py tests/unit/test_report_parts.py tests/unit/test_bug_report.py tests/unit/test_app_status.py`: the envelope, scrubbing, environments, limits, every HTTP answer, offline and back, each Python part, bug reports and raw sessions, the switch over HTTP (`tests/unit/test_app_status.py:209-213`), against a fake Sentry on localhost (~10 s).
- `./scripts/test-integration.sh -k reports`: the real mod, bridge and MCP against the fake Sentry (`tests/integration/test_reports_game.py`, fixture `sentry` in `tests/integration/conftest.py`).
- `./scripts/test-e2e.sh -k reports`: the window's report with the tab's chat and Kira's two-step feedback, real model (`tests/e2e/test_reports_e2e.py`).
- A real send by hand: `Reporter("smoke", outbox=<scratch dir>, settings_url=None).capture_exception(...)`, then `flush()`, and look in the Sentry project under environment `dev-<host>`.

## Decisions

- Sentry with a hand-built envelope and no SDK, environments `prod`/`dev-<host>`, the DSN shipped and refreshed from `release`, automatic errors on by default with a switch, what an error carries, chat and logs only on the player's yes, once an hour per error, offline play normal: [decisions.md](../decisions.md), the entries from "Error reports go to Sentry" on. The reasons are in [sentry.md, "Decided"](../sentry.md#decided).

## Terms

- **automatic error**: an event a part sends by itself, `-auto-` in its outbox file name (`report/kc_report.py:305-320`).
- **bug report**: a Sentry `feedback` with one `report.zip`, from the window (`button`), the app page (`app`) or Kira (`kira`); a copy stays in `<KIROCREW_HOME>/factorio/bug-reports/` (`report/kc_report.py:257-300`).
- **outbox**: `<KIROCREW_HOME>/factorio/outbox/`, the envelopes waiting to be sent (`report/kc_report.py:117-118`).
- **environment**: `prod` for releases, `dev-<host>` for everything else (`report/kc_report.py:111-112`).
