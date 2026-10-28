# factorycrew

Ask Kiro Crew about the current Factorio run from an in-game window. Design: [SPEC.md](SPEC.md).

## Layout

| Path | What |
|---|---|
| `mod/kc-companion/` | Factorio 2.0 mod: in-game window, query execution, map marks, UDP to the bridge |
| `docker/Dockerfile` | Test image: headless Factorio 2.0.77 + FactorioTest (built from source, no portal login) |
| `scripts/test-mod.sh` | Level 1 tests (mod logic under FactorioTest) |

Bridge, MCP server and dispatcher cron are not written yet.

## Tests

Level 1, mod logic, headless Factorio in Docker (`linux/amd64`, runs under Rosetta on Apple Silicon):

```bash
./scripts/test-mod.sh
```

The first run builds the `factorycrew-test:2.0.77` image (~1 min). After that a run takes ~10 s. Extra arguments go to `factorio-test run`.

## Installing the mod for the player (account `gamer`)

The mod lives where both accounts can reach it:

```bash
# as umaxfun, once
mkdir -p /Users/Shared/kc-companion && rsync -a --delete mod/kc-companion/ /Users/Shared/kc-companion/
# as gamer, once
ln -s /Users/Shared/kc-companion "$HOME/Library/Application Support/factorio/mods/kc-companion"
```

Steam launch options under `gamer`: `--enable-lua-udp 34200`.
