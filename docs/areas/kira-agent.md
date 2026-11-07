# Kira, the agent

The Kiro Crew agent `factorio-companion` that answers questions from the game: an agent spec (tools, MCP server, short prompt) plus a rules file that is her behaviour. The rules are the product's code: a change to them changes what players get, and only the e2e suites test it.

## Main files

- `agent/factorio-companion.json`: the spec. `name`, `description`, a 755-character `prompt` (the header format, "end every game turn with `factorio_reply`", answer in the header's `reply in L`), `tools` and `allowedTools` = `["@factorio"]`, `resources` = `file://__ROOT__/agent/factorio-companion.md`, `includeMcpJson: false`, and `mcpServers.factorio` = `/usr/bin/python3 __ROOT__/mcp/factorio_mcp.py --bridge http://127.0.0.1:34198` (the file). No `model` field.
- `agent/factorio-companion.md`: the rules, by section: How a turn works (line 3: `tool_call` arguments, the header, pointers `@1`, `factorio_progress`), Answers (20), Building (27: `kc.place_ghosts` only, power in the same turn), The base: factorio_atlas (39), Done means it works (52: trial run before "done"), Calculations and layouts (60), The kc library (68), Skills (77), Resources (81: nearest cluster only), Changing existing things (89: consent first), Queries (96), Factorio 2.0 API notes (107), Live panels (116), Marks (131), Playbook: trains leave stations with leftover cargo (139). A short summary is in [SPEC.md, "Agent rules"](../../SPEC.md#agent-rules); the file is the authority.
- `mcp/factorio_mcp.py:43-253` (`TOOLS`): the tool descriptions, which Kira reads as part of her instructions too. Ten tools: `factorio_query`, `_mark`, `_unmark`, `_list_marks`, `_reply`, `_compute`, `_panel`, `_saved_panels`, `_atlas`, `_progress` (`grep -n '"name": "factorio_' mcp/factorio_mcp.py`).

## Talks to

- Uses: the MCP server `factorio` (`mcp/factorio_mcp.py`), started by kiro-cli per chat session from `mcpServers` (repo) or the app's `mcpServers` (`app/app.json:15-22`). Contract: [SPEC.md, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio).
- Uses: the mod's query helpers through `factorio_query` ([Build helpers](build-helpers.md), [Panels](panels.md), [Circuits](circuits.md)) and the Python skills through `factorio_compute` ([Skills](skills.md)).
- Used by: the dispatcher, which opens one chat per run and tab with `"agent": "factorio-companion"` (`dispatcher/factorio_dispatch.py:35`, `:155-158`), sets it to session mode `trust_reads` (`:160`) and writes the header line with `reply in L` from the letters of the question (`:173-193`).
- Used by: the app status page, which looks for `<home>/kiro/agents/factorycrew--factorio-companion.json` and fails "Kira's tools" when it has no `factorio` MCP server (`app/backend/status.py:103-112`, `:188-201`).

## How the agent is put in place

- Repo / stand: `scripts/kc.sh install` seds `__ROOT__` to the checkout and `127.0.0.1:34198` to `127.0.0.1:$FC_BRIDGE_HTTP`, writing `$KIRO_HOME/agents/factorio-companion.json` with `KIRO_HOME=$KIROCREW_HOME/kiro` (`scripts/kc.sh:19-21`, `:40-46`). In a worktree it runs as `scripts/stand.sh kc install` (`scripts/kc.sh:12-18`); `stand.sh up` installs before `kc start` (`scripts/stand.sh:191`), and `stand.sh check` verifies the file names this checkout and this bridge (`scripts/stand.sh:155-158`). Ports and homes: [docs/worktrees.md](../worktrees.md).
- App: `scripts/build-app.sh:25-42` writes `dist/factorycrew/agents/factorio-companion.json`: the prompt minus the sentence "Follow the rules in factorio-companion.md. ", with the whole rules file appended, `tools`/`allowedTools` = `["@factorycrew:factorio"]`, no `resources`, no `mcpServers`, no `model`. The gateway installs it as `factorycrew--factorio-companion.json` (`app/backend/status.py:105`, `tests/unit/test_app_status.py:39`). `scripts/app-dev.sh` rebuilds, reinstalls, trusts and enables it on a dev gateway.

## Must stay true

- The agent spec has hooks `userPromptSubmit`, `postToolUse`, `stop` running `app/hooks/turn_hook.py` (the turn contract, [Bridge](bridge.md#turn-contract)); `scripts/build-app.sh` writes the same three with the app's install path (`tests/unit/test_turn_contract.py::test_the_app_and_the_stand_agent_carry_the_same_three_hooks`). A turn from the game that ends without `factorio_reply` comes back to her as "[Hook continuation — automatic] … Call factorio_reply now".
- "Invalid input for tool_call — arguments: expected object, received string" is a malformed call, not broken tools: the rules tell her to call again with `arguments` as an object and still end with `factorio_reply` (`agent/factorio-companion.md`, "How a turn works"; seen 2026-11-01 under model `auto`).
- "Bug reports and ideas for the developers": `factorio_feedback` makes a draft that sends nothing, she shows it with what goes and asks; only after a yes does she send it and give the report id (`tests/e2e/test_reports_e2e.py::test_kira_drafts_a_bug_report_and_sends_it_only_after_yes`). See [Error and bug reports](../sentry.md).
- Every turn started by a game question ends with `factorio_reply(chat=<tab>)` (prompt in `agent/factorio-companion.json`; `agent/factorio-companion.md:7`). When it does not, the dispatcher forwards the turn's last text to the tab (`dispatcher/factorio_dispatch.py:317-333`; `tests/unit/test_dispatch.py::test_forwards_the_last_text_when_the_turn_skipped_factorio_reply`).
- The answer is in the language the header names, not the run name's (`tests/unit/test_dispatch.py::test_header_says_the_language_and_not_the_run_name`; `tests/e2e/test_e2e.py::test_answers_in_the_players_language`; `tests/app_e2e/test_app_e2e.py::test_answers_in_the_players_language`).
- Kira has only the `factorio` tools: `allowedTools` lists only that server and `includeMcpJson` is false (`agent/factorio-companion.json`; `tests/app_e2e/test_app_e2e.py::test_it_was_the_apps_agent` checks the app's turns call `@factorycrew:factorio/...`).
- The spec pins no model (decision below). No test checks it (`grep -rn '"model"' tests` finds nothing).
- Existing entities change only after the player's yes, and only the described change (`agent/factorio-companion.md:89-94`; `tests/app_e2e/test_app_e2e.py::test_changing_existing_entities_needs_consent`).
- Builds are tried on a hidden trial surface before "done" (`agent/factorio-companion.md:52-58`; `tests/app_e2e/test_uncoded.py` asserts `kc.trial_start` in the transcript).
- "Here" and resource requests mean the nearest cluster to the player (`agent/factorio-companion.md:9`, `:83`; `tests/app_e2e/test_app_e2e.py::test_oil_field_nearest_cluster`).
- The agent name stays `factorio-companion`: the dispatcher, `kc.sh`, `stand.sh check` and the status page all use it (sources above).
- The prompt keeps the exact sentence "Follow the rules in factorio-companion.md. ": `build-app.sh:36` removes it by string replace and silently does nothing if it changes.
- The bridge address stays the literal `127.0.0.1:34198` in the spec: `kc.sh` rewrites only that string (`scripts/kc.sh:44`). The MCP process gets its bridge from `--bridge`; the spec's `env` block does not reach it ([docs/worktrees.md, "Checking only your own stand"](../worktrees.md#checking-only-your-own-stand)).

## Easy to break

- A rules edit reaches a stand only after `stand.sh kc install`, and a gateway sees only the agents present when it starts (`scripts/stand.sh:191`, `scripts/test-e2e.sh:23`). The app needs a rebuild and reinstall (`scripts/app-dev.sh`).
- Kira cannot open files: her only tools are the `factorio` ones (`allowedTools`, `agent/factorio-companion.json`, `scripts/build-app.sh:37-38`). A rule that sends her to a doc ("format in docs/circuits-format.md", `agent/factorio-companion.md:74`) gives her a name, not its content; what she needs goes into the rules or a tool description.
- On Kiro Crew below 0.11 the app's agent gets no Factorio tools and Kira says she has none (`app/backend/status.py:120-123`, `:194-196`).
- Rule wording is not unit-tested: a reworded rule passes `check.sh` and fails only in the e2e suites (`grep -rn 'factorio-companion.md' tests` finds nothing).
- The e2e suites use a real model, so one red run of a behaviour test can be the model and not the change; rerun the single test with `-k` before concluding (inferred).

## How to check a change

- `./scripts/test-unit.sh tests/unit/test_dispatch.py tests/unit/test_app_status.py` (18 tests, ~2 s): header and language, the forward fallback, the status page's agent check. They do not run Kira.
- `./scripts/test-e2e.sh` (headless 2.1.24 in Docker, real model, ~4 min): `tests/e2e/test_e2e.py` (7 chests marked, follow-up from context, remove marks, a new tab is a separate chat, Russian answered in Russian) and `tests/e2e/test_panels_e2e.py` (science panel with a period filter, live logistics panel, restore a saved panel, the window's Library without Kira).
- `./scripts/test-app-e2e.sh [-k ...]` (real client, the built app, ~20 min; run it on the 2.0.77 client and with `FACTORIO_APP=/Users/Shared/factorio-2.1.app` on the 2.1.24 one): `tests/app_e2e/test_app_e2e.py` (marks, follow-up, trash button, language, an older tab, long-build progress, nearest oil cluster, consent, the app's own MCP server), `test_uncoded.py` (lamp that never lights, trains leaving with cargo, power lamps from the grid, a rate from real numbers, belt around a wall), `test_atlas_planted.py` (planted faults and their controls), `test_atlas.py` and `test_atlas_events.py` (need a player's save, `-k atlas`, `-k events`).
- By hand: read Kira's chat for the tab in the gateway's dashboard (`Factorio/<run>/<tab>`), and the transcript `<home>/sessions/dashboard_<slot>.jsonl` (`tests/app_e2e/test_app_e2e.py:356-362`).

## Decisions

- No `model` in the spec: left on auto, cheap for players. Confirmed by Michael, 2026-10-29.
- Kira changes existing entities only after the player's consent. Confirmed by Michael, 2026-10-29; `agent/factorio-companion.md`, "Changing existing things".
- Read-only `factorio_query` is an agent rule, not a sandbox: the query runs any Lua. Confirmed by Michael, 2026-10-29; [SPEC.md, "Agent tools"](../../SPEC.md#agent-tools-mcp-factorio).
- When SPEC, README and the code disagree, the code wins. Confirmed by Michael, 2026-10-29.
- Players get Kira through the Kiro Crew app; the `gamer`-account setup is retired. Confirmed by Michael, 2026-10-29.
- The agent lives in a separate gateway home, never in the shared `~/.kiro/agents/`. [SPEC.md, "What runs where"](../../SPEC.md#what-runs-where), "What to enable".

## Terms

- **Header**: the first line of a game question, `[Factorio V · run "R" · tab "T" · tick N · player P at (x, y) on S · reply in L]`, `V` the game version (`agent/factorio-companion.md:7`). The rules list what 2.1 removed from the 2.0 API under "Factorio API notes (2.0 and 2.1)" (`agent/factorio-companion.md:114`).
- **Tab / chat**: one in-game tab is one Kiro Crew chat `Factorio/<run>/<tab>`; `chat` in every tool is the tab name.
- **Pointer**: `@1`, `@2` lines between header and question, a click or a box the player made (`agent/factorio-companion.md:11`).
- **Rules**: `agent/factorio-companion.md`; the repo spec loads them as a `file://` resource and the app folds them into the prompt (`scripts/build-app.sh:36`).
