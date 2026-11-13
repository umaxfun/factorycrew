#!/usr/bin/env bash
# AI review of a merge request (GitLab) or pull request (GitHub) with kiro-cli, on one model.
# Usage: review.sh <model>      (the CI job ai-review-<model slug> runs it)
#
# Run it from the copy of factory/ taken from the DEFAULT branch (the CI templates do that), so
# the prompt modules next to this script never come from a crew-pushed feature branch;
# factory.yml, the calibration file and the area docs come from the target, so a merge request
# cannot weaken the review that judges it. Only when the default branch has no factory/ yet
# (the merge request that adds the factory) is the merge
# request's own copy used, and that one touches gate paths, so a person approves it.
#
# Needs: KIRO_API_KEY; a checkout of the head commit with full history in the working
# directory; python3 with PyYAML; FACTORY_READ_TOKEN (GitLab) or GITHUB_TOKEN (GitHub) to read
# the issues the merge request closes (without it the review runs without them).
#
# Exit 0 only on a last line "VERDICT: PASS" plus a "[FACTORY-REVIEWED] <head sha>" marker.
# Anything else (BLOCK, no verdict, wrong sha, timeout) fails: not checked is not clean.
# Exit 3 (GitLab: allowed to fail) when the tier job says this tier does not need this review,
# or that the merge request is out of the factory's scope (FACTORY_IN_SCOPE=0).
# Exit 4: an infrastructure failure, not a verdict on the change: the review blocked only because
# a lens failed to run (`lens_errors`) and found nothing. The anchors line says `infra`; GitLab
# retries the job once on its own, and the merger asks for a retry.
set -euo pipefail
MODEL="${1:?model}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SLUG="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
JOB="ai-review-$SLUG"

if [[ -n "${GITLAB_CI:-}" ]]; then
  TARGET="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:?not a merge request pipeline}"
  SOURCE=""
  # The source branch's design is read only from this project's own branch, never a fork's.
  if [[ "${CI_MERGE_REQUEST_SOURCE_PROJECT_ID:-}" == "${CI_MERGE_REQUEST_PROJECT_ID:-x}" ]]; then
    SOURCE="${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-}"
  fi
  HEAD_SHA="${CI_COMMIT_SHA:?}"
  printf '%s\n\n%s\n' "${CI_MERGE_REQUEST_TITLE:-}" "${CI_MERGE_REQUEST_DESCRIPTION:-}" > /tmp/factory-intent.txt
  JOB_URL="${CI_JOB_URL:-}"
elif [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  TARGET="${GITHUB_BASE_REF:?not a pull_request run}"
  SOURCE="$(python3 -I -c 'import json,os;p=json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"];print(p["head"]["ref"] if (p["head"].get("repo") or {}).get("full_name")==(p["base"].get("repo") or {}).get("full_name") else "")')"
  HEAD_SHA="$(python3 -I -c 'import json,os;print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["head"]["sha"])')"
  python3 -I - > /tmp/factory-intent.txt <<'PY'
import json, os
pr = json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]
print(pr.get("title") or "", "", pr.get("body") or "", sep="\n")
PY
  JOB_URL="${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
else
  echo "review.sh: not in GitLab CI or GitHub Actions" >&2; exit 2
fi

REPO="$PWD"
OUT="$REPO/.review/$SLUG"
# The checkout is the merge request's: a committed .review/ or kiro-usage/ (a symlink, or a
# directory holding one) would make the root-run steps below write or copy through it. Start
# both from nothing; rm -rf removes a symlink itself, never its target.
rm -rf -- "$REPO/.review" "$REPO/kiro-usage"
mkdir -p "$OUT"
# How this job ended, for the comment on the merge request (publish.py): the exit code and the
# head it is about, written on every way out from here on, the two skips below included. The
# comment then never says PASS for a review that failed closed, or for another commit, and a
# skipped review leaves `3 <sha>` here, never a folder the merge request committed. It also
# undoes the confinement, once set.
finish() {
  local code=$?
  if [[ -n "${CONFINE+x}" ]]; then "${CONFINE[@]}" restore --ws "$WS" >/dev/null 2>&1 || true; fi
  printf '%s %s\n' "$code" "$HEAD_SHA" > "$OUT/result" 2>/dev/null || true
}
trap finish EXIT

# scope.mode own: the tier job found the merge request outside the factory's stream. Skip.
if [[ "${FACTORY_IN_SCOPE:-1}" == "0" ]]; then
  echo "out of the factory's scope (scope.mode own): $JOB skipped"
  exit 3
fi
# The tier job listed the checks this merge request's tier needs. Not on the list: skip.
if [[ -n "${FACTORY_CHECKS+x}" && ",${FACTORY_CHECKS}," != *",${JOB},"* ]]; then
  echo "not required for ${FACTORY_TIER:-this tier}: $JOB skipped (checks: ${FACTORY_CHECKS})"
  exit 3
