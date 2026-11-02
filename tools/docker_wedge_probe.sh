#!/usr/bin/env bash
# Probe for the Docker Desktop stalls of docs/docker-hangs.md: times one amd64 container start
# before, during and after a long-running container with published ports (like the e2e stand),
# and after it is removed with `docker rm -f`.
#   tools/docker_wedge_probe.sh [seconds the stand-like container lives, default 60]
set -u
LIVE="${1:-60}"
p() {
  local t0 rc
  t0=$(date +%s)
  timeout -k 2 40 docker run --rm --platform linux/amd64 factorycrew-test:2.0.77 true >/dev/null 2>&1
  rc=$?
  echo "$(date +%H:%M:%S) $1: rc=$rc $(( $(date +%s) - t0 ))s created=$(timeout -k 2 10 docker ps -aq --filter status=created | wc -l | tr -d ' ')"
}
p before
timeout -k 2 30 docker run -d --name fc-wedge-test --platform linux/amd64 \
  -p 127.0.0.1:39998:34198 -p 127.0.0.1:39999:27015 factorycrew-test:2.1.23 sleep "$((LIVE + 120))" >/dev/null
p "stand up"
sleep "$LIVE"
p "stand after ${LIVE}s"
timeout -k 2 30 docker rm -f fc-wedge-test >/dev/null
p "after rm -f"
sleep 10
p "10s later"
