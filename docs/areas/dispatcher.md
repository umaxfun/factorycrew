# Dispatcher

The dispatcher is a Kiro Crew script-cron. It takes the questions the game sent to the [Bridge](bridge.md) and puts each one into its chat `Factorio/<run>/<tab>`, then follows that chat's transcript so the tab sees the turn as it runs. The flow, the cron settings and the reasons for the private calls are in [SPEC.md "How it works"](../../SPEC.md#how-it-works) (the **Dispatcher** paragraph and the numbered steps) and [SPEC.md "The message the dispatcher puts in the chat"](../../SPEC.md#the-message-the-dispatcher-puts-in-the-chat); this doc adds what a change to the code needs.

## Main files

- `dispatcher/factorio_dispatch.py`: the whole dispatcher, stdlib only.
  - `CONFIG`, `app_config`, `run`: settings, overrides and the wake loop (`dispatcher/factorio_dispatch.py:33-41`, `:511`, `:526`).
  - `state_path`, `load_state`, `save_state`: `dispatch-state.json` (`:62-86`).
  - `_private_post`, `ensure_folder`, `run_folder`, `slot_key`, `open_chat`: folders and chats in Kiro Crew (`:92-162`).
  - `reply_language`, `render`, `render_pointer`: the chat message (`:173-238`).
  - `Tracer`, `trace_lines`, `turn_ended`, `older_than`: the live trace and the fallback forward (`:244-377`).
  - `library*`, `artifact_cli`: the panel library, see [Panels](panels.md) (`:380-463`).
  - `handle`: one question, with the `not_creator` retry and the 3-attempt limit (`:466`).
- Installed copies: `<KIROCREW_HOME>/crons/factorio_dispatch.py` by `scripts/kc.sh install` (`scripts/kc.sh:47-53`), and `<app>/dispatcher/` by `scripts/build-app.sh` (`scripts/build-app.sh:20`).

## Talks to

- Uses: [Bridge](bridge.md), through `GET /asks?wait=<≤25>&lease=60`, `POST /asks/done`, `/reply` (the failure notice and the fallback copy), `/trace`, `/library_result` (`dispatcher/factorio_dispatch.py:554`, `:495`, `:492`, `:327`, `:371`, `:460`).
- Uses: Kiro Crew `ScriptContext`: `list_session_folders`, private `_post("/api/chat/folders")` and `_post("/api/chat/slots")`, `set_session_mode(slot, "trust_reads")`, `send_to_session` (`dispatcher/factorio_dispatch.py:92-160`, `:471`).
- Uses: the chat transcript `<KIROCREW_HOME>/sessions/dashboard_<slot>.jsonl`, read-only (`dispatcher/factorio_dispatch.py:261-263`).
- Uses: the `kirocrew artifact` CLI for the library (`dispatcher/factorio_dispatch.py:401`), see [Panels](panels.md).
- Uses: `<app>/data/config.json` for `http_port`, `agent`, `state_file` when it runs as the app's cron (`dispatcher/factorio_dispatch.py:511-523`).
- Used by: Kiro Crew, which runs `run(ctx)` as cron `factorio-dispatch`: from `app/app.json:29-38` in the [App](app.md), or registered by `scripts/test-e2e.sh:26-29` and `scripts/stand.sh:194-196` on a [dev stand](dev-stand.md) gateway.
- Used by: [Kira](kira-agent.md) (agent `factorio-companion`), which reads the header it writes (`agent/factorio-companion.md:7`, `:22`).
- Used by: tests that read `dispatch-state*.json` to find chats: `tests/e2e/watch.py:38-40`, `tests/e2e/test_panels_e2e.py:64`, `tests/app_e2e/test_app_e2e.py:56`.

## Must stay true

