#!/usr/bin/env bash
# A whole factorycrew stack per worktree, so parallel sessions never share one:
# own Factorio client (data dir + save), own bridge, own Kiro Crew gateway with Kira and
# the dispatcher, all from THIS checkout. The default stand (slot 0) is the one in
# ~/.factorycrew-dev + ~/.kirocrew-factorio + 5477, left alone by other slots.
#
#   scripts/stand.sh up [SLOT]    # slot 1..9, remembered in .stand; game + bridge + gateway + Kira
#   scripts/stand.sh down         # stop game, bridge and gateway of this stand
#   scripts/stand.sh status | env
#   scripts/stand.sh ui <ui_dev.py args...>   # e.g. shot NAME open, lua 'return 1'
#
# Slot N: bridge HTTP 34198+100N (UDP +1 bridge, +2 game), gateway 5500+N (5477-5479 are
# taken by the default and app gateways),
# homes ~/.factorycrew-dev-<name>, ~/.kirocrew-factorio-<name> (<name> = checkout dir name).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(basename "$ROOT")"
SLOT_FILE="$ROOT/.stand"

slot() {
  local s="${1:-}"
  [ -n "$s" ] || s="$(cat "$SLOT_FILE" 2>/dev/null || true)"
  if ! [[ "$s" =~ ^[1-9]$ ]]; then
    echo "give a slot 1..9 the first time: scripts/stand.sh up 2 (ports differ per slot)" >&2; exit 2
  fi
  echo "$s" > "$SLOT_FILE"
  echo "$s"
}

setenv() {
  local n="$1"
  export FC_UI_HOME="$HOME/.factorycrew-dev-$NAME"
  export FC_UI_HTTP_PORT=$((34198 + 100 * n))
  export FC_BRIDGE_HTTP="$FC_UI_HTTP_PORT"
  export FC_KC_HOME="$HOME/.kirocrew-factorio-$NAME"
  export FC_KC_PORT=$((5500 + n))
}

kc() { "$ROOT/scripts/kc.sh" "$@"; }

# A port held by someone else's process is not ours to reuse (another session's stand).
own_port() { # port, the KIROCREW_HOME / data dir that must appear in its command line or env
  local pid
  pid="$(lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)" # lsof exits 1 on a free port
  [ -z "$pid" ] && return 0
  if ps eww -o command= -p "$pid" | grep -qF -- "$2"; then return 0; fi
  echo "port $1 is held by pid $pid, not by this stand ($2); pick another slot" >&2
  exit 1
}

case "${1:-status}" in
  up)
    setenv "$(slot "${2:-}")"
    own_port "$FC_KC_PORT" "KIROCREW_HOME=$FC_KC_HOME"
    own_port "$FC_UI_HTTP_PORT" "--http-port $FC_UI_HTTP_PORT"
    mkdir -p "$FC_UI_HOME"
    python3 "$ROOT/tools/ui_dev.py" start
    kc start
    kc install
    kc config set agent.session_control true >/dev/null
    if ! kc cron list 2>/dev/null | grep -q factorio-dispatch; then
      kc cron add factorio-dispatch "{\"bridge\": \"http://127.0.0.1:$FC_UI_HTTP_PORT\"}" --every 30 \
        --script "$FC_KC_HOME/crons/factorio_dispatch.py:run" \
        --timeout 3600 --timeout-secs 3610 --no-persistent-session --minimal-context --hide-in-chat >/dev/null
    fi
    job="$(kc cron list 2>/dev/null | awk '/factorio-dispatch/ {print $2; exit}')"
    kc cron trigger "$job" >/dev/null 2>&1 || true # 409: a wake is already running
    echo "stand $NAME (slot $(cat "$SLOT_FILE")): game+bridge :$FC_UI_HTTP_PORT, Kiro Crew http://127.0.0.1:$FC_KC_PORT"
    ;;
  down)
    setenv "$(slot)"
    python3 "$ROOT/tools/ui_dev.py" stop || true
    kc stop || true
    ;;
  status)
    setenv "$(slot)"
    curl -fsS --max-time 2 "http://127.0.0.1:$FC_UI_HTTP_PORT/health" && echo || echo "bridge :$FC_UI_HTTP_PORT down"
    kc status || true
    ;;
  env)
    setenv "$(slot)"
    echo "export FC_UI_HOME=$FC_UI_HOME FC_UI_HTTP_PORT=$FC_UI_HTTP_PORT FC_BRIDGE_HTTP=$FC_BRIDGE_HTTP FC_KC_HOME=$FC_KC_HOME FC_KC_PORT=$FC_KC_PORT"
    ;;
  ui)
    setenv "$(slot)"
    shift
    python3 "$ROOT/tools/ui_dev.py" "$@"
    ;;
  *)
    sed -n '2,13p' "$0"; exit 2
    ;;
esac
