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
elif verb == "delete":
    if not os.path.exists(path(rest[0])):
        print("Error: not found", file=sys.stderr); sys.exit(1)
    os.remove(path(rest[0])); print("deleted")
elif verb == "list":
    for f in sorted(os.listdir(store)):
        d = json.load(open(os.path.join(store, f)))
        if opt("--tag") in (d["tags"] or "").split(","):
            print(f"{f[:-5]}   v{len(d['versions'])}   {d['kind']}   [{d['tags']}]  {d['name']}")
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
RES = {"ok": True, "every": 5, "input": {"period": "1m"}, "run_name": "Run 1", "run_id": "d988dac8-0", "view": [
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


def test_link_cells_read_as_their_text_or_position():
    assert m._plain({"text": 9759, "gps": [51, 33], "surface": "nauvis"}) == "9759"
    assert m._plain({"gps": {"x": -20, "y": 14}}) == "-20, 14"
    assert m._plain({"gps": "[gps=1,2,nauvis]"}) == "1, 2"


SLUG = "factorio-panel-d988dac8-0-science-made"


def test_a_working_panel_is_saved_versioned_and_restored(fake_crew, monkeypatch):
    monkeypatch.setattr(m, "current_run_id", lambda: "d988dac8-0")
    assert m.library_save(SPEC, RES) == {"ok": True, "slug": SLUG}
    changed = {**SPEC, "every": 10}
    assert m.library_save(changed, RES)["ok"]
    import json as j
    d = j.loads((fake_crew / f"{SLUG}.json").read_text())
    assert len(d["versions"]) == 2 and d["tags"] == "factorio-panel,factorio-run-d988dac8-0" and d["kind"] == "markdown"
    # another run's panel of the same name is another artifact, outside this run's library
    assert m.library_save(SPEC, {**RES, "run_id": "other-1"})["slug"] == "factorio-panel-other-1-science-made"
    listed = m.library_list()
    assert listed["ok"] and [p["slug"] for p in listed["panels"]] == [SLUG]
    assert len(m.library_list(all_runs=True)["panels"]) == 2
    one = listed["panels"][0]
    assert one["title"] == "Science packs made" and one["description"] == "Science packs crafted in the chosen period."
    assert [p["slug"] for p in m.library_list("CRAFTED period")["panels"]] == [SLUG]
    assert m.library_list("logistics")["panels"] == []
    got = m.library_get("Science made")
    assert got["ok"] and got["spec"] == {k: changed[k] for k in ("name", "title", "description", "lua", "every", "inputs")}
    assert m.library_get(SLUG)["spec"]["every"] == 10
    missing = m.library_get("nope")
    assert missing["error"] == "not_saved" and missing["saved"] == [SLUG]

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


# ── the window's library, served by the dispatcher with the same CLI ──

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dispatcher"))
import factorio_dispatch as d  # noqa: E402


def test_dispatcher_and_mcp_agree_on_names():
    assert d.run_tag("d988dac8-0") == m.run_tag("d988dac8-0") == "factorio-run-d988dac8-0"
    assert d.ARTIFACT_PREFIX == m.ARTIFACT_PREFIX and d.SPEC_FENCE == m.SPEC_FENCE
    # the real `kirocrew artifact list` line
    line = "factorio-panel-requester-missing          v1   markdown    [factorio-panel]  Factorio panel: Requester chests: still missing"
    mt = d.LIST_LINE.match(line)
    assert mt and mt.group(1) == "factorio-panel-requester-missing" and mt.group(2).endswith("still missing")


def test_library_lists_opens_and_deletes_only_this_runs_panels(fake_crew, monkeypatch):
    monkeypatch.setattr(m, "current_run_id", lambda: "d988dac8-0")
    m.library_save(SPEC, RES)
    m.library_save({**SPEC, "name": "elsewhere"}, {**RES, "run_id": "other-1"})
    posted = []
    monkeypatch.setattr(d, "bridge", lambda cfg, method, path, body=None, timeout=40.0: posted.append((path, body)) or {"ok": True})
    ask = {"kind": "library", "id": "lib-1", "run_id": "d988dac8-0"}
    r = d.library({}, {**ask, "op": "list"})
    assert r["ok"] and r["panels"] == [{"slug": SLUG, "title": "Science packs made"}], r
    assert posted[-1][0] == "/library_result" and posted[-1][1]["id"] == "lib-1"
    r = d.library({}, {**ask, "op": "open", "slug": SLUG})
    assert r["ok"] and r["spec"]["name"] == "Science made" and r["spec"]["lua"] == SPEC["lua"], r
    r = d.library({}, {**ask, "op": "open", "slug": "factorio-panel-other-1-elsewhere"})
    assert not r["ok"] and "no saved panel" in r["error"], "another run's panel is not in this library"
    r = d.library({}, {**ask, "op": "delete", "slug": SLUG})
    assert r["ok"] and r["panels"] == [] and not (fake_crew / f"{SLUG}.json").exists()
    assert (fake_crew / "factorio-panel-other-1-elsewhere.json").exists()