- A question is confirmed (`/asks/done`) only after `send_to_session` returned, or after its third failure was reported to the tab as "Could not pass the question to Kiro Crew: <reason>"; an unconfirmed one comes back by lease (`dispatcher/factorio_dispatch.py:486-496`; no unit test, the lease side is `tests/integration/test_bridge.py::test_asks_wait_while_dispatcher_is_away`).
- One chat per `run_id` + tab: key `factorio-<sha1(run_id\ttab)[:12]>` (plus `-<gen>` after a `not_creator`), title = tab name, in the run's folder (`dispatcher/factorio_dispatch.py:144-162`; `tests/e2e/test_e2e.py::test_other_tab_is_a_separate_chat`).
- On `not_creator` the tab gets a new chat (next generation, same title and folder) and the question is resent (`dispatcher/factorio_dispatch.py:472-483`; `tests/app_e2e/test_app_e2e.py::test_tab_from_an_earlier_install_still_answers`).
- Folders are looked up by name and parent before one is created; a second run with a taken name gets ` · <last 6 chars of run_id>` (`dispatcher/factorio_dispatch.py:102-141`; no test).
- The header is `[Factorio · run "R" · tab "T" · tick N · player P at (x, y) on S · reply in L]`, pointers one line each, then the question; the language comes from the letters of the question, never the run name (`tests/unit/test_dispatch.py::test_header_says_the_language_and_not_the_run_name`, `::test_reply_language_from_the_question_text`, `::test_pointers_go_as_lines_between_header_and_question`, `::test_no_pointers_no_extra_lines`). The agent rules parse this exact shape (`agent/factorio-companion.md:7`).
- The trace posts Kira's text and labels of `factorio_*`/MCP tool rows; `factorio_progress` is not traced; `factorio_reply` ends the watch (`tests/unit/test_dispatch.py::test_trace_lines_carry_kiras_text_and_tool_labels`).
- A turn that ends (`turn_stats`) without `factorio_reply` forwards its last non-empty text as `/reply` with `fallback: true` and the `ask_id`; rows dated before the question, and every row before the question's own user row (the previous turn still closing after a quick follow-up), are skipped; the transcript offset is taken before the question is sent (`tests/unit/test_dispatch.py::test_a_quick_follow_up_does_not_get_the_previous_turns_text`, `tests/unit/test_dispatch.py::test_forwards_the_last_text_when_the_turn_skipped_factorio_reply`, `::test_does_not_forward_when_factorio_reply_was_called`, `::test_forwards_earlier_text_when_the_last_message_is_empty`, `::test_an_earlier_turn_met_again_after_a_rewrite_is_not_forwarded`, `::test_keeps_watching_while_the_turn_runs`). The bridge decides whether the copy is shown ([Bridge](bridge.md)).
- A wake exits when its parent gateway is gone (`os.getppid()` changed) or when the installed script or the app config changed mtime (`dispatcher/factorio_dispatch.py:499-548`); `scripts/test-e2e.sh:31-53` waits for a wake that started after the installed file.
- `dispatch-state.json` is written whole through a temp file and `os.replace` (`dispatcher/factorio_dispatch.py:82-86`).
- Library requests are confirmed at once and answered on a thread, so they never hold up questions (`dispatcher/factorio_dispatch.py:559-564`); open/delete accept only slugs from the run's own list (`tests/unit/test_panel_tool.py::test_library_lists_opens_and_deletes_only_this_runs_panels`, `::test_dispatcher_and_mcp_agree_on_names`).
- Stdlib only, runs on the macOS system `python3` 3.9 (`README.md` "Layout", `docs/worktrees.md` "Requirements on the host").

## Easy to break

