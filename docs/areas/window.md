# In-game window and pointers

Kira's window in the game (run name, tabs, feed, status, marks row, panels row, input) and the pointer tool that lets the player show Kira places as `@1`, `@2`. What the player sees is specified in [SPEC.md, "In-game window"](../../SPEC.md#in-game-window) and ["Pointers"](../../SPEC.md#pointers); this doc covers how the code builds it and how to check it.

## Main files

- `mod/kc-companion/scripts/gui.lua`: the top-bar button, the window `kc_main` in `player.gui.screen`, one `refresh_*` per row, click and confirm handlers, the library frame `kc_library` in `player.gui.left`, and `open`/`close`/`click`/`confirm` for scripted driving (`mod/kc-companion/scripts/gui.lua:10`, `:63`, `:394-404`, `:321`, `:567-600`)
- `mod/kc-companion/scripts/pointers.lua`: making a pointer from a selection (click or box), its ground drawing, its wire form, `take` for the next question, the tool in the hand (`mod/kc-companion/scripts/pointers.lua:154`, `:111-138`, `:220`, `:227-238`)
- `mod/kc-companion/data.lua`: prototypes: hotkeys `kc-toggle` (Ctrl+Shift+K), `kc-pointer` (Alt+K), `kc-clear-cursor` (linked to the game's clear-cursor), the `kc-pointer` selection tool and shortcut, the `kc-kira` sprite (`mod/kc-companion/data.lua`)
- `mod/kc-companion/graphics/`: `kira.png` and `pointer.png`, both 64×64, drawn by `tools/draw_kira.py` and `tools/draw_pointer.py` (`file mod/kc-companion/graphics/*`; docstrings of both scripts)
- `mod/kc-companion/locale/en/kc.cfg`: names for the `kc-toggle` control and the mod only
- `tools/ui_dev.py`: the UI loop, a real 2.0.77 client driven over a dev bridge, with screenshots (docstring, `tools/ui_dev.py:1-22`)

## Talks to

- Uses: [Mod core](mod-core.md), through `protocol.tab`, `protocol.ask`, `protocol.chat_marks`, `protocol.unmark`, `protocol.close_tab`, `protocol.connected`, `protocol.udp_state`, `protocol.library_request`, `protocol.stop_request` (`mod/kc-companion/scripts/gui.lua`)
- Used by: Mod core's `control.lua`, which routes `on_gui_click`, `on_gui_confirmed`, `kc-toggle`, `on_player_selected_area`, `on_player_alt_selected_area` and `kc-clear-cursor` here, and refreshes the window after bridge messages (`mod/kc-companion/control.lua:83-151`)
- Uses: [Live panels](panels.md) (`mod/kc-companion/scripts/panels.lua`) for the Panels row and the library list; `panels.on_click` sees every click before `gui.on_click` (`mod/kc-companion/scripts/gui.lua:292-375`; `mod/kc-companion/control.lua:138-143`)
- Sends: the pointers' wire form inside `ask.pointers`, rendered into the chat header by the dispatcher ([SPEC.md, "Pointers"](../../SPEC.md#pointers), ["The message the dispatcher puts in the chat"](../../SPEC.md#the-message-the-dispatcher-puts-in-the-chat))
- Used by: `tools/ui_dev.py` and the app e2e, through `remote.call("kc", "ui_open" | "ui_click" | "ui_confirm" | "ui_inject" | "point")` (`tools/ui_dev.py:232-263`; `tests/app_e2e/test_app_e2e.py:83-94`; `mod/kc-companion/control.lua:169-177`)

## Must stay true

- Under a turn that went wrong (stopped, `no_reply`, an answer starting "Kiro Crew could not finish" or "Kiro has no credits") the feed shows "Report this to the developer", which opens the same dialog (`mod/kc-companion/tests/test-gui.lua::offers the report right under a turn that went wrong`).
- "Report a problem" in the run row opens the bug report dialog `kc_bug` (comment, screenshot checkbox, what goes, Send); it keeps typed text across refreshes, shows the report id or the local copy's path from `bug_report_result`, and gives up after a minute with no answer. A note at the top says once that error reports are on (`mod/kc-companion/tests/test-gui.lua::sends a bug report from the window and shows its id`, `::says once that error reports are on, until OK`, `::gives up on a bug report with no answer in a minute`). See [Error and bug reports](../sentry.md).
- The top-bar button is a `sprite-button` named `kc_toggle` with the `kc-kira` sprite (`mod/kc-companion/tests/test-gui.lua::opens with a default tab, top-bar button and no-connection indicator`)
- A window opened with no tabs creates the `General` tab (`mod/kc-companion/scripts/gui.lua:29`; same test)
- The link indicator reads "Kira is offline" or "UDP is off" before the first `pong` and "online" after (same test)
- Enter or Send sends the trimmed text as an `ask` into the selected tab, with the player's position, and empties the field (`mod/kc-companion/tests/test-gui.lua::sends the input as an ask into the selected tab and shows it`)
- "thinking… N s · step K" and the activity show while a turn runs and clear on `reply`; the trace folds into "(worked: K steps, N s)" (`mod/kc-companion/tests/test-gui.lua::shows elapsed time, steps and activity while a long turn works`, `::shows the running turn step by step, then folds it into one line`)
- After 60 s without a new step the status line adds "· no new step for N s" and the turn stays on (`mod/kc-companion/tests/test-gui.lua::says when a running turn has gone quiet, and keeps it running`)
- While a turn runs the status row has a Stop button; a click sends `stop` and shows "stopping…"; `idle` with note `stopped` folds the trace into "(stopped after K steps, N s)" and removes the button; `stop_failed` keeps the turn (`mod/kc-companion/tests/test-gui.lua::stops a running turn from the window`)
- Tabs sit in a 4-column grid of fixed-width buttons; a name over 9 characters is cut with "…" by UTF-8 character, with the full name in the tooltip (`mod/kc-companion/tests/test-gui.lua::wraps many tabs into a grid of fixed-width buttons and cuts long names`)
- Closing a tab keeps its feed; reopening the name brings it back (`mod/kc-companion/tests/test-gui.lua::adds, switches and closes tabs`)
- ◀ ▶ wrap around the tab's marks, and the trash button removes only this tab's marks (`mod/kc-companion/tests/test-gui.lua::counts, walks and clears the tab's marks`)
- An `[OPTIONS: …]` line of Kira's last answer becomes buttons that send the choice and disappear after it (`mod/kc-companion/tests/test-panels.lua::turns a trailing [OPTIONS: a | b] of an answer into buttons that send the choice`)
- A click on a machine is that machine with its recipe and status, plus the ore under it; a click on ore is the ore with its amount; a click on bare ground gives its tile (`mod/kc-companion/tests/test-pointers.lua`, the three "a click on …" tests)
- A drag is a box with entity counts, recipes and ore (`mod/kc-companion/tests/test-pointers.lua::a drag is a box with counts, recipes and ore`)
- Pointers number from 1 up to 9 per player; the next question takes them all and removes their drawings, and render ids never go on the wire (`mod/kc-companion/tests/test-pointers.lua::numbers pointers, caps them, and the next question takes and clears them`)
- A new pointer opens the window and appends "@n " to the input (`mod/kc-companion/tests/test-pointers.lua::the window lists pointers, puts @n into the question and sends them with it`)
- Pointer drawings are visible to the pointing player only (`mod/kc-companion/scripts/pointers.lua:113`)
- After an app update, while the bridge's `pong` carries an `app_version` other than the loaded mod's, the status line says "Kira updated to X: restart Factorio to load the new mod"; a pong without it clears the line (`mod/kc-companion/tests/test-gui.lua` "opens with a default tab...", `test-protocol.lua` "reports its version...").

## Easy to break

- `gui.on_click` ignores any element whose name does not start with `kc_` (`mod/kc-companion/scripts/gui.lua:467`)
- The status row is the exception to `clear()`: `gui.tick` updates it every second, so its label is updated in place and the Stop button is added or destroyed only when the turn starts or ends. A button rebuilt every second loses a click that lands between press and release (inferred: Factorio raises the click on release, on the element that was pressed) (`mod/kc-companion/scripts/gui.lua`, `refresh_status`)
- `refresh` rebuilds each other row with `clear()`, and the feed scrolls to the bottom on every refresh (`mod/kc-companion/scripts/gui.lua:272`); `gui.tick` re-renders the feed every second while the last question is unacknowledged (`mod/kc-companion/scripts/gui.lua:412-422`)
- A row added to `build` needs a nil guard in its `refresh_*`, as `refresh_panels` has (`mod/kc-companion/scripts/gui.lua:294`). A window left open in a save keeps its old element tree, so `find` returns nil there (inferred: GUI elements are saved with the game)
- Text cut with `string.sub` splits Cyrillic; `utf8_head` exists because Factorio's Lua has no `utf8` library (`mod/kc-companion/scripts/gui.lua:146-155`; the "Логистика…" case in `test-gui.lua`)
- The trash button unmarks locally and sends nothing to the bridge; Kira sees the change only through `factorio_list_marks` (`mod/kc-companion/scripts/gui.lua:516-519`)
- ◀ ▶ call `set_controller` under `pcall`, so a failure is silent (`mod/kc-companion/scripts/gui.lua:459`)
- Window text is English literals in `gui.lua`, not locale keys. `kc.cfg` has no names for the `kc-pointer` and `kc-clear-cursor` controls or the `kc-pointer` shortcut (`mod/kc-companion/locale/en/kc.cfg`); the game then shows its unknown-key placeholder in Controls and the shortcut bar (inferred)
- If headless FactorioTest has no LuaPlayer, every window and pointer test returns early. Only "has a player to build the window for" and "has a player to point with" fail then (`mod/kc-companion/tests/test-gui.lua:63-65`, `mod/kc-companion/tests/test-pointers.lua:50-52`). The tool-in-hand test skips without a character (`mod/kc-companion/tests/test-pointers.lua:136-138`)
- A sprite size change needs the matching `size`/`icon_size` in `data.lua` (`mod/kc-companion/data.lua:14`, `:39-41`, `:66-67`); graphics and prototypes reload only on a full game restart, `control.lua` on a save reload ([SPEC.md, "What runs where"](../../SPEC.md#what-runs-where))
- In a linked worktree `tools/ui_dev.py` refuses to drive the shared client; use `./scripts/stand.sh ui …` ([docs/worktrees.md](../worktrees.md); `tools/ui_dev.py:285-288`)

## How to check a change

- `./scripts/test-mod.sh` and `./scripts/test-mod-2.1.sh`: level 1, ~11 s each. This area: `mod/kc-companion/tests/test-gui.lua`, `mod/kc-companion/tests/test-pointers.lua`; the options buttons and library frame are in `mod/kc-companion/tests/test-panels.lua`
- `./scripts/test-app-e2e.sh`: the real client and the window's own handlers, ~20 min (`tests/app_e2e/test_app_e2e.py`: tabs, input, trash button, long-build progress)
- How it looks: `tools/ui_dev.py start`, then e.g. `tools/ui_dev.py shot window open 'reply:General=Hello' 'status:General=thinking'`; the printed PNG path is under `~/.factorycrew-dev/factorio/script-output/kc/` (`tools/ui_dev.py:1-22`, `:266-281`). In a worktree: `./scripts/stand.sh ui start` and `./scripts/stand.sh ui shot …`. `restart` after a mod change. The screenshot needs a client window that renders (`tools/ui_dev.py:281`)
- Pointers by hand or scripted: `tools/ui_dev.py shot ptr open 'lua:return remote.call("kc", "point", {10, 10})'` makes a click pointer (`mod/kc-companion/control.lua:172-177`)
- No Python unit test covers this area

## Decisions

- Tabs are a fixed-width 4-column grid, 3 rows shown, the rest scroll, so any number of tabs stays inside the window: [SPEC.md, "In-game window"](../../SPEC.md#in-game-window)
- Mark navigation uses the remote view (`set_controller{type=remote}`): 2.0 has no `zoom_to_world`. [SPEC.md, "In-game window"](../../SPEC.md#in-game-window)
- The mod reports only where a pointer is and what is under it; what it means is Kira's job. [SPEC.md, "Pointers"](../../SPEC.md#pointers)
- The window does not pause the game. [SPEC.md, "Mod ↔ bridge protocol"](../../SPEC.md#mod--bridge-protocol)
- How the window looks is checked with a real client, because headless renders nothing: [SPEC.md, "Decided", "Screenshots for UI work"](../../SPEC.md#decided)
- The code wins when SPEC, README and code disagree: confirmed by the repository owner, [decisions.md](../decisions.md#when-spec-readme-and-the-code-disagree-the-code-wins-and-the-docs-are-fixed)

## Terms

- **view state**: per-player window state in `storage.kc.ui[player_index]`: selected tab, mark cursor, `adding`, `renaming`, `library`, `confirm_delete`
- **pointer**: a numbered selection (`@n`) the player made with the `kc-pointer` tool; `kind = point | box`
- **wire form**: a pointer without its render ids, as sent in `ask.pointers`
- **UI loop**: `tools/ui_dev.py`, a real client with its own data dir and dev bridge ports 34298-34300
- **stand**: one worktree's whole stack on its own ports ([docs/worktrees.md](../worktrees.md))
