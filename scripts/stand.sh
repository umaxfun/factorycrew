#!/usr/bin/env bash
# A whole factorycrew stack per worktree, so parallel sessions never share one: its own
# Factorio client (data dir + save), bridge, and Kiro Crew gateway with Kira and the
# dispatcher, all running THIS checkout's code on ports nobody else holds. See docs/worktrees.md.
#
#   scripts/stand.sh up [SLOT]     # claim a free slot (or the given one), start everything
#   scripts/stand.sh claim [SLOT]  # only claim the slot and print its exports (the e2e scripts use it)
#   scripts/stand.sh check         # verify this stand end to end, and only this stand
#   scripts/stand.sh env           # this stand's ports and homes, as exports
#   scripts/stand.sh status
#   scripts/stand.sh ui <ui_dev.py args...>   # e.g. shot NAME open, restart, lua 'return 1'
#   scripts/stand.sh kc <kc.sh args...>       # e.g. token, install, cron list, logs
#   scripts/stand.sh down          # stop game, bridge and gateway; keep the slot and the data
#   scripts/stand.sh release       # down, then give the slot back
#
# Slot N (1..FC_STAND_MAX, default 20), with P = B+100N (B = FC_STAND_BRIDGE_BASE, 34198) and
# G = FC_STAND_GATEWAY_BASE (5500):
#   stand:   bridge HTTP P, bridge UDP P+1, game UDP P+2, gateway G+N
#   e2e:     bridge HTTP P+10, RCON P+11 (Docker stand), gateway G+100+N   (scripts/test-e2e.sh)
#   app e2e: bridge HTTP P+20, UDP P+21, game UDP P+22, gateway G+200+N   (scripts/test-app-e2e.sh)
# Slot 0 would be the default ports of a plain install, so it is never handed out.
# Claims live in FC_STAND_DIR (default ~/.factorycrew-stands), one dir per slot holding the
# owner checkout's path; a claim whose checkout no longer exists is stale and reused.
# Homes (<dir> = checkout dir name): ~/.factorycrew-dev-<dir>, ~/.kirocrew-factorio-<dir>;
# e2e ~/.kirocrew-factorio-<dir>-e2e; app e2e ~/.kirocrew-factorio-<dir>-app, ~/.factorycrew-dev-<dir>-app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(basename "$ROOT")"
SLOT_FILE="$ROOT/.stand"
REG="${FC_STAND_DIR:-$HOME/.factorycrew-stands}"
MAX="${FC_STAND_MAX:-20}"
BRIDGE_BASE="${FC_STAND_BRIDGE_BASE:-34198}"
GATEWAY_BASE="${FC_STAND_GATEWAY_BASE:-5500}"

kc() { "$ROOT/scripts/kc.sh" "$@"; }

ports_of() { # slot -> "proto:port ..." for everything the slot owns
  local b=$((BRIDGE_BASE + 100 * $1)) g=$((GATEWAY_BASE + $1))
  echo "tcp:$g tcp:$b udp:$((b + 1)) udp:$((b + 2))" \
       "tcp:$((g + 100)) tcp:$((b + 10)) tcp:$((b + 11))" \
       "tcp:$((g + 200)) tcp:$((b + 20)) udp:$((b + 21)) udp:$((b + 22))"
}

holder() { # proto port -> pid listening/bound there, or nothing (lsof exits 1 on a free port)
  if [ "$1" = tcp ]; then lsof -nP -iTCP:"$2" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
  else lsof -nP -iUDP:"$2" -t 2>/dev/null | head -1 || true; fi
}

ours() { # pid -> true when the process belongs to this checkout's stand
  local cmd
  cmd="$(ps eww -o command= -p "$1" 2>/dev/null || true)"
  case "$cmd" in
    *"KIROCREW_HOME=$HOME/.kirocrew-factorio-$NAME "*|*"KIROCREW_HOME=$HOME/.kirocrew-factorio-$NAME") return 0 ;;
    *"KIROCREW_HOME=$HOME/.kirocrew-factorio-$NAME-e2e "*|*"KIROCREW_HOME=$HOME/.kirocrew-factorio-$NAME-e2e") return 0 ;;
    *"KIROCREW_HOME=$HOME/.kirocrew-factorio-$NAME-app "*|*"KIROCREW_HOME=$HOME/.kirocrew-factorio-$NAME-app") return 0 ;;
    *"$HOME/.factorycrew-dev-$NAME-app/"*) return 0 ;;
    *"$ROOT/bridge/factorio_bridge.py"*) return 0 ;;
    *"$HOME/.factorycrew-dev-$NAME/"*) return 0 ;;
  esac
  return 1
}