- Without the parent check, a gateway restart leaves two dispatchers sharing one queue (SPEC.md "How it works"). Without the source-stamp check, old code keeps serving for up to an hour, with a stale chat header for example (SPEC.md "How it works"). `scripts/kc.sh install` copies only on change, because any copy restarts the running wake (`scripts/kc.sh:47-48`).
- `not_creator` is recognised by that substring in the exception text (`dispatcher/factorio_dispatch.py:473`). If Kiro Crew changes the wording, every tab opened by an earlier cron job answers "Could not pass the question…" after 3 tries (inferred: the wording is not pinned by a Kiro Crew contract).
- Recreating the cron, reinstalling the app or disabling and enabling it gives a new job id, so every old chat refuses messages and is replaced by a fresh one without the agent's memory of the old one (SPEC.md "How it works" step 3).
- The private `_post` calls break on a Kiro Crew update; the tab then shows "ScriptContext._post is gone" or "/api/chat/... refused" after 3 tries (`dispatcher/factorio_dispatch.py:92-99`; SPEC.md "Open questions").
- Without `agent.session_control = true` the dispatcher gets `session_control_disabled` (SPEC.md "What to enable"), and each question ends as "Could not pass the question…" after 3 tries (`dispatcher/factorio_dispatch.py:486-493`). The app status page still shows the dispatcher green then, because it checks only that `/asks` is polled (`app/backend/status.py:168-178`).
- The trace and the fallback forward depend on Kiro Crew's transcript shape (`role`, `content`, `meta.turn_stats`, `meta.mcp_server`, `ts`). If it changes they stop silently; the tab then gets only "Kira did not answer here" after 90 s when the model skips `factorio_reply` (SPEC.md "Mod ↔ bridge protocol", "Long turns"; `dispatcher/factorio_dispatch.py:266-299`).
- `poll_wait` above 60 s: the bridge caps the wait at 60 s, and the app status page reports the dispatcher down after 60 s of silence (`bridge/factorio_bridge.py:548`, `app/backend/status.py:21`).
- A bridge error on `/asks/done` or on the failure `/reply` inside `handle` is not caught, so the wake ends with an exception; the ask comes back by lease to the next wake (`dispatcher/factorio_dispatch.py:492-495`, `:565`).
- A tab's chat is cached in `dispatch-state.json` and only replaced on `not_creator`; a chat removed in the dashboard probably fails every later question of that tab (inferred: what `send_to_session` does for a deleted slot is not checked).
- Renaming a run does not rename its folder (SPEC.md "Open questions").

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_dispatch.py tests/unit/test_panel_tool.py`: the header, pointers, language, trace mapping, fallback forward and library, ~5 s, no gateway.
- `./scripts/test-unit.sh tests/unit/test_e2e_watch.py tests/unit/test_app_status.py`: the e2e watchdog and the status page's dispatcher check (fake `/health`), ~5 s.
- `./scripts/test-e2e.sh`: the real dispatcher in a gateway, real model, ~4 min (`tests/e2e/test_e2e.py`, `tests/e2e/test_panels_e2e.py`). It installs the dispatcher, registers the cron if missing and waits for a fresh wake (`scripts/test-e2e.sh:23-53`).
- `./scripts/test-app-e2e.sh`: the dispatcher as the app's cron, ~20 min; covers `not_creator` (`::test_tab_from_an_earlier_install_still_answers`) and the live trace (`::test_long_build_shows_progress`) in `tests/app_e2e/test_app_e2e.py`.
- Syntax on the system Python: `/usr/bin/python3 -c "import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]" dispatcher/factorio_dispatch.py` (run, passes on 3.9.6).
- By hand on a stand: `./scripts/stand.sh kc install`, then `./scripts/stand.sh check`, which verifies the installed dispatcher is this checkout's and its cron polls your bridge (`scripts/stand.sh:176-180`, [worktrees.md](../worktrees.md) "Checking only your own stand"). Then ask in a tab and read the chat in the dashboard.

## Decisions

- When SPEC, README and the code disagree, the code wins: confirmed by Michael on 2026-10-29.
- Chat key is `run_id` + tab, one folder per run: the public `open_session` uses one string as key and title and keys are global, so two runs' "Logistics" would share a chat. SPEC.md "Decided" "Chat key", "How it works" step 2.
- Nested folders through the private `_post` until Kiro Crew [issue #25210](https://github.com/kirodotdev/KiroCrew/issues/25210) lands: the public `create_session_folder` has no parent. SPEC.md "How it works" step 1, "Open questions".
- One wake long-polls for 3500 s: Kiro Crew caps a script at 3600 s. SPEC.md "How it works".
- Confirm after `send_to_session`, not on pickup: a dispatcher dying halfway loses no question. SPEC.md "How it works".
- The trace reads Kiro Crew's transcript, accepting that a format change stops it: nothing else breaks. SPEC.md "Mod ↔ bridge protocol", "Long turns".
- The `gamer`-account setup is retired; players install the Kiro Crew app, whose cron runs this script: confirmed by Michael on 2026-10-29.

## Terms

- **wake**: one run of the cron script, up to `run_seconds` = 3500 s (`dispatcher/factorio_dispatch.py:37`).
- **slot**: a Kiro Crew chat key (`factorio-<hash>[-<gen>]`). Not the stand slot of [worktrees.md](../worktrees.md).
- **gen**: the chat generation of a tab, bumped on each `not_creator` (`dispatcher/factorio_dispatch.py:78`).
- **attempts**: failures per ask id, kept in the state file; 3 and the question is dropped (`dispatcher/factorio_dispatch.py:39`, `:77`).
- **fallback copy**: the turn's last text forwarded when the transcript shows no `factorio_reply` (`dispatcher/factorio_dispatch.py:317-334`).
- **trace**: the lines of a running turn shown under the question (SPEC.md "Long turns").
