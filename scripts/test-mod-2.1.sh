#!/usr/bin/env bash
# Level 1 on Factorio 2.1.23: same as scripts/test-mod.sh, with the mod copied and its
# factorio_version set to 2.1 inside the container (the repo copy stays at 2.0).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="factorycrew-test:2.1.23"
docker run --rm --platform linux/amd64 -v "$ROOT:/work" "$IMAGE" bash -c '
  set -e
  DATA=/work/.test-data/level1-2.1
  mkdir -p "$DATA/mods"
  cp -r /opt/factorio-test-mods/. "$DATA/mods/"
  rm -rf /tmp/kc-companion && cp -r /work/mod/kc-companion /tmp/kc-companion
  sed -i -E "s/\"factorio_version\": *\"2.0\"/\"factorio_version\": \"2.1\"/" /tmp/kc-companion/info.json "$DATA"/mods/factorio-test_*/info.json
  # FactorioTest 3.0.2 targets 2.0: on 2.1 the profiler lives in `helpers`
  sed -i "s/game\.create_profiler/helpers.create_profiler/g" "$DATA"/mods/factorio-test_*/_factorio-test.lua
  grep -h factorio_version /tmp/kc-companion/info.json "$DATA"/mods/factorio-test_*/info.json
  factorio-test run \
    --mod-path /tmp/kc-companion \
    --factorio-path /opt/factorio/bin/x64/factorio \
    --data-directory "$DATA" "$@"
' bash "$@"