fi

mv /tmp/factory-intent.txt "$OUT/intent.txt"
[[ "$(git rev-parse HEAD)" == "$HEAD_SHA" ]] || { echo "checkout is $(git rev-parse HEAD), not the head $HEAD_SHA: check out the head commit" >&2; exit 1; }
git fetch --quiet origin "+refs/heads/$TARGET:refs/remotes/origin/$TARGET"   # the branch, never a tag of that name
REF="refs/remotes/origin/$TARGET"   # the full name: no tag `origin/<branch>` stands in
BASE="$(git merge-base "$REF" HEAD)"
# The merge request's own .gitattributes must not hide its code (`-diff`, a textconv or an external
# diff driver): force a plain text diff of every file.
git diff --text --no-ext-diff --no-textconv "$BASE"...HEAD > "$OUT/diff.patch"
# --text puts a binary file's raw bytes into the patch; a reader that refuses invalid UTF-8 would
# then see none of the hunks after it. Replace invalid bytes (U+FFFD) so every hunk stays readable.
python3 -I -c 'import sys;p=sys.argv[1];b=open(p,"rb").read();open(p,"w",encoding="utf-8").write(b.decode("utf-8","replace"))' "$OUT/diff.patch"

# A feature branch going into the default branch is judged with the design approved into it:
# fetch the branch as the forge holds it (not the head commit). prepare.py decides whether it
# applies and says in inputs.txt when the head is not that branch's tip.
SOURCE_REF=""
if [[ -n "$SOURCE" && "$SOURCE" != "$TARGET" ]] && git fetch --quiet origin "+refs/heads/$SOURCE:refs/remotes/origin/$SOURCE"; then
  SOURCE_REF="refs/remotes/origin/$SOURCE"
fi

# The agent runs as a separate user on copies of the checkout and of the inputs (confine.py):
# root runs the confinement, directly (GitLab's docker runner) or through passwordless sudo
# (GitHub Actions). Without either the review cannot be confined, so it does not run.
if [[ $EUID -eq 0 ]]; then
  AS_ROOT=(); ENV_STDIN=()
elif command -v sudo >/dev/null && sudo -n true 2>/dev/null; then
  # No -E: a sudo resets the environment, and some ignore the flag (sudo-rs: "preserving the
  # entire environment is not supported"). Each confine.py step gets the few variables it
  # needs on its stdin instead (confine_step below), so nothing depends on what sudo keeps.
  AS_ROOT=(sudo -n); ENV_STDIN=(--env-stdin)
else
  echo "cannot run the review agent as a separate user (not root, no passwordless sudo): failing closed"; exit 1
fi
# Under /tmp, not $TMPDIR or the runner temp dir: confine.py locks those.
WS="$(mktemp -d /tmp/factory-review-XXXXXX)"

python3 "$HERE/prepare.py" --repo "$REPO" --ref "$REF" --target "$TARGET" \
  --head "$HEAD_SHA" --base "$BASE" --out "$OUT" --source "$SOURCE" --source-ref "$SOURCE_REF" \
  --agent-repo "$WS/repo" --agent-out "$WS/inputs"

