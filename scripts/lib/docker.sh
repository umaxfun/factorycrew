# Docker guards for the test scripts (sourced). A stuck Docker shows up two ways, and both
# used to hang a test run silently:
#   * the daemon does not answer at all (`docker info` blocks);
#   * the daemon answers, but its VM cannot start a container: `docker run` leaves the
#     container in "Created" forever and the client waits (seen 2026-10-29 on Docker Desktop
#     29.6.1, amd64 image under emulation).
# docker_ok fails fast on either with a clear message; docker_bounded puts a wall clock on a
# container and removes it when the clock runs out.
#
# `timeout -k`: a `docker run` client waiting for a container that never starts ignores
# SIGTERM, so a plain `timeout` does not end it.

FC_DOCKER_INFO_S="${FC_DOCKER_INFO_S:-15}"
FC_DOCKER_START_S="${FC_DOCKER_START_S:-60}"

docker_alive() { # the daemon answers at all; call before any other docker command
  if ! timeout -k 3 "$FC_DOCKER_INFO_S" docker info >/dev/null 2>&1; then
    echo "Docker does not answer (docker info > ${FC_DOCKER_INFO_S} s): start or restart Docker Desktop" >&2
    exit 3
  fi
}

docker_ok() { # image: the daemon answers AND can start a container of this image
  docker_alive
  local probe="fc-probe-$$"
  if ! timeout -k 3 "$FC_DOCKER_START_S" docker run --rm --name "$probe" --platform linux/amd64 "$1" true >/dev/null 2>&1; then
    timeout -k 3 20 docker rm -f "$probe" >/dev/null 2>&1 || true
    echo "Docker cannot start a container in ${FC_DOCKER_START_S} s (image $1): the Docker VM is stuck," \
         "restart Docker Desktop" >&2
    exit 3
  fi
}

docker_bounded() { # name seconds docker-run-args... : `docker run --rm --name name ...` with a wall clock
  local name="$1" secs="$2" rc=0
  shift 2
  timeout -k 5 "$secs" docker run --rm --name "$name" "$@" || rc=$?
  if [ "$rc" = 124 ] || [ "$rc" = 137 ]; then
    timeout -k 3 20 docker rm -f "$name" >/dev/null 2>&1 || true
    echo "container $name did not finish in $secs s: stopped and removed (docker logs were in the output above)" >&2
  fi
  return "$rc"
}
