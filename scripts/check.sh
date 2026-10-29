#!/usr/bin/env bash
# The fast check: everything that needs no Kiro Crew, no model and no game window, ~2 min.
# Run it before every commit; CI runs the same steps.
#
#   scripts/check.sh            # unit, level 1 on 2.0.77 and 2.1.23, level 2
#
# Every step runs even when an earlier one fails, so one red step does not hide another.
# One line per step at the end; the exit code is non-zero when any step failed.
# Full logs: .test-data/check/<step>.log. The e2e levels (scripts/test-e2e.sh,
# scripts/test-app-e2e.sh) use a real model and are not part of it.
# Plain bash 3.2 (macOS /bin/bash): no associative arrays.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/.test-data/check"
mkdir -p "$LOGS"

step_cmd() {
  case "$1" in
    unit) echo "$ROOT/scripts/test-unit.sh" ;;
    level1-2.0) echo "$ROOT/scripts/test-mod.sh" ;;
    level1-2.1) echo "$ROOT/scripts/test-mod-2.1.sh" ;;
    level2) echo "$ROOT/scripts/test-integration.sh" ;;
  esac
}

summary=""
failed=0
for s in unit level1-2.0 level1-2.1 level2; do
  start=$SECONDS
  "$(step_cmd "$s")" </dev/null >"$LOGS/$s.log" 2>&1
  rc=$?
  t=$((SECONDS - start))
  # the last line that says how many passed or failed, from pytest or FactorioTest
  tally="$(grep -E '(passed|failed)' "$LOGS/$s.log" | tail -1 | sed -E 's/^[=[:space:]]+//; s/[=[:space:]]+$//')"
  if [ "$rc" = 0 ]; then st=ok; else st=FAIL; failed=1; fi
  line="$(printf '%-5s %-11s %4ss  %s' "$st" "$s" "$t" "${tally:-rc $rc, see $LOGS/$s.log}")"
  echo "$line"
  summary="$summary$line"$'\n'
done
echo "---"
printf '%s' "$summary"
exit "$failed"
