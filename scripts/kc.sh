#!/usr/bin/env bash
# Separate Kiro Crew gateway for factorycrew: own data home, own kiro-cli home (agents,
# sessions), own port. Never touches the main gateway (~/.kiro/crew, ~/.kiro/agents, :5476).
#
#   scripts/kc.sh start | stop | status | logs | install | env | <any kirocrew subcommand...>
#
# KIRO_HOME must be exactly $KIROCREW_HOME/kiro: that is the layout Kiro Crew treats as
# the instance's private agents dir (config/paths.py isolated_agents_dir), the same
# recipe its own isolated pods use.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# A linked git worktree never falls back to the shared default instance: it has its own
# (scripts/stand.sh, docs/worktrees.md). FC_ALLOW_DEFAULT=1 overrides.
if [ -z "${FC_KC_HOME:-}" ] && [ -z "${FC_ALLOW_DEFAULT:-}" ] && \
   [ "$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)" != "$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null)" ]; then
  echo "kc.sh: this is a worktree; use its own gateway: scripts/stand.sh kc $*" >&2
  exit 2
fi
export KIROCREW_HOME="${FC_KC_HOME:-$HOME/.kirocrew-factorio}"
export KIRO_HOME="$KIROCREW_HOME/kiro"
export KIROCREW_PORT="${FC_KC_PORT:-5477}"
# No 610 MB embedding model for this instance: memory search uses its keyword fallback.
export KIROCREW_SKIP_MODEL_DOWNLOAD=1
LOG="$KIROCREW_HOME/gateway.log"
URL="http://127.0.0.1:$KIROCREW_PORT"

# Clean environment: when started from inside another Kiro Crew session, that session's
# TMPDIR / scratch / KIRO_SESSION_ID would leak in, and the cron sandbox refuses to run
# scripts staged in another session's scratch dir.
CLEAN=(env -i HOME="$HOME" USER="${USER:-}" LOGNAME="${LOGNAME:-}" SHELL="${SHELL:-/bin/zsh}"
       LANG="${LANG:-en_US.UTF-8}" LC_CTYPE="${LC_CTYPE:-UTF-8}" PATH="$PATH"
       TMPDIR="$KIROCREW_HOME/tmp/"
       KIROCREW_HOME="$KIROCREW_HOME" KIRO_HOME="$KIRO_HOME" KIROCREW_PORT="$KIROCREW_PORT"
       KIROCREW_SKIP_MODEL_DOWNLOAD=1 KIRO_NO_AUTO_UPDATE=1
       ${KIROCREW_BUNDLED_KIRO_DIR:+KIROCREW_BUNDLED_KIRO_DIR="$KIROCREW_BUNDLED_KIRO_DIR"})
# The gateway itself starts without KIROCREW_PORT, only with --port, the way the desktop app starts
# a player's gateway: inherited by crons and MCP servers, the variable hid that their CLI calls
# dialed 5476 (another gateway on a machine with two), and players' panels went unsaved.
GATEWAY=()
for a in "${CLEAN[@]}"; do [[ "$a" == KIROCREW_PORT=* ]] || GATEWAY+=("$a"); done

up() { curl -fsS -o /dev/null --max-time 2 "$URL/api/health" 2>/dev/null; }

install_files() {
  mkdir -p "$KIRO_HOME/agents" "$KIROCREW_HOME/crons"
  # agent template with absolute paths to this checkout
  # FC_BRIDGE_HTTP: the bridge's HTTP port for this instance's MCP (a stand per worktree, scripts/stand.sh)
  sed -e "s#__ROOT__#$ROOT#g" -e "s#127.0.0.1:34198#127.0.0.1:${FC_BRIDGE_HTTP:-34198}#g" \
    "$ROOT/agent/factorio-companion.json" > "$KIRO_HOME/agents/factorio-companion.json"
  echo "installed agent -> $KIRO_HOME/agents/factorio-companion.json"
  # Copy only on change: a running wake exits when this file's mtime moves (it compiled the
  # old source), so an identical copy would restart it for nothing.
  if cmp -s "$ROOT/dispatcher/factorio_dispatch.py" "$KIROCREW_HOME/crons/factorio_dispatch.py"; then
    echo "dispatcher unchanged -> $KIROCREW_HOME/crons/factorio_dispatch.py"
  else
    cp "$ROOT/dispatcher/factorio_dispatch.py" "$KIROCREW_HOME/crons/factorio_dispatch.py"
    echo "installed dispatcher -> $KIROCREW_HOME/crons/factorio_dispatch.py (running wake restarts)"
  fi
}

case "${1:-status}" in
  env)
    echo "export KIROCREW_HOME=$KIROCREW_HOME KIRO_HOME=$KIRO_HOME KIROCREW_PORT=$KIROCREW_PORT KIROCREW_SKIP_MODEL_DOWNLOAD=1"
    ;;
  run)
    # foreground, for launchd (scripts/services.sh)
    mkdir -p "$KIROCREW_HOME" "$KIRO_HOME" "$KIROCREW_HOME/tmp"
    [ -f "$KIRO_HOME/agents/kirocrew.json" ] || "${CLEAN[@]}" kirocrew setup --agent-only || true
    exec "${GATEWAY[@]}" kirocrew gateway --no-open --no-tunnel --port "$KIROCREW_PORT"
    ;;
  start)
    if up; then echo "already up: $URL"; exit 0; fi
    mkdir -p "$KIROCREW_HOME" "$KIRO_HOME" "$KIROCREW_HOME/tmp"
    [ -f "$KIRO_HOME/agents/kirocrew.json" ] || "${CLEAN[@]}" kirocrew setup --agent-only >>"$LOG" 2>&1 || true
    # detached: survives this shell; stop with `scripts/kc.sh stop`
    nohup "${GATEWAY[@]}" kirocrew gateway --no-open --no-tunnel --port "$KIROCREW_PORT" >>"$LOG" 2>&1 < /dev/null &
    for _ in $(seq 1 120); do up && { echo "up: $URL"; exit 0; }; sleep 1; done
    echo "gateway did not come up in 120 s, see $LOG" >&2; exit 1
    ;;
  stop)
    "${CLEAN[@]}" kirocrew stop
    ;;
  status)
    if up; then echo "up: $URL (home $KIROCREW_HOME)"; else echo "down (home $KIROCREW_HOME)"; exit 1; fi
    ;;
  logs)
    tail -n "${2:-80}" "$LOG"
    ;;
  install)
    install_files
    ;;
  *)
    "${CLEAN[@]}" kirocrew "$@"
    ;;
esac
