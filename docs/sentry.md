# Error reports and bug reports through Sentry

Status: plan, branch `feature/sentry`. Agreed with Michael on 2026-11-01.

Why: on 2026-10-31 a player's tab kept "thinking" after Kiro credits ran out, and the cause (an old dispatcher still running) could not be seen from the developer's machine. Reports bring that data back without the player logging in anywhere.

## Decided

- **Sentry, no SDK.** One stdlib POST of an envelope to `https://<host>/api/<project>/envelope/`, the DSN in the envelope header ([Envelopes](https://develop.sentry.dev/sdk/data-model/envelopes/)). A DSN only allows sending new events, never reading, so it may ship inside the app ([DSN](https://docs.sentry.io/concepts/key-terms/dsn-explainer/)).
- **The DSN is a file on the `release` branch**, so it can be rotated without an app update.
- **Automatic errors are on by default** and can be turned off in the app (`telemetry` in `data/config.json`). The app says so once, on first start.
- **What an automatic error carries:** versions of every part, error type, stack, and for a Lua error the Lua code that failed (Kira's `factorio_query` code, a panel's code). Never the player's question, Kira's chat or logs. The OS user name is cut out of paths.
- **Chat, logs and screenshots go only from the button and from Kira's skill**, after the player sees what goes and says yes.
- **The same error goes at most once an hour**, so a loop does not eat the free quota (Developer plan: one user, 30-day retention).

## Checked

- Network from app processes (2026-11-01, Kiro Crew on macOS): the macOS sandbox profile is `(allow default)` plus file denies only (`kiro_crew/sandbox_seatbelt.py`); the Linux path has no `--unshare-net`. A POST from the system `python3` 3.9 inside a Kiro Crew sandboxed shell reached Sentry (HTTP 401 without a DSN, TLS fine). The backend, MCP server, dispatcher and bridge can send directly; no route through the bridge is needed.

## Not checked

- The free plan's attachment quota (third-party sources say 1 GB/month; Sentry's docs state it for paid plans only). Read it in Usage Stats once the project exists.
- Whether the Developer plan can turn off storing IP addresses.
- Attachment size per request: Sentry's pages say 20 MB or 40 MB compressed; keep the zip under 20 MB.

## Tasks

**T1. Sender and switch.** A stdlib module used by bridge, dispatcher, MCP server and app backend: builds an envelope (event or `feedback`, optional attachments), strips the user name from paths, rate-limits by fingerprint, posts. DSN from the `release` branch file. `telemetry` flag in the app's settings, default on, one-time notice.
- Done when: unit tests cover the envelope format, path stripping and the rate limit; with `telemetry=false` no request is made; a send to a local fake receiver passes.

**T2. Automatic errors.** Uncaught exceptions in bridge, dispatcher, MCP server and backend; Lua errors in the mod reach Sentry through the bridge with the failing Lua code. Every event carries the versions of all parts.
- Done when: an integration test plants one error in each part and gets exactly one event each on the fake receiver, with no player text. Needs T1.

**T3. "Send a bug report" button.** In the game window, a copy on the app page. Comment field, the list of what goes, Send. A zip: versions, bridge `/health`, tails of the bridge and dispatcher logs, the current tab's chat, an optional screenshot. The player gets a short report id. If sending fails, the zip is saved locally and its path shown.
- Done when: the app e2e presses the button and the fake receiver has a zip with every part; the id shows in the window. Needs T1.

**T4. Kira's skill "suggest a feature or report a bug".** A tool `factorio_feedback(kind, text)` and a skill. Kira writes the report from the conversation, shows it, and sends only after a yes, with the same data as T3.
- Done when: an e2e with a real model: the player writes "Kira, this is a bug, the panel does not update", Kira shows the text, after "yes" a `feedback` event is on the fake receiver; without the yes nothing is sent. Needs T3.

## Needed from Michael

- A Sentry project and its DSN.
