#!/usr/bin/env bash
# The atlas fixture: a player's save with its mods, loaded in headless Factorio (Docker) with THIS
# checkout's mod in place of the copy in the mods dir. See docs/atlas.md, "Fixture".
#
#   FC_ATLAS_SAVE=<save.zip> FC_ATLAS_MODS=<mods dir with mod-list.json> scripts/atlas-fixture.sh dump
#       writes the structure dump to $FC_ATLAS_OUT (default .test-data/atlas/dump.txt) and prints counts
#   scripts/atlas-fixture.sh up | lua '<body>' | down      keep the game running between calls
#
# The game runs 2.0.77 (the version of the reported save); the save is never modified.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/docker.sh"
VERSION="${FC_ATLAS_VERSION:-2.0.77}"
IMAGE="factorycrew-test:$VERSION"
WORK="$ROOT/.test-data/atlas"
NAME="fc-atlas-$(basename "$ROOT")"
PORT="${FC_ATLAS_RCON:-27116}"
PASS="atlas-fixture"
OUT="${FC_ATLAS_OUT:-$WORK/dump.txt}"
RCON=(python3 "$ROOT/scripts/lib/rcon.py" "$PORT" "$PASS")

need() { [ -n "${!1:-}" ] || { echo "$1 is not set (see docs/atlas.md, Fixture)" >&2; exit 2; }; }

prepare() {
  need FC_ATLAS_SAVE; need FC_ATLAS_MODS
  rm -rf "$WORK/data/mods" && mkdir -p "$WORK/data/mods" "$WORK/data/saves" "$WORK/data/config"
  # clones on APFS (instant, no extra space), plain copies elsewhere
  for f in "$FC_ATLAS_MODS"/*.zip "$FC_ATLAS_MODS"/mod-list.json "$FC_ATLAS_MODS"/mod-settings.dat; do
    [ -e "$f" ] && { cp -c "$f" "$WORK/data/mods/" 2>/dev/null || cp "$f" "$WORK/data/mods/"; }
  done
  cp -R "$ROOT/mod/kc-companion" "$WORK/data/mods/kc-companion"
  python3 - "$WORK/data/mods/mod-list.json" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
mods = [m for m in d["mods"] if m["name"] != "kc-companion"] + [{"name": "kc-companion", "enabled": True}]
d["mods"] = mods
json.dump(d, open(p, "w"), indent=1)
EOF
  cp -c "$FC_ATLAS_SAVE" "$WORK/data/saves/fixture.zip" 2>/dev/null || cp "$FC_ATLAS_SAVE" "$WORK/data/saves/fixture.zip"
  printf '[path]\nread-data=/opt/factorio/data\nwrite-data=/data\n' > "$WORK/data/config/config.ini"
}

up() {
  docker_alive
  timeout -k 2 30 docker rm -f "$NAME" >/dev/null 2>&1 || true
  prepare
  timeout -k 2 60 docker run -d --name "$NAME" --platform linux/amd64 -p "127.0.0.1:$PORT:27015/tcp" \
    -v "$WORK/data:/data" "$IMAGE" factorio -c /data/config/config.ini --mod-directory /data/mods \
    --start-server /data/saves/fixture.zip --rcon-port 27015 --rcon-password "$PASS" >/dev/null
  "${RCON[@]}" --wait 180 || { timeout -k 2 10 docker logs --tail 30 "$NAME" >&2; exit 1; }
}

down() { timeout -k 2 30 docker rm -f "$NAME" >/dev/null 2>&1 || true; }

case "${1:-}" in
  up) up ;;
  down) down ;;
  lua) "${RCON[@]}" "$2" ;;
  dump)
    running=$(timeout -k 2 10 docker ps -q --filter "name=^$NAME$" || true)
    [ -n "$running" ] || up
    "${RCON[@]}" 'rcon.print(helpers.table_to_json(remote.call("kc", "atlas_dump", "nauvis", nil, "atlas/dump.txt")))'
    mkdir -p "$(dirname "$OUT")"
    cp "$WORK/data/script-output/atlas/dump.txt" "$OUT"
    echo "dump -> $OUT"
    [ -n "$running" ] || down
    ;;
  *) sed -n 2,9p "$0"; exit 2 ;;
esac
