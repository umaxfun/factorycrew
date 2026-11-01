#!/usr/bin/env bash
# Level 2: mod + bridge + MCP server integration, headless Factorio in Docker.
# Runs on headless 2.1.x, not 2.0.77: a 2.0 headless server aborts on the first UDP packet
# it receives (forums.factorio.com/viewtopic.php?t=134790, fixed in 2.1). The player's 2.0.77
# client is not affected; UDP on 2.0.77 itself is checked in the live game (level 3).
# Logs of the last run: .test-data/integration/{factorio,bridge,create}.log
set -euo pipefail
# Error reports (docs/sentry.md): tests send nothing to the real Sentry unless FC_SENTRY_DSN
# is given; tests that check reports start their own fake one.
export FC_SENTRY_DSN="${FC_SENTRY_DSN-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${FACTORIO_L2_VERSION:-2.1.23}"
IMAGE="factorycrew-test:$VERSION"
. "$ROOT/scripts/lib/docker.sh"
docker_alive

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build --platform linux/amd64 --build-arg FACTORIO_VERSION="$VERSION" -t "$IMAGE" "$ROOT/docker"
fi

docker_ok "$IMAGE"
docker_bounded "fc-l2-$(basename "$ROOT")-$$" "${FC_L2_TIMEOUT:-900}" --platform linux/amd64 -v "$ROOT:/work" "$IMAGE" \
  python3 -m pytest -p no:cacheprovider -q /work/tests/integration "$@"
