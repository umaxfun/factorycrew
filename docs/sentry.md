# Error reports and bug reports through Sentry

Status: plan, branch `feature/sentry`. Agreed with Michael on 2026-11-01.

Why: on 2026-10-31 a player's tab kept "thinking" after Kiro credits ran out, and the cause (an old dispatcher still running) could not be seen from the developer's machine. Reports bring that data back without the player logging in anywhere.

## Decided

- **Sentry, no SDK.** One stdlib POST of an envelope to `https://<host>/api/<project>/envelope/`, the DSN in the envelope header ([Envelopes](https://develop.sentry.dev/sdk/data-model/envelopes/)). A DSN only allows sending new events, never reading, so it may ship inside the app ([DSN](https://docs.sentry.io/concepts/key-terms/dsn-explainer/)). Why not `sentry-sdk`:
  - All Python here is stdlib only (README): the app asks the player for `python3` alone, the dispatcher runs on the macOS system `python3` 3.9, the bridge and the MCP server start as `python3 <file>` with no venv. The SDK would have to be vendored into the app with `urllib3` and `certifi`, a new part of the build and of every update.
  - The SDK sends more than agreed by default: local variables of stack frames (`include_local_variables`) and log lines as breadcrumbs, and in the bridge and the dispatcher those hold the player's question. Keeping that out would rest on SDK settings; a hand-built envelope lists what goes.
  - Most of what we send is not a Python exception (Lua errors, the bug-report zip, Kira's feedback), so it is built by hand anyway. What the SDK would add is the exception hook and a retrying transport; both are below (T1), about 150 lines.
- **The DSN is a file on the `release` branch**, so it can be rotated without an app update.
- **Automatic errors are on by default** and can be turned off in the app (`telemetry` in `data/config.json`). The app says so once, on first start.
- **What an automatic error carries:** versions of every part, error type, stack, and for a Lua error the Lua code that failed (Kira's `factorio_query` code, a panel's code). Never the player's question, Kira's chat or logs. The OS user name is cut out of paths.
- **Chat, logs and screenshots go only from the button and from Kira's skill**, after the player sees what goes and says yes.
- **The same error goes at most once an hour**, so a loop does not eat the free quota (Developer plan: one user, 30-day retention).
- **Playing with no network is normal** (local models, no internet). Reporting never blocks or slows the game, a question or a tool call, and never fills the logs with network errors. What could not be sent waits in a local outbox and goes when the network is back.

## Sending and retries

- **Never on the caller's path.** A report is written to the outbox (`<KIROCREW_HOME>/factorio/outbox/<id>.envelope`, through a temp file and `os.replace`) and the caller returns. A background thread sends; each POST has a 5 s timeout.
- **What is retried:** no network, DNS failure, timeout, HTTP 5xx. The file stays and is tried again after 1 min, 5 min, 30 min, then hourly, and at every start of a part that sends (bridge, dispatcher wake, MCP server, backend). The first failure in a row logs one line ("reports wait in the outbox: no network"); later ones log nothing.
- **What is not retried:** HTTP 400, 401, 403, 413 (bad envelope, DSN revoked, too large): the file is dropped with one log line. HTTP 429: nothing is sent until `Retry-After` has passed, and the automatic errors queued meanwhile are dropped, as Sentry asks ([quota](https://docs.sentry.io/product/accounts/quotas/manage-attachments-quota/)); bug reports and Kira's feedback stay queued.
- **Limits:** at most 50 files and 50 MB in the outbox, the oldest automatic error goes first, a bug report last. An automatic error older than 7 days is dropped; a bug report is kept until it is sent or the player deletes it.
- **One sender at a time:** each part takes a lock file in the outbox before sending, so two parts do not send the same file twice.
- **Switch off means off:** with `telemetry=false` no automatic error is written, and those already queued are deleted. The button and Kira's skill still work, because the player asks for them explicitly.
- **Offline bug report:** the window says "saved, it will go when the network is back" and shows the zip's path, so the player can also hand it over by other means.

## Checked

- Network from app processes (2026-11-01, Kiro Crew on macOS): the macOS sandbox profile is `(allow default)` plus file denies only (`kiro_crew/sandbox_seatbelt.py`); the Linux path has no `--unshare-net`. A POST from the system `python3` 3.9 inside a Kiro Crew sandboxed shell reached Sentry (HTTP 401 without a DSN, TLS fine). The backend, MCP server, dispatcher and bridge can send directly; no route through the bridge is needed.

## Not checked

- The free plan's attachment quota (third-party sources say 1 GB/month; Sentry's docs state it for paid plans only). Read it in Usage Stats once the project exists.
- Whether the Developer plan can turn off storing IP addresses.
- Attachment size per request: Sentry's pages say 20 MB or 40 MB compressed; keep the zip under 20 MB.

## Tasks

**T1. Sender, outbox and switch.** A stdlib module used by bridge, dispatcher, MCP server and app backend: builds an envelope (event or `feedback`, optional attachments), strips the user name from paths, rate-limits by fingerprint, writes it to the outbox and sends it in the background as in "Sending and retries". DSN from the `release` branch file. `telemetry` flag in the app's settings, default on, one-time notice.
- Done when: unit tests cover the envelope format, path stripping, the rate limit, the outbox limits and the answer to each HTTP status; with `telemetry=false` no request is made and the queued errors are gone; a send to a local fake receiver passes; with the receiver down a report waits in the outbox, the caller returns in under 50 ms, and the report arrives once the receiver is up.

**T2. Automatic errors.** Uncaught exceptions in bridge, dispatcher, MCP server and backend; Lua errors in the mod reach Sentry through the bridge with the failing Lua code. Every event carries the versions of all parts.
- Done when: an integration test plants one error in each part and gets exactly one event each on the fake receiver, with no player text. Needs T1.

**T3. "Send a bug report" button.** In the game window, a copy on the app page. Comment field, the list of what goes, Send. A zip: versions, bridge `/health`, tails of the bridge and dispatcher logs, the current tab's chat, an optional screenshot. The player gets a short report id. If sending fails, the zip is saved locally and its path shown.
- Done when: the app e2e presses the button and the fake receiver has a zip with every part; the id shows in the window. Needs T1.

**T4. Kira's skill "suggest a feature or report a bug".** A tool `factorio_feedback(kind, text)` and a skill. Kira writes the report from the conversation, shows it, and sends only after a yes, with the same data as T3.
- Done when: an e2e with a real model: the player writes "Kira, this is a bug, the panel does not update", Kira shows the text, after "yes" a `feedback` event is on the fake receiver; without the yes nothing is sent. Needs T3.

## Needed from Michael

- A Sentry project and its DSN.