ports_free() { # slot -> 0 when every port of the slot is free or held by this stand
  local pp pid
  for pp in $(ports_of "$1"); do
    pid="$(holder "${pp%%:*}" "${pp#*:}")"
    if [ -n "$pid" ] && ! ours "$pid"; then return 1; fi
  done
  return 0
}

claim() { # slot -> 0 when the slot is now (or already) ours
  local d="$REG/slot-$1" owner
  mkdir -p "$REG"
  if mkdir "$d" 2>/dev/null; then echo "$ROOT" > "$d/owner"; return 0; fi
  owner="$(cat "$d/owner" 2>/dev/null || true)"
  [ "$owner" = "$ROOT" ] && return 0
  if [ -n "$owner" ] && [ ! -d "$owner" ]; then echo "$ROOT" > "$d/owner"; return 0; fi # stale
  return 1
}

current() { # the slot recorded for this checkout, if its claim is still ours
  local s
  s="$(cat "$SLOT_FILE" 2>/dev/null || true)"
  [[ "$s" =~ ^[0-9]+$ ]] || return 1
  [ "$(cat "$REG/slot-$s/owner" 2>/dev/null || true)" = "$ROOT" ] || return 1
  echo "$s"
}

allocate() { # [wanted] -> claimed slot
  local s
  if s="$(current)"; then echo "$s"; return 0; fi
  if [ -n "${1:-}" ]; then
    [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le "$MAX" ] || { echo "slot must be 1..$MAX" >&2; exit 2; }
    ports_free "$1" || { echo "slot $1: one of its ports ($(ports_of "$1")) is held by another process" >&2; exit 1; }
    claim "$1" || { echo "slot $1 is claimed by $(cat "$REG/slot-$1/owner")" >&2; exit 1; }
    echo "$1" > "$SLOT_FILE"; echo "$1"; return 0
  fi
  for s in $(seq 1 "$MAX"); do
    ports_free "$s" || continue
    claim "$s" || continue
    echo "$s" > "$SLOT_FILE"; echo "$s"; return 0
  done
  echo "no free slot in 1..$MAX (claims in $REG)" >&2; exit 1
}

need() { # the claimed slot, or a hint
  current || { echo "this checkout has no stand yet: scripts/stand.sh up" >&2; exit 2; }
}

setenv() {
  local g=$((GATEWAY_BASE + $1)) b=$((BRIDGE_BASE + 100 * $1))
  export FC_STAND_SLOT="$1"
  export FC_E2E_HTTP=$((b + 10)) FC_E2E_RCON=$((b + 11)) FC_E2E_KC_PORT=$((g + 100))
  export FC_E2E_KC_HOME="$HOME/.kirocrew-factorio-$NAME-e2e" FC_E2E_CONTAINER="fc-e2e-$NAME"
  export FC_APP_HTTP=$((b + 20)) FC_APP_UDP=$((b + 21)) FC_APP_GAME=$((b + 22)) FC_APP_PORT=$((g + 200))
  export FC_APP_HOME="$HOME/.kirocrew-factorio-$NAME-app" FC_APP_UI_HOME="$HOME/.factorycrew-dev-$NAME-app"
  export FC_UI_HOME="$HOME/.factorycrew-dev-$NAME"
  export FC_UI_HTTP_PORT="$b"
  export FC_BRIDGE_HTTP="$b"
  export FC_KC_HOME="$HOME/.kirocrew-factorio-$NAME"
  export FC_KC_PORT="$g"
}

check() {
  local fail=0 h t1 t2 agent cron pid
  ok() { echo "ok    $*"; }
  bad() { echo "FAIL  $*"; fail=1; }
  h="$(curl -fsS --max-time 3 "http://127.0.0.1:$FC_UI_HTTP_PORT/health" 2>/dev/null || true)"
  if echo "$h" | grep -q '"game_online": true'; then ok "bridge :$FC_UI_HTTP_PORT, game online"
  else bad "bridge :$FC_UI_HTTP_PORT: ${h:-not answering}"; fi
  pid="$(holder tcp "$FC_UI_HTTP_PORT")"
  if [ -n "$pid" ] && ps -o command= -p "$pid" | grep -qF "$ROOT/bridge/factorio_bridge.py"; then
    # the path alone is not enough: a bridge started before an edit keeps running the old code
    # (on 2026-10-29 it silently dropped the new library messages). ui_dev.py records what it started.
    if [ ! -s "$FC_UI_HOME/bridge.sha" ]; then
      bad "no record of the code the bridge started with (started before this check existed): scripts/stand.sh ui restart"
    elif [ "$(cat "$FC_UI_HOME/bridge.sha")" = "$(python3 "$ROOT/tools/ui_dev.py" digest bridge)" ]; then
      ok "bridge runs this checkout's code"
    else bad "the bridge runs older code than this checkout's (started before a change): scripts/stand.sh ui restart"; fi
  else bad "bridge on :$FC_UI_HTTP_PORT is not this checkout's (pid ${pid:-none})"; fi
  t1="$(python3 "$ROOT/tools/ui_dev.py" lua 'return game.tick' 2>/dev/null || true)"; sleep 1.2
  t2="$(python3 "$ROOT/tools/ui_dev.py" lua 'return game.tick' 2>/dev/null || true)"
  if [[ "$t1" =~ ^[0-9]+$ && "$t2" =~ ^[0-9]+$ && "$t2" -gt "$t1" ]]; then ok "query round trip, game ticking ($t1 -> $t2)"
  else bad "query round trip / ticking: '$t1' -> '$t2' (paused or in a menu?)"; fi
  # the game loads the mod once, at start; ui_dev.py records what it loaded in game-mod.sha
  local stale=""
  if [ "$(cat "$FC_UI_HOME/game-mod.sha" 2>/dev/null || true)" != "$(python3 "$ROOT/tools/ui_dev.py" digest)" ]; then
    stale="the game runs another mod than this checkout's (started before a change, or by someone else): scripts/stand.sh ui restart"
  fi
  if [ -z "$stale" ]; then ok "game runs this checkout's mod"; else bad "$stale"; fi
  pid="$(holder tcp "$FC_KC_PORT")"
  if [ -n "$pid" ] && ours "$pid"; then ok "gateway :$FC_KC_PORT, home $FC_KC_HOME"
  else bad "gateway :$FC_KC_PORT is not this stand's (pid ${pid:-none})"; fi
  agent="$FC_KC_HOME/kiro/agents/factorio-companion.json"
  if grep -qF "127.0.0.1:$FC_UI_HTTP_PORT" "$agent" 2>/dev/null && grep -qF "$ROOT/mcp/factorio_mcp.py" "$agent"; then
    ok "Kira's MCP: this checkout, this bridge"
  else bad "Kira's agent ($agent) does not point at this checkout and :$FC_UI_HTTP_PORT: scripts/stand.sh kc install"; fi
  # what the RUNNING MCP servers dial (the file can be right while a live process is not)
  local mcp n=0 wrong=0 old=0 edited
  # kiro-cli starts one MCP server per chat session and it keeps the code it started with, so an
  # edit of mcp/factorio_mcp.py reaches only new tabs. A server started before the file's last
  # change runs older code. (Its kc/ and skill code is copied fresh on every compute call.)
  edited="$(stat -f %m "$ROOT/mcp/factorio_mcp.py")"
  for mcp in $(pgrep -f "mcp/factorio_mcp.py" || true); do
    ps eww -o command= -p "$mcp" | grep -qF "KIROCREW_HOME=$FC_KC_HOME " || continue
    n=$((n + 1))
    ps -o command= -p "$mcp" | grep -qF -- "--bridge http://127.0.0.1:$FC_UI_HTTP_PORT" || wrong=$((wrong + 1))
    started="$(ps -o lstart= -p "$mcp" | python3 -c 'import sys, time; print(int(time.mktime(time.strptime(" ".join(sys.stdin.read().split()), "%a %b %d %H:%M:%S %Y"))))' 2>/dev/null || echo 0)"
    [ "$started" -ge "$edited" ] || old=$((old + 1))
  done
  if [ "$wrong" -gt 0 ]; then bad "$wrong of $n running Kira MCP servers dial another bridge: scripts/stand.sh kc stop, then up"
  elif [ "$old" -gt 0 ]; then bad "$old of $n running Kira MCP servers started before the last change of mcp/factorio_mcp.py and run older code (their tabs keep it; new tabs get the new code): scripts/stand.sh kc stop, then up"
  elif [ "$n" -gt 0 ]; then ok "running Kira MCP servers ($n) dial this bridge and run this checkout's code"
  else ok "no Kira session running yet (MCP checked on the first question)"; fi
  if cmp -s "$ROOT/dispatcher/factorio_dispatch.py" "$FC_KC_HOME/crons/factorio_dispatch.py"; then ok "dispatcher is this checkout's"
  else bad "installed dispatcher differs: scripts/stand.sh kc install"; fi
  cron="$(kc cron list 2>/dev/null | grep factorio-dispatch || true)"
  if echo "$cron" | grep -qF "127.0.0.1:$FC_UI_HTTP_PORT"; then ok "dispatcher cron polls this bridge"
  else bad "dispatcher cron missing or polling another bridge: ${cron:-none}"; fi
  return $fail
}

case "${1:-status}" in
  up)
    setenv "$(allocate "${2:-}")"
    mkdir -p "$FC_UI_HOME"
    python3 "$ROOT/tools/ui_dev.py" start
    kc install # before start: a gateway sees only the agents present when it starts
    kc start
    kc config set agent.session_control true >/dev/null
    if ! kc cron list 2>/dev/null | grep -q factorio-dispatch; then
      kc cron add factorio-dispatch "{\"bridge\": \"http://127.0.0.1:$FC_UI_HTTP_PORT\"}" --every 30 \
        --script "$FC_KC_HOME/crons/factorio_dispatch.py:run" \
        --timeout 3600 --timeout-secs 3610 --no-persistent-session --minimal-context --hide-in-chat >/dev/null
    fi
    job="$(kc cron list 2>/dev/null | awk '/factorio-dispatch/ {print $2; exit}')"
    kc cron trigger "$job" >/dev/null 2>&1 || true # 409: a wake is already running
    echo "stand $NAME, slot $FC_STAND_SLOT: bridge :$FC_UI_HTTP_PORT, Kiro Crew http://127.0.0.1:$FC_KC_PORT"
    ;;
  check)
    setenv "$(need)"
    check
    ;;
  down)
    setenv "$(need)"
    python3 "$ROOT/tools/ui_dev.py" stop || true
    kc stop || true
    ;;
  release)
    s="$(need)"
    setenv "$s"
    python3 "$ROOT/tools/ui_dev.py" stop || true
    kc stop || true
    rm -rf "${REG:?}/slot-$s"
    rm -f "$SLOT_FILE"
    echo "slot $s released; data stays in $FC_UI_HOME and $FC_KC_HOME"
    ;;
  status)
    setenv "$(need)"
    echo "slot $FC_STAND_SLOT"
    curl -fsS --max-time 2 "http://127.0.0.1:$FC_UI_HTTP_PORT/health" && echo || echo "bridge :$FC_UI_HTTP_PORT down"
    kc status || true
    ;;
  env|claim)
    if [ "$1" = claim ]; then setenv "$(allocate "${2:-}")"; else setenv "$(need)"; fi
    for v in FC_STAND_SLOT FC_UI_HOME FC_UI_HTTP_PORT FC_BRIDGE_HTTP FC_KC_HOME FC_KC_PORT \
             FC_E2E_HTTP FC_E2E_RCON FC_E2E_KC_PORT FC_E2E_KC_HOME FC_E2E_CONTAINER \
             FC_APP_HTTP FC_APP_UDP FC_APP_GAME FC_APP_PORT FC_APP_HOME FC_APP_UI_HOME; do
      printf 'export %s=%q\n' "$v" "${!v}"
    done
    ;;
  ui)
    setenv "$(need)"
    shift
    python3 "$ROOT/tools/ui_dev.py" "$@"
    ;;
  kc)
    setenv "$(need)"
    shift
    kc "$@"
    ;;
  *)
    sed -n '2,25p' "$0"; exit 2
    ;;
esac
