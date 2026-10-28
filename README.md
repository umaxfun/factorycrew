# factorycrew

Ask Kiro Crew about the current Factorio run from an in-game window. Design: [SPEC.md](SPEC.md).

## Layout

| Path | What |
|---|---|
| `mod/kc-companion/` | Factorio 2.0 mod: query execution, map marks, tabs, UDP to the bridge (in-game window: not yet) |
| `bridge/factorio_bridge.py` | Bridge: UDP to the game, HTTP API on 127.0.0.1:34198 for the dispatcher and the MCP server |
| `mcp/factorio_mcp.py` | MCP server `factorio` (stdio): `factorio_query`, `_mark`, `_unmark`, `_list_marks`, `_reply` |
| `docker/Dockerfile` | Test image: headless Factorio + FactorioTest (built from source, no portal login) + Python |
| `tests/integration/` | Level 2: mod + bridge + MCP |

Dispatcher cron and agent template are not written yet. All Python is stdlib only.

## Running the bridge

```bash
python3 bridge/factorio_bridge.py        # udp :34199, game at :34200, http 127.0.0.1:34198
```

The HTTP API has no authentication: whoever reaches the port can run Lua in the game. It binds to 127.0.0.1 only.

## Tests

Three levels (SPEC.md, "Тесты"). Only level 3 is end to end.

| Level | What | Command | Factorio |
|---|---|---|---|
| 1 | mod logic under FactorioTest | `./scripts/test-mod.sh` | headless 2.0.77 |
| 2 | integration: mod + bridge + MCP, test plays dispatcher and agent | `./scripts/test-integration.sh` | headless 2.1.23 |
| 3 | e2e: game → bridge → dispatcher → Kiro Crew chat → agent → game | not yet | headless 2.1 + live game 2.0.77 |

Level 2 is on 2.1 because a 2.0 headless server aborts on the first UDP packet the mod receives ([forum report](https://forums.factorio.com/viewtopic.php?t=134790), fixed in 2.1). Each image is built on first use (~1 min); runs take ~10 s and ~20 s. Logs of the last level 2 run: `.test-data/integration/`.

## Installing the mod for the player (account `gamer`)

The mod lives where both accounts can reach it:

```bash
# as umaxfun, once
mkdir -p /Users/Shared/kc-companion && rsync -a --delete mod/kc-companion/ /Users/Shared/kc-companion/
# as gamer, once
ln -s /Users/Shared/kc-companion "$HOME/Library/Application Support/factorio/mods/kc-companion"
```

Steam launch options under `gamer`: `--enable-lua-udp 34200`.
