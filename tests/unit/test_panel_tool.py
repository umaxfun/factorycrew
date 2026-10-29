"""factorio_panel builds one Lua query; the spec must reach the game byte for byte."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp"))
import factorio_mcp as m  # noqa: E402


def test_long_string_never_closes_early():
    for s in ["plain", "a]]b", "a]]b]=]c]==]", "]"]:
        lit = m.lua_long_string(s)
        eq = lit[1:lit.index("[", 1)]
        assert lit == f"[{eq}[{s}]{eq}]" and f"]{eq}]" not in s


def test_panel_lua_carries_the_spec_as_json():
    lua = m.panel_lua({"name": "sci", "lua": "return {']]'}", "every": 3, "inputs": [{"key": "p", "options": ["1m"]}],
                       "title": None})
    assert lua.startswith("return kc.panel(helpers.json_to_table([")
    body = lua[lua.index("[", lua.index("json_to_table(")) :]
    eq = body[1:body.index("[", 1)]
    spec = json.loads(body[len(eq) + 2 : body.rindex(f"]{eq}]")])
    assert spec == {"name": "sci", "lua": "return {']]'}", "every": 3, "inputs": [{"key": "p", "options": ["1m"]}]}
    assert "panel_remove" in m.panel_lua({"name": "sci", "remove": True})


def test_panel_needs_a_name_and_a_body():
    assert m.panel({"lua": "return {}"})["error"] == "bad_request"
    assert m.panel({"name": "x"})["error"] == "bad_request"
    assert "factorio_panel" in [t["name"] for t in m.TOOLS]
