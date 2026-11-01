# Error reports and bug reports through Sentry

Status: T1 (`report/kc_report.py`, the switch on the status page) and T2 (every part reports) done; T3, T4 open. Branch `feature/sentry`, agreed with Michael on 2026-11-01. This is also the area doc for `report/`.

Why: on 2026-10-31 a player's tab kept "thinking" after Kiro credits ran out, and the cause (an old dispatcher still running) could not be seen from the developer's machine. Reports bring that data back without the player logging in anywhere.

## Decided

- **Sentry, no SDK.** One stdlib POST of an envelope to `https://<host>/api/<project>/envelope/`, authenticated with `X-Sentry-Auth` ([Envelopes](https://develop.sentry.dev/sdk/data-model/envelopes/)). A DSN only allows sending new events, never reading, so it may ship inside the app ([DSN](https://docs.sentry.io/concepts/key-terms/dsn-explainer/)). Why not `sentry-sdk`:
  - All Python here is stdlib only (README): the app asks the player for `python3` alone, the dispatcher runs on the macOS system `python3` 3.9, the bridge and the MCP server start as `python3 <file>` with no venv. The SDK would have to be vendored into the app with `urllib3` and `certifi`, a new part of the build and of every update.
  - The SDK sends more than agreed by default: local variables of stack frames (`include_local_variables`) and log lines as breadcrumbs, and in the bridge and the dispatcher those hold the player's question. Keeping that out would rest on SDK settings; a hand-built envelope lists what goes.
  - Most of what we send is not a Python exception (Lua errors, the bug-report zip, Kira's feedback), so it is built by hand anyway. What the SDK would add is the exception hook and a retrying transport; the transport is in `report/kc_report.py`, the hook is T2.
- **Environments:** `prod` for what players run, `dev-<hostname>` for everything else. `scripts/release.sh` builds with `FC_REPORT_ENV=prod`; `scripts/build-app.sh` stamps `report/environment` into the build (`dev-<host>` unless told otherwise, so an app e2e or app-dev install never writes into `prod`); a checkout has no stamp and reports as `dev-<host>`. Only `dev-*` events carry the host name (`server_name`); a release sends none.
- **The DSN** ships in `report/settings.json`. The app also fetches that file from the `release` branch at most once a day and caches it in the outbox, so a rotated DSN reaches old installs; a fetched DSN is used only if it points at `*.sentry.io`, so the branch cannot send reports anywhere else. `FC_SENTRY_DSN` overrides both (empty: send nothing; tests point it at a fake receiver).
- **Automatic errors are on by default** and can be turned off on the app's status page (`telemetry` in `<app>/data/config.json`). The page states what is sent next to the switch. A notice in the game window (shown once) is part of T3, which is the first task that changes the window.
- **What an automatic error carries:** versions of every part, error type, message (trimmed to 500 chars), stack without local variables, and for a Lua error the Lua code that failed (Kira's `factorio_query` code, a panel's code). Never the player's question, Kira's chat or logs. The OS user name is cut out of paths (`/Users/<x>`, `/home/<x>`, `C:\Users\<x>` become `~`).
- **Chat, logs and screenshots go only from the button and from Kira's skill**, after the player sees what goes and says yes.
- **The same error goes at most once an hour** (by type, top frame and release), so a loop does not eat the free quota (Developer plan: one user, 30-day retention).
- **Playing with no network is normal** (local models, no internet). Reporting never blocks or slows the game, a question or a tool call, and never fills the logs with network errors. What could not be sent waits in a local outbox and goes when the network is back.

## Sending and retries

- **Never on the caller's path.** A report is written to the outbox (`<KIROCREW_HOME>/factorio/outbox/<ms>-<auto|report>-<event_id>.envelope`, through a temp file and `os.replace`; `FC_REPORT_OUTBOX` overrides) and the caller returns. A background thread sends, gzipped; each POST has a 5 s timeout.
- **What is retried:** no network, DNS failure, timeout, HTTP 5xx. The file stays and is tried again after 1 min, 5 min, 30 min, then hourly, at every start of a part that sends, and whenever a new report is queued; once one send succeeds, the whole outbox goes at once. The first failure in a row logs one line ("reports wait in the outbox"); later ones log nothing.
- **What is not retried:** any other answer that is not 2xx or 429 (400, 401, 403, 413: bad envelope, DSN revoked, too large): the file is dropped with one log line. HTTP 429: nothing is sent until `Retry-After` has passed, and the automatic errors queued and arriving meanwhile are dropped, as Sentry asks ([quota](https://docs.sentry.io/product/accounts/quotas/manage-attachments-quota/)); bug reports and Kira's feedback stay queued.
- **Limits:** at most 50 files and 50 MB in the outbox, the oldest automatic error goes first, a bug report last. An automatic error is kept up to 30 days, so weeks of offline play still reach Sentry with the time it happened (whether Sentry accepts an event that old is not checked); a bug report is kept until it is sent.
- **One sender at a time:** a part takes `flock` on `<outbox>/.send.lock` before sending (none on Windows, where Sentry's dedup by `event_id` keeps a double send harmless).
- **Switch off means off:** with `telemetry=false` no automatic error is written, and those already queued are deleted before the next send, also in a part that is already running. The button and Kira's skill still work, because the player asks for them explicitly.
- **Offline bug report:** the window says "saved, it will go when the network is back" and shows the zip's path, so the player can also hand it over by other means (T3).

## Main files

- `report/kc_report.py`: `Reporter` (settings, `capture_exception`, `capture_message`, `send_report`, the outbox, `flush`, the sender thread) and module functions `configure(part)`, `capture_exception`, `capture_message`, `send_report` that never raise.
- `report/settings.json`: the shipped DSN. `report/environment`: written only into a build.
- `app/backend/server.py` `settings`, `save_settings`: `GET`/`POST /api/settings` behind the gateway's `/apps/factorycrew/api/settings`; `app/ui/index.mjs` `Reports`: the switch.
- `scripts/build-app.sh` copies `report/` into the app and stamps the environment; `scripts/release.sh` builds with `FC_REPORT_ENV=prod`.

## Easy to break

- Turning the switch writes `data/config.json`, whose mtime the dispatcher watches: its wake exits and the next one starts within 30 s, and a turn it was following loses its live trace (`dispatcher/factorio_dispatch.py` `source_stamp`).
- From T2 on, every part reports. Test scripts and CI must set `FC_SENTRY_DSN` to a fake receiver or to empty, or planted test errors land in `dev-<host>` (or in `prod`, from a test of a release build).
- The exception message is sent: an exception built from player text would carry it. Messages are trimmed to 500 chars; code that raises with a question in the text must not report it.

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_report.py tests/unit/test_app_status.py`: the envelope, scrubbing, environments, rate limit, outbox limits, every HTTP answer, offline and back, the switch over HTTP, against a fake Sentry on localhost. ~10 s.
- A real send: `Reporter("smoke", outbox=<scratch dir>, settings_url=None).capture_exception(...)`, then `flush()`; Sentry answered 200 for an event and for a `feedback` with an attachment on 2026-11-01 (environment `dev-mac`).

## Checked

- Network from app processes (2026-11-01, Kiro Crew on macOS): the macOS sandbox profile is `(allow default)` plus file denies only (`kiro_crew/sandbox_seatbelt.py`); the Linux path has no `--unshare-net`. A POST from the system `python3` 3.9 inside a Kiro Crew sandboxed shell reached Sentry. The backend, MCP server, dispatcher and bridge can send directly; no route through the bridge is needed.
- The app SDK's `api.post(url, body)` sends JSON, and the gateway proxies every method to the backend with its body (`kiro_crew/apps/routes.py`, `add_route("*", "/apps/{name}/api/{path:.*}")`).
- Sentry accepted (HTTP 200) an `event` and a `feedback` with an attachment from `report/kc_report.py` on the system `python3` 3.9 (2026-11-01). 200 means accepted for processing; that they show up in the project is not checked from here.

## Not checked

- The free plan's attachment quota (third-party sources say 1 GB/month; Sentry's docs state it for paid plans only). Read it in Usage Stats.
- Whether the Developer plan can turn off storing IP addresses; events from Python are not supposed to get an inferred IP, but that is not confirmed.
- Attachment size per request: Sentry's pages say 20 MB or 40 MB compressed; keep the zip under 20 MB.

## Tasks

**T1. Sender, outbox and switch.** Done. A stdlib module for every part that reports: builds an envelope (event or `feedback`, optional attachments), strips the user name from paths, rate-limits by fingerprint, writes it to the outbox and sends it in the background as in "Sending and retries". DSN and environment as in "Decided". The switch on the status page, default on.
- Done when: unit tests cover the envelope format, path stripping, the rate limit, the outbox limits and the answer to each HTTP status; with `telemetry=false` no request is made and the queued errors are gone; a send to a local fake receiver passes; with the receiver down several reports wait in the outbox, the caller returns in under 50 ms, and when the receiver comes up all of them arrive after the next retry, with their original timestamps. All in `tests/unit/test_report.py`, `tests/unit/test_app_status.py`.

**T2. Automatic errors.** Done. What reports what:
- **Bridge** (`bridge/factorio_bridge.py`): a packet whose handling raised, a failed atlas prefetch, an HTTP handler that raised; Kira's `factorio_query` that came back `compile_error`/`runtime_error` (only calls flagged `_agent`), with `code.lua`; and the mod's new `lua_error` message. Tags: `part`, `source`, `mod_version`, `app_version`.
- **Mod** (`control.lua`, `scripts/protocol.lua`, `scripts/panels.lua`): sends `lua_error` `{source, name?, message, lua?}` for a panel body's first failure in a row (`panel`), every failed panel button (`panel_action`), and an error in the mod's own handler (`mod`, with the traceback). The UDP handler, the 60-tick handler and the GUI handlers run under `xpcall`: an error there no longer stops the game with Factorio's error screen; the game prints one line per session, and a request the bridge waits on is answered `mod_error` instead of timing out. Queue capped at 20 between sends; module memory, not `storage`.
- **MCP server** (`mcp/factorio_mcp.py`): a tool that raised (it now answers `internal` instead of killing the server), and Kira's `factorio_compute` code that failed (`ComputeError`, with `main.py` and `stderr.txt`), on the same terms as her Lua.
- **Dispatcher** (`dispatcher/factorio_dispatch.py`): a wake that raised; a question it could not pass after the last attempt; a turn Kiro Crew ended with an error row, as a `warning` `KiroCrewTurnError` with Kiro Crew's text and `usage_limit` (the 2026-10-31 case). Its printed lines are kept for a bug report (`note`, `RECENT_LOG`).
- **Backend** (`app/backend/server.py`): a failed mod install, an HTTP handler that raised. It sets reports up for the bridge running in its process.
- Every part: uncaught exceptions of the main thread and of other threads (`install_hooks`), and the bridge's asyncio loop (`asyncio_handler`).
- At most 30 automatic errors an hour per machine on top of the once-an-hour rule (`MAX_AUTO_PER_HOUR`).
- `scripts/test-unit.sh`, `test-integration.sh`, `test-e2e.sh`, `test-app-e2e.sh` export `FC_SENTRY_DSN` empty unless it is given, so `./scripts/check.sh` and CI send nothing; the L2 harness starts a fake Sentry for the bridge and the MCP server (`tests/integration/conftest.py`, fixture `sentry`). `scripts/kc.sh install` copies `report/` to `<home>/crons/report/` for the installed dispatcher.
- Checked by `tests/unit/test_report_parts.py` (each Python part against the fake) and `tests/integration/test_reports_game.py` (the real mod, bridge and MCP: Kira's failing query with its code and once only, a failing panel once, an error in the mod's handler answered and reported, no player text in any event).

**T3. "Send a bug report" button.** In the game window, a copy on the app page. Comment field, the list of what goes, Send. A zip: versions, bridge `/health`, tails of the bridge and dispatcher logs, the current tab's chat, an optional screenshot. The player gets a short report id. If sending fails, the zip is saved locally and its path shown. The window shows once that error reports are on and where to turn them off.
- Done when: the app e2e presses the button and the fake receiver has a zip with every part; the id shows in the window. Needs T1.

**T4. Kira's skill "suggest a feature or report a bug".** A tool `factorio_feedback(kind, text)` and a skill. Kira writes the report from the conversation, shows it, and sends only after a yes, with the same data as T3.
- Done when: an e2e with a real model: the player writes "Kira, this is a bug, the panel does not update", Kira shows the text, after "yes" a `feedback` event is on the fake receiver; without the yes nothing is sent. Needs T3.
