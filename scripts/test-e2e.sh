#!/usr/bin/env bash
# Level 3, automatic e2e: game -> bridge -> dispatcher -> Kiro Crew chat -> agent -> game.
#
# Isolated per worktree (docs/worktrees.md): the slot of this checkout (scripts/stand.sh claim)
# gives the run its own Kiro Crew gateway (FC_E2E_KC_PORT, home FC_E2E_KC_HOME) and its own
# Docker stand (headless 2.1 + bridge) published on FC_E2E_HTTP / FC_E2E_RCON. Nothing outside
# the slot is touched, so several worktrees can run it at once. A real model: a run costs a
# few agent turns (~1-2 credits). A fresh stand per run = a new run_id, so every run gets its
# own folder Factorio/<e2e MM-DD HH:MM> and empty chats.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="factorycrew-test:2.1.23"

eval "$("$ROOT/scripts/stand.sh" claim)"
export FC_KC_HOME="$FC_E2E_KC_HOME" FC_KC_PORT="$FC_E2E_KC_PORT" FC_BRIDGE_HTTP="$FC_E2E_HTTP"
BRIDGE="http://127.0.0.1:$FC_E2E_HTTP"
K="$ROOT/scripts/kc.sh"

"$K" install # before start: a gateway sees only the agents present when it starts
"$K" status >/dev/null 2>&1 || "$K" start
"$K" config set agent.session_control true >/dev/null
if ! "$K" cron list 2>/dev/null | grep -q factorio-dispatch; then
  "$K" cron add factorio-dispatch "{\"bridge\": \"$BRIDGE\"}" --every 30 \
    --script "$FC_KC_HOME/crons/factorio_dispatch.py:run" \
    --timeout 3600 --timeout-secs 3610 --no-persistent-session --minimal-context --hide-in-chat >/dev/null
fi
# A wake compiles the dispatcher once and lives up to an hour. Wait until the wake serving
# this run started after the installed script (an older wake exits within ~25 s when it sees
# the new file); start one with `cron trigger` when none is running.
JOB="$("$K" cron list 2>/dev/null | awk '/factorio-dispatch/ {print $2; exit}')"
fresh_wake() {
  python3 - "$FC_KC_HOME" <<'EOF'
import glob, json, os, sys
home = sys.argv[1]
src = os.stat(f"{home}/crons/factorio_dispatch.py").st_mtime
for p in glob.glob(f"{home}/cron-running/*.json"):
    m = json.load(open(p))
    if m.get("name") == "factorio-dispatch":
        sys.exit(0 if m["started_at"] >= src else 2)
sys.exit(1)  # no wake running
EOF
}
for i in $(seq 1 90); do
  rc=0; fresh_wake || rc=$?
  [ "$rc" = 0 ] && break
  if [ "$rc" = 1 ] && [ $((i % 15)) = 1 ]; then "$K" cron trigger "$JOB" >/dev/null 2>&1 || true; fi
  sleep 1
done
fresh_wake || { echo "no dispatcher wake with the installed script after 90 s" >&2; exit 1; }

docker image inspect "$IMAGE" >/dev/null 2>&1 || \
  docker build --platform linux/amd64 --build-arg FACTORIO_VERSION=2.1.23 -t "$IMAGE" "$ROOT/docker"
docker rm -f "$FC_E2E_CONTAINER" >/dev/null 2>&1 || true
cleanup() {
  docker rm -f "$FC_E2E_CONTAINER" >/dev/null 2>&1 || true
  [ -n "${FC_E2E_KEEP_GATEWAY:-}" ] || "$K" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
# Inside the container the bridge and RCON keep their usual ports; the host side is the slot's.
# Published to host loopback only: the bridge API has no auth.
docker run -d --name "$FC_E2E_CONTAINER" --platform linux/amd64 \
  -p "127.0.0.1:$FC_E2E_HTTP:34198" -p "127.0.0.1:$FC_E2E_RCON:27015" \
  -e FC_INT_DATA=/work/.test-data/e2e -v "$ROOT:/work" "$IMAGE" \
  python3 -u /work/tests/e2e/stand.py >/dev/null
for _ in $(seq 1 120); do docker logs "$FC_E2E_CONTAINER" 2>&1 | grep -q STAND_READY && break; sleep 1; done
docker logs "$FC_E2E_CONTAINER" 2>&1 | grep -q STAND_READY || { docker logs "$FC_E2E_CONTAINER" | tail -20; exit 1; }

cd "$ROOT"
FC_E2E_BRIDGE="$BRIDGE" KIROCREW_HOME="$FC_KC_HOME" \
  uv run --no-project --with pytest==8.3.5 python -m pytest -p no:cacheprovider -s -q tests/e2e "$@"
