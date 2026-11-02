#!/usr/bin/env python3
"""Kira's agent-spec hook: the turn contract between Kiro Crew and the bridge.

Kiro Crew runs it for UserPromptSubmit, PostToolUse and Stop of every turn of Kira's
(docs/areas/bridge.md, "Turn contract"). It hands the event to the bridge and, on Stop, prints
the bridge's decision: {"decision": "block", "reason": ...} sends the turn back to Kira when a
question from the game got no factorio_reply. Stdlib only; never fails loudly: a hook that errs
is only logged by Kiro Crew, and the bridge's own backstops still apply.

  turn_hook.py [--bridge http://127.0.0.1:PORT]

Without --bridge the port comes from <app>/data/config.json (http_port), as for the MCP server.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

TIMEOUT = 5.0


def bridge_url() -> str:
    if "--bridge" in sys.argv[1:-1]:
        return sys.argv[sys.argv.index("--bridge") + 1].rstrip("/")
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "data" / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    return f"http://127.0.0.1:{int(cfg.get('http_port', 34198))}"


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    event = data.get("hook_event_name") or os.environ.get("KIROCREW_HOOK_EVENT") or ""
    body = {
        "event": event,
        "session_key": data.get("session_key") or "",
        # Stop: Kiro Crew's assistant_text; kiro-cli running the spec itself names it assistant_response
        "assistant_text": data.get("assistant_text") or data.get("assistant_response") or "",
        "prompt": data.get("prompt") or "",
        "tool_name": data.get("tool_name") or "",
    }
    req = urllib.request.Request(bridge_url() + "/hook", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            res = json.loads(r.read())
    except (OSError, ValueError):
        return 0  # no bridge: nothing to tell, and the turn must not be held up
    if event == "Stop" and res.get("decision") == "block" and res.get("reason"):
        print(json.dumps({"decision": "block", "reason": res["reason"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
