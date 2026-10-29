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


# ── saved panels (Kiro Crew artifacts), with a stand-in `kirocrew` CLI ──

FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
store = os.path.join(os.environ["KIROCREW_HOME"], "fake-artifacts")
os.makedirs(store, exist_ok=True)
a = sys.argv[1:]
assert a[0] == "artifact", a
verb, rest = a[1], a[2:]
def opt(k):
    return rest[rest.index(k) + 1] if k in rest else None
def path(slug): return os.path.join(store, slug + ".json")
if verb == "update":
    if not os.path.exists(path(rest[0])):
        print("Error: no artifact " + rest[0], file=sys.stderr); sys.exit(1)
    d = json.load(open(path(rest[0])))
    d["versions"].append(open(opt("--content-file")).read()); d["name"] = opt("--name") or d["name"]
    d["description"] = opt("--description") or d.get("description")
    json.dump(d, open(path(rest[0]), "w")); print("updated")
elif verb == "save":
    slug = opt("--slug")
    json.dump({"name": opt("--name"), "tags": opt("--tags"), "kind": opt("--kind"), "description": opt("--description"),
               "versions": [open(opt("--content-file")).read()]}, open(path(slug), "w")); print("saved " + slug)
elif verb == "show":
    if not os.path.exists(path(rest[0])):
        print("Error: not found", file=sys.stderr); sys.exit(1)
    d = json.load(open(path(rest[0])))
    if "--meta" in rest:
        print(json.dumps({"slug": rest[0], "name": d["name"], "description": d.get("description"), "updated_at": "t"}))
    else:
        print(d["versions"][-1])
elif verb == "list":
    for f in sorted(os.listdir(store)):
        d = json.load(open(os.path.join(store, f)))
        if opt("--tag") in (d["tags"] or ""): print(f"  {f[:-5]}  {d['name']}  v{len(d['versions'])}")
'''

import os  # noqa: E402
import stat  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def fake_crew(tmp_path, monkeypatch):
    cli = tmp_path / "bin" / "kirocrew"
    cli.parent.mkdir()
    cli.write_text(FAKE_CLI)
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{cli.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FC_NO_ARTIFACTS", raising=False)
    return tmp_path / "home" / "fake-artifacts"


SPEC = {"name": "Science made", "title": "Science packs made", "lua": "return {']]'}", "every": 5,
        "description": "Science packs crafted in the chosen period.",
        "inputs": [{"key": "period", "label": "Period", "options": ["1m", "1h"]}]}
RES = {"ok": True, "every": 5, "input": {"period": "1m"}, "run_name": "Run 1", "view": [
    {"title": "Made in [color=green]1m[/color]", "columns": ["", "made"],
     "rows": [[{"item": "automation-science-pack"}, 120], [{"item": "logistic-science-pack"}, 12345]]},
    {"columns": ["item"], "rows": {}, "empty": "nothing missing"},
    "total [item=iron-plate] 3 | 4",
]}


def test_snapshot_markdown_is_readable():
    md = m.panel_markdown(SPEC, RES)
    assert "# Science packs made\n\nScience packs crafted in the chosen period." in md and "Run 1" in md and "Period: 1m, 1h" in md
    assert "**Made in 1m**" in md and "| automation-science-pack | 120 |" in md and "| logistic-science-pack | 12.3k |" in md
    assert "_nothing missing_" in md and "total iron-plate 3 \\| 4" in md
    assert 'restore the panel Science made' in md


def test_a_working_panel_is_saved_versioned_and_restored(fake_crew, monkeypatch):
    assert m.library_save(SPEC, RES) == {"ok": True, "slug": "factorio-panel-science-made"}
    changed = {**SPEC, "every": 10}
    assert m.library_save(changed, RES)["ok"]
    import json as j
    d = j.loads((fake_crew / "factorio-panel-science-made.json").read_text())
    assert len(d["versions"]) == 2 and d["tags"] == "factorio-panel" and d["kind"] == "markdown"
    listed = m.library_list()
    assert listed["ok"] and [p["name"] for p in listed["panels"]] == ["science-made"]
    one = listed["panels"][0]
    assert one["title"] == "Science packs made" and one["description"] == "Science packs crafted in the chosen period."
    assert [p["name"] for p in m.library_list("CRAFTED period")["panels"]] == ["science-made"]
    assert m.library_list("logistics")["panels"] == []
    got = m.library_get("Science made")
    assert got["ok"] and got["spec"] == {k: changed[k] for k in ("name", "title", "description", "lua", "every", "inputs")}
    assert m.library_get("factorio-panel-science-made")["spec"]["every"] == 10
    missing = m.library_get("nope")
    assert missing["error"] == "not_saved" and missing["saved"] == ["science-made"]

    # restore sends the saved spec to the game as a normal panel
    sent = {}
    monkeypatch.setattr(m, "bridge_post", lambda path, body, timeout=30: sent.setdefault("q", body) and
                        {"ok": True, "ms": 5, "data": {"ok": True, "name": "Science made", "view": RES["view"]}})
    r = m.panel({"restore": "Science made", "chat": "General"})
    assert r["ok"] and r["saved"]["ok"] and '"every": 10' in sent["q"]["lua"] and '"chat": "General"' in sent["q"]["lua"]


def test_outside_kiro_crew_nothing_is_saved(monkeypatch):
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    assert m.library_save(SPEC, RES)["ok"] is False
    assert m.library_list()["error"] == "no_library"
