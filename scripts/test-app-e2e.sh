#!/usr/bin/env bash
# App e2e: build the Kiro Crew app, reinstall it into a dev gateway, run the real Factorio
# 2.0.77 client against the app's bridge, and drive questions through the window.
# No Docker. Needs /Users/Shared/factorio.app (FACTORIO_APP) and a real model (a few agent turns).
#
# Isolated per worktree (docs/worktrees.md): this checkout's slot (scripts/stand.sh claim) gives
# the run its own app gateway (FC_APP_PORT, home FC_APP_HOME), app bridge ports
# (FC_APP_HTTP/UDP/GAME) and client data dir (FC_APP_UI_HOME). Nothing outside the slot is touched.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/checkout.sh"
checkout_readable_by_gateway "$ROOT"
eval "$("$ROOT/scripts/stand.sh" claim)"
export FC_KC_HOME="$FC_APP_HOME" FC_KC_PORT="$FC_APP_PORT"
# Error reports (docs/sentry.md): the installed app sends nothing to the real Sentry during the
# run. A setting of this home, read by every part on each send (the gateway may not pass env on).
mkdir -p "$FC_KC_HOME/factorio" && echo '{"dsn": ""}' > "$FC_KC_HOME/factorio/report.json"
BRIDGE="http://127.0.0.1:$FC_APP_HTTP"

"$ROOT/scripts/app-dev.sh" ports "$FC_APP_HTTP" "$FC_APP_UDP" "$FC_APP_GAME" >/dev/null
"$ROOT/scripts/app-dev.sh"

for _ in $(seq 1 60); do curl -fsS --max-time 2 "$BRIDGE/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS --max-time 2 "$BRIDGE/health" >/dev/null || { echo "app bridge not up on $BRIDGE" >&2; exit 1; }

# A wake of the app's dispatcher cron that started after this install.
JOB="$("$ROOT/scripts/kc.sh" cron list 2>/dev/null | awk '/factorycrew\/factorio-dispatch/ {print $2; exit}')"
SRC="$FC_APP_HOME/apps/factorycrew/dispatcher/factorio_dispatch.py"
fresh_wake() {
  python3 - "$FC_APP_HOME" "$SRC" <<'EOF'
import glob, json, os, sys
home, src = sys.argv[1], sys.argv[2]
for p in glob.glob(f"{home}/cron-running/*.json"):
    m = json.load(open(p))
    if m.get("name") == "factorycrew/factorio-dispatch":
        sys.exit(0 if m["started_at"] >= os.stat(src).st_mtime else 2)
sys.exit(1)
EOF
}
for i in $(seq 1 90); do
  rc=0; fresh_wake || rc=$?
  [ "$rc" = 0 ] && break
  if [ "$rc" = 1 ] && [ $((i % 15)) = 1 ]; then "$ROOT/scripts/kc.sh" cron trigger "$JOB" >/dev/null 2>&1 || true; fi
  sleep 1
done
fresh_wake || { echo "no app dispatcher wake after 90 s" >&2; exit 1; }

# The client plays the built app's mod, so the test covers what players get.
export FC_UI_EXTERNAL_BRIDGE=1 FC_UI_MOD_SRC="$ROOT/dist/factorycrew/mod/kc-companion"
export FC_UI_HOME="$FC_APP_UI_HOME" FC_UI_HTTP_PORT="$FC_APP_HTTP"
mkdir -p "$FC_UI_HOME"
python3 "$ROOT/tools/ui_dev.py" stop >/dev/null
python3 "$ROOT/tools/ui_dev.py" start
cleanup() {
  python3 "$ROOT/tools/ui_dev.py" stop >/dev/null || true
  [ -n "${FC_APP_KEEP_GATEWAY:-}" ] || "$ROOT/scripts/kc.sh" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

cd "$ROOT"
FC_APP_BRIDGE="$BRIDGE" uv run --no-project --with pytest==8.3.5 python -m pytest -p no:cacheprovider -s -q tests/app_e2e "$@"
