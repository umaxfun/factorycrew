---
name: feedback
description: Send the app's developers a bug report or a feature idea from the player, with the player's yes first. Use for "this is a bug", "report it", "tell the developers", "it would be nice if…".
---

# Bug reports and ideas for the developers

A text skill: no code, one tool, `factorio_feedback` (docs/sentry.md, T4). Kira's rules carry the same steps (`agent/factorio-companion.md`, "Bug reports and ideas for the developers"), because she reads no files.

## Steps

1. Only for this app (the window, panels, buttons, Kira herself), not for the player's factory.
2. `factorio_feedback(kind="bug"|"feature", text=..., chat=T)`: sends nothing, returns `draft` and `goes`. The text: what happened or what is wanted, the steps, what was expected, what was found (an error message, a panel's `error`), in the player's language.
3. `factorio_reply` with the text and `goes`, asking whether to send, ending with `[OPTIONS: <send it> | <don't send>]`. Nothing else in this turn.
4. After a yes: `factorio_feedback(send=<draft>)`; tell the player the `report_id` (`sent: false`: saved, goes when the network is back). After a no: nothing. A changed text: back to step 2.

## What goes

The text, the versions of the app, the mod and the game, the bridge's state and recent log, and the tab's chat transcript. Kept on disk as `<KIROCREW_HOME>/factorio/bug-reports/<report_id>.zip` too.
