#!/usr/bin/env bash
# Level 3, automatic e2e: game -> bridge -> dispatcher -> Kiro Crew chat -> agent -> game.
#
# Uses the separate factorycrew gateway (scripts/kc.sh, port 5477) and a real model, so a run
# costs a few agent turns (~1-2 credits). A fresh headless 2.1 stand per run = a new run_id,
# so every run gets its own folder Factorio/<e2e MM-DD HH:MM> and empty chats.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="factorycrew-test:2.1.23"
NAME="fc-e2e"

"$ROOT/scripts/kc.sh" status >/dev/null || "$ROOT/scripts/kc.sh" start
"$ROOT/scripts/kc.sh" install
"$ROOT/scripts/kc.sh" config set agent.session_control true >/dev/null
if ! "$ROOT/scripts/kc.sh" cron list 2>/dev/null | grep -q factorio-dispatch; then
  "$ROOT/scripts/kc.sh" cron add factorio-dispatch '{}' --every 30 \
    --script "${FC_KC_HOME:-$HOME/.kirocrew-factorio}/crons/factorio_dispatch.py:run" \
    --timeout 3600 --timeout-secs 3610 --no-persistent-session --minimal-context --hide-in-chat
fi

docker image inspect "$IMAGE" >/dev/null 2>&1 || \
  docker build --platform linux/amd64 --build-arg FACTORIO_VERSION=2.1.23 -t "$IMAGE" "$ROOT/docker"
docker rm -f "$NAME" >/dev/null 2>&1 || true
# The stand's bridge takes the host bridge's port 34198 (the dispatcher and the agent's MCP
# server both dial it). Park the host bridge for the run and bring it back afterwards.
HOST_BRIDGE=0
if launchctl print "gui/$(id -u)/dev.factorycrew.bridge" >/dev/null 2>&1; then
  HOST_BRIDGE=1
  "$ROOT/scripts/services.sh" stop bridge
fi
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  [ "$HOST_BRIDGE" = 1 ] && "$ROOT/scripts/services.sh" start bridge || true
}
trap cleanup EXIT
# Published to host loopback only: the bridge API has no auth.
docker run -d --name "$NAME" --platform linux/amd64 \
  -p 127.0.0.1:34198:34198 -p 127.0.0.1:27015:27015 \
  -e FC_INT_DATA=/work/.test-data/e2e -v "$ROOT:/work" "$IMAGE" \
  python3 -u /work/tests/e2e/stand.py >/dev/null
for _ in $(seq 1 120); do docker logs "$NAME" 2>&1 | grep -q STAND_READY && break; sleep 1; done
docker logs "$NAME" 2>&1 | grep -q STAND_READY || { docker logs "$NAME" | tail -20; exit 1; }

cd "$ROOT"
uv run --no-project --with pytest==8.3.5 python -m pytest -p no:cacheprovider -s -q tests/e2e "$@"
