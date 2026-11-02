"""The kirocrew CLI calls work from a process whose working directory was deleted: an updated app's
MCP server (re-executed in place) and backend keep the old, removed install folder as their cwd,
and the CLI reads its cwd on start (gamer, 2026-11-02: "cannot reach Kiro Crew artifacts:
FileNotFoundError ... os.getcwd()", no panel saved)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROBE = r'''
import os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
gone = sys.argv[3]
os.makedirs(gone); os.chdir(gone); os.rmdir(gone)  # the old install folder, removed by the update
import factorio_mcp as m, factorio_dispatch as d
m._kirocrew = lambda: sys.argv[4]
d.kirocrew_exe = lambda: sys.argv[4]
os.environ["KIROCREW_HOME"] = sys.argv[5]
p = m._kc("list")
print("mcp", p.returncode, p.stdout.strip())
print("dispatcher", d.artifact_cli("list").strip())
'''


def test_the_cli_runs_from_a_deleted_working_directory(tmp_path):
    fake = tmp_path / "kirocrew"
    fake.write_text("#!/usr/bin/python3\nimport os\nprint('cwd ok', os.getcwd())\n")  # like the real CLI
    fake.chmod(0o755)
    p = subprocess.run([sys.executable, "-c", PROBE, str(ROOT / "mcp"), str(ROOT / "dispatcher"),
                        str(tmp_path / "old-install"), str(fake), str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-800:]
    home = os.path.expanduser("~")
    assert f"mcp 0 cwd ok {home}" in p.stdout and f"dispatcher cwd ok {home}" in p.stdout, p.stdout