if ! command -v kiro-cli >/dev/null; then
  curl -fsSL https://cli.kiro.dev/install -o /tmp/kiro-install.sh
  bash /tmp/kiro-install.sh > /tmp/kiro-install.log 2>&1 || { tail -20 /tmp/kiro-install.log; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi

# Confine the agent before it reads anything (confine.py says how): its own user, a workspace
# of its own as the working directory (kiro-cli loads README.md, AGENTS.md and .kiro/steering
# from there as context; in the checkout those are the merge request's own text, in the
# workspace there are none), copies of the checkout and the inputs, the job's own directories
# locked, read-only tools in the agent profile, and in its environment only KIRO_API_KEY,
# which kiro-cli takes from nowhere else. The agent engine is pinned to v2: the profile uses
# its tool names, and its stream-json reports the credits kiro-run.sh records. check proves that
# no other process's environment and no CI secret value is readable by that user; if setup or
# check fails, the review fails closed. redact.py below stays as the second layer.
KIRO_BIN="$(dirname "$(readlink -f "$(command -v kiro-cli)")")"
export FACTORY_COMMIT="$HEAD_SHA"
CONFINE=("${AS_ROOT[@]}" python3 "$HERE/confine.py")   # from here on `finish` restores
# The job's variables a step needs, for its stdin: an allow-list per step (confine.py,
# `step_env`), never the whole environment, and never in a command line. Root started by the
# job itself inherits them and reads nothing here.
step_vars() { python3 "$HERE/confine.py" export --for "$1"; }
confine_step() {
  local step="$1"; shift
  if [[ ${#AS_ROOT[@]} -eq 0 ]]; then "${CONFINE[@]}" "$step" "$@"; return; fi
  step_vars "$step" | "${CONFINE[@]}" "$step" --env-stdin "$@"
}
confine_step setup --ws "$WS" --repo "$REPO" --inputs "$OUT" --kiro-bin "$KIRO_BIN" \
  || { echo "could not confine the review agent: failing closed"; exit 1; }
confine_step check --ws "$WS" || { echo "the review agent's confinement does not hold: failing closed"; exit 1; }
# The key goes to `run` only, the same way.
run_agent() {
  bash "$HERE/kiro-run.sh" review "$OUT/raw.md" timeout "${REVIEW_TIMEOUT:-1500}" \
    "${CONFINE[@]}" run --ws "$WS" --with-key "${ENV_STDIN[@]}" -- \
    kiro-cli chat --no-interactive --agent-engine v2 --agent factory-review \
    --trust-tools=read,grep,glob,subagent \
    --model "$MODEL" "$(cat "$OUT/prompt.md")"
}
set +e
if [[ ${#AS_ROOT[@]} -eq 0 ]]; then run_agent; else run_agent < <(step_vars run); fi
rc=$?
echo "confine: kiro-cli ran as user factory-review with agent factory-review (exit $rc)"
python3 "$HERE/redact.py" "$OUT" "${CI_PROJECT_DIR:-${GITHUB_WORKSPACE:-$REPO}}/kiro-usage"
leaked=$?
set -e
touch "$OUT/raw.md"
sed 's/\x1b\[[0-9;]*m//g' "$OUT/raw.md" > "$OUT/review.md"
# An empty transcript has no last line; under pipefail grep's exit 1 must not end the script
# before it says why the review failed.
last="$(grep -v '^[[:space:]]*$' "$OUT/review.md" | tail -n1 | tr -d '\r' || true)"
echo "------ review ($MODEL) ------"
# On GitHub the transcript is printed with workflow commands stopped: a `::notice ...::` line
# the agent wrote must not become an annotation, and the merger reads the anchors notice below.
STOP=""
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  STOP="$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')"
  echo "::stop-commands::$STOP"
fi
cat "$OUT/review.md"
if [[ -n "$STOP" ]]; then echo "::$STOP::"; fi
echo "Complete review artifact: .review/$SLUG/review.md ${JOB_URL}"
echo "-----------------------------"
# The anchors of the blocking findings, for the merger: the crew may release its own block as a
# false positive only when none is security, crash, data-loss, corruption or guard-removal. This
# line comes after the transcript, and the merger reads only the last one.
anchors="$(python3 -I - "$OUT/review.md" <<'PY' || echo unknown
import json, re, sys
KNOWN = {"security", "crash", "data-loss", "corruption", "guard-removal", "broken-contract",
         "design-one-way-door", "product-shape", "evidence-gap", "none"}
blocks = re.findall(r"```json\s*\n(.*?)\n```", open(sys.argv[1], errors="replace").read(), re.S)
try:
    found = {str(f.get("anchor") or "") for f in json.loads(blocks[-1]).get("findings") or []
             if f.get("severity") == "blocking"}
    errors = json.loads(blocks[-1]).get("lens_errors") or []
except (IndexError, ValueError, AttributeError, TypeError):
    found, errors = {"unknown"}, []
if not found <= KNOWN:
    found = {"unknown"}
# No blocking finding, but a lens did not run: the block is the infrastructure's, not the change's.
if not found and errors:
    found = {"infra"}
print(",".join(sorted(found)) or "none")
PY
)"
echo "[FACTORY-ANCHORS] $HEAD_SHA $anchors"
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then echo "::notice title=factory-anchors::$HEAD_SHA $anchors"; fi
[[ $leaked -eq 0 ]] || { echo "the review read a CI credential (see redact above): failing closed"; exit 1; }
[[ $rc -eq 0 ]] || { echo "kiro-cli exited $rc: failing closed"; exit 1; }
grep -q "^\[FACTORY-REVIEWED\] $HEAD_SHA" "$OUT/review.md" || { echo "no [FACTORY-REVIEWED] $HEAD_SHA marker: failing closed"; exit 1; }
case "$last" in
  "VERDICT: PASS") echo "PASS at $HEAD_SHA"; exit 0 ;;
  "VERDICT: BLOCK")
    if [[ "$anchors" == infra ]]; then
      echo "review infra failure at $HEAD_SHA: a lens did not run (lens_errors) and nothing was found; retry the job (GitLab retries it once on its own), or a person overrides with class infra-failure <job URL>"; exit 4
    fi
    echo "BLOCK at $HEAD_SHA: fix the BLOCKING items above and push, or a person overrides with: /factory override ai-review $HEAD_SHA <class> model=$MODEL: <reason> (classes: AGENTS.md, Factory rules)"; exit 1 ;;
  *) echo "no verdict line (last line: $last): failing closed"; exit 1 ;;
esac
