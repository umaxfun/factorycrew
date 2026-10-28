#!/usr/bin/env bash
# App e2e: build the Kiro Crew app, reinstall it into the dev gateway (:5479), run the real
# Factorio 2.0.77 client against the app's bridge, and drive questions through the window.
# No Docker. Needs /Users/Shared/factorio.app and a real model (a few agent turns).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export FC_APP_HOME="${FC_APP_HOME:-$HOME/.kirocrew-factorio-app}"
export FC_KC_HOME="$FC_APP_HOME" FC_KC_PORT="${FC_APP_PORT:-5479}"

# The dev client's ports (tools/ui_dev.py), never the player's 34198-34200.
"$ROOT/scripts/app-dev.sh" ports 34298 34299 34300 >/dev/null
"$ROOT/scripts/app-dev.sh"

for _ in $(seq 1 60); do curl -fsS --max-time 2 http://127.0.0.1:34298/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS --max-time 2 http://127.0.0.1:34298/health >/dev/null || { echo "app bridge not up" >&2; exit 1; }

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
python3 "$ROOT/tools/ui_dev.py" stop >/dev/null
python3 "$ROOT/tools/ui_dev.py" start
trap 'python3 "$ROOT/tools/ui_dev.py" stop >/dev/null' EXIT

cd "$ROOT"
uv run --no-project --with pytest==8.3.5 python -m pytest -p no:cacheprovider -s -q tests/app_e2e "$@"
