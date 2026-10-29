#!/usr/bin/env bash
# Python unit tests (tests/unit): the kc library, atlas, circuits, MCP tools against a fake
# bridge, the dispatcher, the app status page, the e2e watchdog. No game, no Docker, ~15 s.
#
#   scripts/test-unit.sh                       # all of tests/unit
#   scripts/test-unit.sh tests/unit/test_kc_atlas.py -k trace   # any pytest arguments
#
# Needs `uv` (it brings Python 3.12 and pytest; nothing is installed system-wide).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# no path among the arguments (only options like -k or -x): run tests/unit, not pytest.ini's
# testpaths, which would also collect the integration and e2e suites
case " $* " in *" tests/"*) ;; *) set -- tests/unit "$@" ;; esac
exec uv run --no-project --python 3.12 --with pytest==8.3.5 \
  python -m pytest -p no:cacheprovider -q "$@"
