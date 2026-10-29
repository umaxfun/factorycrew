#!/usr/bin/env bash
# Copy the mod to /Users/Shared/kc-companion, where the `gamer` account's mods dir links to it.
# After a deploy, reload the save in the game (or restart Factorio if data.lua changed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The player's install: never from a linked worktree (its code is not released yet).
# FC_ALLOW_DEFAULT=1 overrides.
if [ -z "${FC_ALLOW_DEFAULT:-}" ] && \
   [ "$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)" != "$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null)" ]; then
  echo "$(basename "$0"): this is a worktree; the player's install is done from the main checkout" >&2
  exit 2
fi
DEST="${FC_MOD_DEST:-/Users/Shared/kc-companion}"
mkdir -p "$DEST"
rsync -a --delete --exclude tests "$ROOT/mod/kc-companion/" "$DEST/"
chmod -R a+rX "$DEST"
echo "deployed $(python3 -c "import json;print(json.load(open('$DEST/info.json'))['version'])") -> $DEST"
