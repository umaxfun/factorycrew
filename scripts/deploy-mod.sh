#!/usr/bin/env bash
# Copy the mod to /Users/Shared/kc-companion, where the `gamer` account's mods dir links to it.
# After a deploy, reload the save in the game (or restart Factorio if data.lua changed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${FC_MOD_DEST:-/Users/Shared/kc-companion}"
mkdir -p "$DEST"
rsync -a --delete --exclude tests "$ROOT/mod/kc-companion/" "$DEST/"
chmod -R a+rX "$DEST"
echo "deployed $(python3 -c "import json;print(json.load(open('$DEST/info.json'))['version'])") -> $DEST"
