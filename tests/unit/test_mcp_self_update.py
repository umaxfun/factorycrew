"""After an app update the running MCP server restarts itself in place and the new code answers.

A chat keeps its MCP server process for as long as it lives, so without this the old code kept
serving after an update (players' panels were not saved after 0.3.5 until Kiro Crew restarted)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "mcp" / "factorio_mcp.py"


def _install(app: Path, version: str, marker: str) -> None:
    (app / "mcp").mkdir(parents=True, exist_ok=True)
    # the "new code" differs in one visible place: the error code of an unknown tool
    (app / "mcp" / "factorio_mcp.py").write_text(SRC.read_text().replace('"unknown_tool"', f'"{marker}"'))
    (app / "app.json").write_text(json.dumps({"name": "factorycrew", "version": version}))


def _call(i: int) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                        "params": {"name": "no_such_tool", "arguments": {}}}) + "\n").encode()


def _answer(proc) -> dict:
    line = proc.stdout.readline()
    assert line, proc.stderr.read().decode()
    res = json.loads(line)
    return {"id": res["id"], **json.loads(res["result"]["content"][0]["text"])}


# the gateway starts the server with its own Python 3.12; live it may also be macOS's 3.9
PYTHONS = [sys.executable] + [p for p in ["/usr/bin/python3"] if os.access(p, os.X_OK)]


@pytest.mark.parametrize("python", PYTHONS)
def test_server_restarts_in_place_on_update(tmp_path, python):
    app = tmp_path / "crew" / "apps" / "factorycrew"
    _install(app, "1.0.0", "unknown_tool_v1")
    env = {k: v for k, v in os.environ.items() if k != "FC_MCP_PENDING_FILE"}
    proc = subprocess.Popen([python, "-s", str(app / "mcp" / "factorio_mcp.py"), "--bridge",
                             "http://127.0.0.1:9"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    try:
        init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
        proc.stdin.write((json.dumps(init) + "\n").encode())
        proc.stdin.write(_call(1))
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 0
        assert _answer(proc) == {"id": 1, "ok": False, "error": "unknown_tool_v1", "message": "no_such_tool"}

        _install(app, "1.0.1", "unknown_tool_v2")
        # two calls in one write: the second is already in the old process's buffer at re-exec
        proc.stdin.write(_call(2) + _call(3))
        proc.stdin.flush()
        assert _answer(proc)["error"] == "unknown_tool_v2"
        assert _answer(proc) == {"id": 3, "ok": False, "error": "unknown_tool_v2", "message": "no_such_tool"}
        assert proc.poll() is None  # the same process, so the chat's connection stays
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
    assert "app updated 1.0.0 -> 1.0.1" in proc.stderr.read().decode()


def test_no_restart_outside_an_installed_app(monkeypatch):
    # the repo's mcp/ has no app.json beside it: dev stands restart servers themselves
    sys.path.insert(0, str(SRC.parent))
    import factorio_mcp as m
    assert m.STARTED_VERSION is None and m.updated() is False
