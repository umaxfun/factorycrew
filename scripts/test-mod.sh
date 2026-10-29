#!/usr/bin/env bash
# Level 1: mod logic under FactorioTest, headless Factorio 2.0.77 in Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="factorycrew-test:2.0.77"
. "$ROOT/scripts/lib/docker.sh"
docker_alive

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build --platform linux/amd64 -t "$IMAGE" "$ROOT/docker"
fi

docker_ok "$IMAGE"
# a whole level 1 run takes ~10 s; FC_L1_TIMEOUT bounds a run that hangs inside the container
docker_bounded "fc-l1-$(basename "$ROOT")-$$" "${FC_L1_TIMEOUT:-300}" --platform linux/amd64 -v "$ROOT:/work" "$IMAGE" bash -c '
  set -e
  DATA=/work/.test-data/level1
  mkdir -p "$DATA/mods"
  cp -r /opt/factorio-test-mods/. "$DATA/mods/"
  factorio-test run \
    --mod-path /work/mod/kc-companion \
    --factorio-path /opt/factorio/bin/x64/factorio \
    --data-directory "$DATA" "$@"
' bash "$@"
