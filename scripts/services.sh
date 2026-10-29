#!/usr/bin/env bash
# Autostart for the bridge and the factorycrew gateway: launchd agents of the current user.
#
#   scripts/services.sh install      # write plists, start both (KeepAlive, RunAtLoad)
#   scripts/services.sh uninstall    # stop both, remove plists
#   scripts/services.sh status
#   scripts/services.sh stop|start bridge|gateway   # one service, plist stays
#
# Logs: ~/.kirocrew-factorio/{bridge,gateway}.launchd.log
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The player's install: never from a linked worktree (its code is not released yet).
# FC_ALLOW_DEFAULT=1 overrides.
if [ -z "${FC_ALLOW_DEFAULT:-}" ] && \
   [ "$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)" != "$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null)" ]; then
  echo "$(basename "$0"): this is a worktree; the player's install is done from the main checkout" >&2
  exit 2
fi
HOME_DIR="${FC_KC_HOME:-$HOME/.kirocrew-factorio}"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABEL_BRIDGE="dev.factorycrew.bridge"
LABEL_GATEWAY="dev.factorycrew.gateway"

plist() { # label, log, program args...
  local label="$1" log="$2"; shift 2
  local args=""
  for a in "$@"; do args+="    <string>$a</string>"$'\n'; done
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
EOF
}

label_of() { case "$1" in bridge) echo "$LABEL_BRIDGE";; gateway) echo "$LABEL_GATEWAY";; *) echo "unknown service $1" >&2; exit 2;; esac; }
loaded() { launchctl print "$DOMAIN/$1" >/dev/null 2>&1; }
start_one() { local l; l="$(label_of "$1")"; loaded "$l" || launchctl bootstrap "$DOMAIN" "$AGENTS/$l.plist"; }
stop_one() { local l; l="$(label_of "$1")"; ! loaded "$l" || launchctl bootout "$DOMAIN/$l"; }

case "${1:-status}" in
  install)
    mkdir -p "$AGENTS" "$HOME_DIR"
    plist "$LABEL_BRIDGE" "$HOME_DIR/bridge.launchd.log" /usr/bin/python3 "$ROOT/bridge/factorio_bridge.py" \
      > "$AGENTS/$LABEL_BRIDGE.plist"
    plist "$LABEL_GATEWAY" "$HOME_DIR/gateway.launchd.log" /bin/bash "$ROOT/scripts/kc.sh" run \
      > "$AGENTS/$LABEL_GATEWAY.plist"
    # a gateway started by hand (kc.sh start) holds the port: hand it over to launchd
    if ! loaded "$LABEL_GATEWAY" && "$ROOT/scripts/kc.sh" status >/dev/null 2>&1; then
      "$ROOT/scripts/kc.sh" stop; sleep 3
    fi
    start_one bridge
    start_one gateway
    "$0" status
    ;;
  uninstall)
    stop_one bridge || true
    stop_one gateway || true
    rm -f "$AGENTS/$LABEL_BRIDGE.plist" "$AGENTS/$LABEL_GATEWAY.plist"
    echo "removed"
    ;;
  start) start_one "${2:?bridge|gateway}" ;;
  stop) stop_one "${2:?bridge|gateway}" ;;
  status)
    for s in bridge gateway; do
      l="$(label_of $s)"
      if loaded "$l"; then echo "$s: launchd ($l)"; else echo "$s: not in launchd"; fi
    done
    curl -fsS --max-time 2 http://127.0.0.1:34198/health 2>/dev/null && echo || echo "bridge http: down"
    "$ROOT/scripts/kc.sh" status || true
    ;;
  *) echo "usage: $0 install|uninstall|status|start <svc>|stop <svc>" >&2; exit 2 ;;
esac
