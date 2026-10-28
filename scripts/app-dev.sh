#!/usr/bin/env bash
# Dev cycle for the Kiro Crew app on a separate dev gateway (never the player's):
# build dist/factorycrew, reinstall it, trust it, enable it. App data (data/config.json,
# dispatcher state) survives the reinstall; trust does not, so it is granted again.
#
#   scripts/app-dev.sh                # build + reinstall + enable on :5479, ~/.kirocrew-factorio-app
#   scripts/app-dev.sh ports H U G    # write data/config.json: bridge http, bridge udp, game udp
#
# Env: FC_APP_HOME (default ~/.kirocrew-factorio-app), FC_APP_PORT (default 5479).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export FC_KC_HOME="${FC_APP_HOME:-$HOME/.kirocrew-factorio-app}" FC_KC_PORT="${FC_APP_PORT:-5479}"
K="$ROOT/scripts/kc.sh"
DATA="$FC_KC_HOME/apps/factorycrew/data"

if [ "${1:-}" = ports ]; then
  mkdir -p "$DATA"
  printf '{"http_port": %d, "udp_port": %d, "game_port": %d}\n' "$2" "$3" "$4" > "$DATA/config.json"
  cat "$DATA/config.json"
  exit 0
fi

"$K" status >/dev/null || "$K" start
"$ROOT/scripts/build-app.sh"
"$K" config set agent.session_control true >/dev/null 2>&1
if "$K" app info factorycrew >/dev/null 2>&1; then
  "$K" app uninstall factorycrew 2>&1 | grep -E '✅|❌' || true
fi
"$K" app install "$ROOT/dist/factorycrew" 2>&1 | grep -E '✅|❌'
"$K" config set agent.apps_trusted '["factorycrew"]' >/dev/null 2>&1
"$K" app enable factorycrew 2>&1 | grep -E '✅|❌|Backend|Agents|Skills'
