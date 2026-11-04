#!/usr/bin/env bash
# Run kiro-cli in CI (GitLab CI or GitHub Actions) and record what it cost. Used by review.sh;
# any other CI agent step can use it too.
# Usage: kiro-run.sh <step> <text-out> kiro-cli chat --no-interactive ... "<prompt>"
#
# Adds --output-format stream-json, so kiro-cli reports the credits of every model request and
# the turn time. Writes:
#   <text-out>                                   the parent session transcript
#   <text-out>.subagents.md                      child transcripts, grouped by session
#   <workspace>/kiro-usage/<job id>-<step>.json  credits and time, schema kirocrew.ci-usage/v1
#                                                (workspace: CI_PROJECT_DIR or GITHUB_WORKSPACE)
#   <text-out>.stream.jsonl                      the raw stream, for debugging
# The usage file is written even when kiro-cli fails or is killed by timeout: a failed run costs
# credits too. The Factory Costs app reads these files from the job artifacts.
# Exit code is kiro-cli's own.
set -uo pipefail
STEP="${1:?step (review, fix, agent)}"; TEXT_OUT="${2:?text output file}"; shift 2
[[ "${1:-}" == kiro-cli || "${1:-}" == timeout ]] || { echo "kiro-run.sh: expected a kiro-cli command, got: ${1:-}" >&2; exit 2; }
case "$STEP" in review|fix|agent) ;; *) echo "kiro-run.sh: step must be review, fix or agent" >&2; exit 2 ;; esac

STREAM="$TEXT_OUT.stream.jsonl"
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  export FACTORY_FORGE=github FACTORY_JOB_ID="${GITHUB_RUN_ID:-0}-${GITHUB_RUN_ATTEMPT:-1}-${GITHUB_JOB:-job}"
  export FACTORY_JOB_NAME="${GITHUB_JOB:-}" FACTORY_PIPELINE_ID="${GITHUB_RUN_ID:-}"
  export FACTORY_MR="$(python3 -I -c 'import json,os;print(json.load(open(os.environ["GITHUB_EVENT_PATH"])).get("pull_request",{}).get("number",""))' 2>/dev/null || true)"
  export FACTORY_COMMIT="${FACTORY_COMMIT:-${GITHUB_SHA:-}}"  # review.sh sets the PR head
  WORKSPACE="${GITHUB_WORKSPACE:-$PWD}"
else
  export FACTORY_FORGE=gitlab FACTORY_JOB_ID="${CI_JOB_ID:-local}" FACTORY_JOB_NAME="${CI_JOB_NAME:-}"
  export FACTORY_PIPELINE_ID="${CI_PIPELINE_ID:-}" FACTORY_MR="${CI_MERGE_REQUEST_IID:-}" FACTORY_COMMIT="${FACTORY_COMMIT:-${CI_COMMIT_SHA:-}}"
  WORKSPACE="${CI_PROJECT_DIR:-$PWD}"
fi
USAGE_DIR="$WORKSPACE/kiro-usage"
USAGE="$USAGE_DIR/$FACTORY_JOB_ID-$STEP.json"
mkdir -p "$USAGE_DIR" "$(dirname "$TEXT_OUT")"

MODEL=""; prev=""; CMD=(); added=""
for a in "$@"; do
  [[ "$prev" == --model ]] && MODEL="$a"
  CMD+=("$a")
  # The flag goes right after `chat`, before the prompt.
  if [[ -z "$added" && "$a" == chat ]]; then CMD+=(--output-format stream-json); added=1; fi
  prev="$a"
done
[[ -n "$added" ]] || { echo "kiro-run.sh: no 'chat' subcommand in the kiro-cli command" >&2; exit 2; }

now_ms() { python3 -I -c 'import time; print(int(time.time() * 1000))'; }
started_ms=$(now_ms)
"${CMD[@]}" > "$STREAM" 2> "$TEXT_OUT.stderr"
rc=$?
finished_ms=$(now_ms)

# -I everywhere inline Python runs: the working directory is the merge request's checkout, and
# without it `python3 -`/`-c` puts that directory first on sys.path, so a json.py there would
# run as root here and could write any verdict.
python3 -I - "$STREAM" "$TEXT_OUT" "$USAGE" "$rc" "$started_ms" "$finished_ms" "$MODEL" "$STEP" <<'PY'
import json, os, sys
stream, text_out, usage, rc, t0, t1, model, step = sys.argv[1:]
credits, metered, turn_ms, session, stop = 0.0, False, 0, "", ""
# Use the initial CLI session as the parent (the stream-json session-order contract).
# Preserve its identity: child chunks and late completions must not change its verdict.
# Keep the raw stream artifact so this contract can be checked after a CLI upgrade.
transcripts, previous = {}, {}
for line in open(stream, errors="replace"):
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    d = ev.get("data") or {}
    sid = d.get("sessionId") or session
    session = session or sid
    for m in d.get("meteringUsage") or []:
        if m.get("unit") == "credit" and isinstance(m.get("value"), (int, float)):
            credits += m["value"]; metered = True
    if isinstance(d.get("turnDurationMs"), (int, float)):
        turn_ms += int(d["turnDurationMs"])
    if ev.get("type") == "runFinished" and sid == session:
        stop = d.get("stopReason") or d.get("status") or ""
    u = d.get("update") or {}
    kind = u.get("sessionUpdate")
    if kind not in {"agent_message_chunk", "tool_call"}:
        continue
    parts = transcripts.setdefault(sid, [])
    if kind == "agent_message_chunk":
        if previous.get(sid) != "text" and parts:
            parts.append("\n")
        parts.append((u.get("content") or {}).get("text", ""))
        previous[sid] = "text"
    else:
        parts.append(f"\n[tool] {u.get('title') or u.get('kind') or ''}\n")
        previous[sid] = "tool"
with open(text_out, "w") as f:
    f.write("".join(transcripts.get(session, [])).rstrip("\n") + "\n")
with open(text_out + ".subagents.md", "w") as f:
    for sid, parts in transcripts.items():
        if sid != session:
            f.write(f"## Session {sid or '(unscoped)'}\n\n")
            f.write("".join(parts).rstrip("\n") + "\n\n")
rec = {
    "schema": "kirocrew.ci-usage/v1",
    "step": step,
    "forge": os.environ.get("FACTORY_FORGE"),
    "job_id": os.environ.get("FACTORY_JOB_ID"), "job_name": os.environ.get("FACTORY_JOB_NAME"),
    "pipeline_id": os.environ.get("FACTORY_PIPELINE_ID"),
    "mr_iid": os.environ.get("FACTORY_MR"), "commit": os.environ.get("FACTORY_COMMIT"),
    "session_id": session, "model": model,
    "credits": round(credits, 6), "metered": metered,
    "agent_ms": turn_ms or int(t1) - int(t0), "wall_ms": int(t1) - int(t0),
    "started_at_ms": int(t0), "finished_at_ms": int(t1),
    "exit_code": int(rc), "stop_reason": stop,
}
with open(usage, "w") as f:
    json.dump(rec, f, indent=1)
print(f"kiro-run: {step} {model or '?'} {credits:.2f} credits, {rec['agent_ms']/1000:.0f}s, exit {rc} -> {usage}")
PY
exit "$rc"
