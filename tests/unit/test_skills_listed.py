"""Every skill reaches Kira: her rules, the factorio_compute description and the app manifest name it.

Kira has no file tools, so a skill nobody names is code she never uses: all seven were importable
while the rules named only `belts`, and a build ran its power line with the wrong poles by hand.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALL = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists())
# code skills ship a lib/ that factorio_compute imports; a text skill (feedback) is a procedure
# around one tool, carried by Kira's rules
SKILLS = [s for s in ALL if (ROOT / "skills" / s / "lib").is_dir()]
TEXT_SKILLS = [s for s in ALL if s not in SKILLS]


def test_there_are_skills():
    assert len(SKILLS) >= 7, SKILLS


def test_kiras_rules_name_every_skill():
    rules = (ROOT / "agent" / "factorio-companion.md").read_text(encoding="utf-8")
    missing = [s for s in SKILLS if not re.search(rf"`import {s}`", rules)]
    assert missing == [], f"agent/factorio-companion.md does not tell Kira about: {missing}"


def test_the_compute_tool_names_every_skill():
    sys.path.insert(0, str(ROOT / "mcp"))
    import factorio_mcp as m
    desc = next(t["description"] for t in m.TOOLS if t["name"] == "factorio_compute")
    missing = [s for s in SKILLS if f"`{s}`" not in desc and f"`import {s}`" not in desc]
    assert missing == [], f"factorio_compute's description does not name: {missing}"


def test_the_app_registers_every_skill():
    app = json.loads((ROOT / "app" / "app.json").read_text(encoding="utf-8"))
    assert sorted(app.get("skills") or []) == [f"skills/{s}" for s in ALL]


def test_kiras_rules_name_every_text_skill():
    rules = (ROOT / "agent" / "factorio-companion.md").read_text(encoding="utf-8")
    missing = [s for s in TEXT_SKILLS if f"skill `{s}`" not in rules]
    assert TEXT_SKILLS and missing == [], f"agent/factorio-companion.md does not name the text skills: {missing}"
