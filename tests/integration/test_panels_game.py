"""Level 2: live panels through MCP (factorio_panel) against headless 2.1 with the bridge.

Not e2e: the test writes the panel bodies an agent would write. It proves the tool, the mod's
refresh loop and the game APIs the two example panels need, on known planted numbers.
"""
from __future__ import annotations

import time

from conftest import wait_until
from panel_scenarios import (LOGI_SETUP, REQUESTER_CONTENTS, REQUESTS, SCIENCE, SCIENCE_SETUP, STORED,
                             add_robots, rows_about)

def panel(mcp, **args):
    """factorio_panel through MCP (Mcp.tool's own first argument is called `name` too)."""
    return mcp.tool(**{"name": "factorio_panel"}) if not args else _call(mcp, args)


def _call(mcp, args):
    import json
    r = mcp.rpc("tools/call", {"name": "factorio_panel", "arguments": args})
    payload = json.loads(r["content"][0]["text"])
    assert r["isError"] == (not payload.get("ok")), r
    return payload


SCIENCE_BODY = """
local idx = defines.flow_precision_index[input.period]
local st = game.forces.player.get_item_production_statistics(surface)
local rows, packs = {}, {}
-- science packs = what labs accept (modded ones too; their prototype type differs between versions)
for _, lab in pairs(prototypes.get_entity_filtered{{filter = "type", type = "lab"}}) do
  for _, n in pairs(lab.lab_inputs or {}) do packs[n] = true end
end
for name in pairs(packs) do
  local n = st.get_flow_count{name = name, category = "input", precision_index = idx, count = true}
  if n > 0 then rows[#rows + 1] = {{item = name}, n} end
end
table.sort(rows, function(a, b) return a[2] > b[2] end)
return {{columns = {"", "made in " .. input.period}, rows = rows, empty = "no science made"}}
"""

LOGI_BODY = """
local want, net_of = {}, {}
for _, net in pairs(game.forces.player.logistic_networks[surface.name] or {}) do
  for _, pt in pairs(net.requester_points) do
    local e = pt.owner
    if e.valid and e.type == "logistic-container" then
      for _, f in pairs(pt.filters or {}) do
        local w = want[f.name] or {asked = 0, have = 0, coming = 0, net = 0}
        w.asked = w.asked + f.count
        w.have = w.have + e.get_item_count(f.name)
        w.net = net.get_item_count(f.name)
        want[f.name] = w
      end
      for _, it in pairs(pt.targeted_items_deliver or {}) do
        if want[it.name] then want[it.name].coming = want[it.name].coming + it.count end
      end
    end
  end
end
local zero, waiting = {}, {}
for name, w in pairs(want) do
  local short = w.asked - w.have
  if short > 0 then
    local row = {{item = name}, short, w.coming, w.net}
    if w.net == 0 then zero[#zero + 1] = row else waiting[#waiting + 1] = row end
  end
end
return {
  {title = "None in the network", columns = {"", "short", "coming", "in network"}, rows = zero, empty = "nothing"},
  {title = "Waiting for delivery", columns = {"", "short", "coming", "in network"}, rows = waiting, empty = "nothing"},
}
"""


def test_science_panel_by_period(stack, mcp):
    assert mcp.tool("factorio_query", lua=SCIENCE_SETUP)["ok"]
    r = panel(mcp, name="science", title="Science", lua=SCIENCE_BODY, every=1,
                 inputs=[{"key": "period", "label": "Period", "options": ["one_minute", "ten_minutes", "one_hour"],
                          "default": "one_minute"}])
    assert r["ok"], r

    seen = {}

    def planted_shown():
        seen["panel"] = stack.rcon.lua('return remote.call("kc", "panels")')[0]
        c = {k: rows_about(seen["panel"]["view"], k) for k in SCIENCE}
        return c if all(c[k] and c[k][0][1][-1] == v for k, v in SCIENCE.items()) else None

    # the planted flows land in the statistics within a sample; the panel refreshes by itself
    try:
        got = wait_until(planted_shown, timeout=20, step=0.5, what="the science panel showing the planted counts")
    except AssertionError:
        raise AssertionError(f"panel never showed {SCIENCE}: {seen.get('panel')}")
    print("science rows:", got)
    # the same packs over a longer window are the same count (all made just now)
    stack.rcon.lua('return remote.call("kc", "panel_input", "science", "period", "one_hour")')
    view = stack.rcon.lua('return remote.call("kc", "panels")')[0]
    assert view["input"]["period"] == "one_hour"
    assert rows_about(view["view"], "automation-science-pack")[0][1][-1] == 120, view


def test_logistics_panel_live(stack, mcp):
    made = mcp.tool("factorio_query", lua=LOGI_SETUP)
    assert made["ok"] and made["data"]["network"], made
    r = panel(mcp, name="missing", title="Missing in logistics", lua=LOGI_BODY, every=1)
    assert r["ok"], r
    zero, waiting = r["view"][0]["rows"], r["view"][1]["rows"]
    names = lambda rows: sorted(row[0]["item"] for row in (rows if isinstance(rows, list) else []))  # noqa: E731
    assert names(zero) == ["copper-plate", "steel-plate"], r["view"]
    assert names(waiting) == ["iron-plate"], r["view"]
    iron = [row for row in waiting if row[0]["item"] == "iron-plate"][0]
    assert iron[1] == REQUESTS["iron-plate"] and iron[3] == STORED["iron-plate"], iron

    # robots arrive: the iron is delivered and the panel drops it by itself
    assert add_robots(lambda b: mcp.tool("factorio_query", lua=b)["data"], made["data"]["roboport"]) == 20

    def delivered():
        got = mcp.tool("factorio_query", lua=REQUESTER_CONTENTS)["data"]
        return got if isinstance(got, dict) and got.get("iron-plate", 0) >= REQUESTS["iron-plate"] else None
    wait_until(delivered, timeout=90, step=1, what="robots delivering the requested iron")

    def panel_without_iron():
        v = stack.rcon.lua('return remote.call("kc", "panels")')
        p = [x for x in v if x["name"] == "missing"][0]
        w = p["view"][1]["rows"]
        return p if (w == {} or w == []) else None
    p = wait_until(panel_without_iron, timeout=10, step=0.5, what="the panel dropping delivered iron")
    assert names(p["view"][0]["rows"]) == ["copper-plate", "steel-plate"], p["view"]


def test_bad_body_is_reported_and_kept(stack, mcp):
    r = panel(mcp, name="broken", lua="return {")
    assert not r["ok"] and "compile" in r["error"], r
    r = panel(mcp, name="broken", lua="return nosuch.x")
    assert not r["ok"] and "nosuch" in r["error"], r
    listed = mcp.tool("factorio_query", lua="return kc.panels()")["data"]
    assert any(p["name"] == "broken" and p["error"] for p in listed), listed
    gone = panel(mcp, name="broken", remove=True)
    assert gone["ok"], gone
    # a body with ]] and Cyrillic survives the trip into Lua
    r = panel(mcp, name="quote", lua="return {'a]]b ]==] ящик'}")
    assert r["ok"] and r["view"] == ["a]]b ]==] ящик"], r
    for name in ("quote", "science", "missing"):
        panel(mcp, name=name, remove=True)


def test_library_round_trip_through_the_bridge(stack):
    """The window's Library: request -> bridge queue -> (the test plays the dispatcher) -> result -> panel."""
    from conftest import http
    req = stack.rcon.lua('return remote.call("kc", "library_request", "list")')
    asks = wait_until(lambda: http("GET", "/asks?wait=2&lease=5")["asks"], timeout=10, what="the library request at the bridge")
    lib = [a for a in asks if a.get("kind") == "library"]
    assert lib and lib[0]["id"] == req["id"] and lib[0]["op"] == "list" and lib[0]["run_id"] == stack.mod_state()["run_id"], asks
    http("POST", "/asks/done", {"ids": [a["id"] for a in asks]})
    assert not any(t.get("state") == "thinking" for t in (stack.mod_state()["tabs"] or {}).values()), "a library request is no turn"
    slug = "factorio-panel-l2-saved"
    http("POST", "/library_result", {"id": req["id"], "run_id": lib[0]["run_id"], "op": "list", "ok": True,
                                     "panels": [{"slug": slug, "title": "Сохранённая"}]})
    got = wait_until(lambda: (lambda l: l if l.get("panels") else None)(stack.rcon.lua('return remote.call("kc", "library")')),
                     timeout=10, what="the list in the mod")
    assert got["panels"] == [{"slug": slug, "title": "Сохранённая"}]
    # open: a long spec (several UDP packets) becomes a live panel
    big = "return {" + ", ".join(f"'строка {i}'" for i in range(400)) + "}"
    req = stack.rcon.lua(f'return remote.call("kc", "library_request", "open", "{slug}")')
    http("GET", "/asks?wait=2&lease=5")
    http("POST", "/library_result", {"id": req["id"], "run_id": lib[0]["run_id"], "op": "open", "ok": True,
                                     "spec": {"name": "l2-saved", "title": "Сохранённая", "lua": big, "every": 2}})
    p = wait_until(lambda: [x for x in stack.rcon.lua('return remote.call("kc", "panels")') if x["name"] == "l2-saved"],
                   timeout=10, what="the opened panel")
    assert len(p[0]["view"]) == 400 and p[0]["view"][-1] == "строка 399"
    # an answer for another run is dropped
    http("POST", "/library_result", {"id": "x", "run_id": "another-run", "op": "list", "ok": True, "panels": []})
    time.sleep(1)
    assert stack.rcon.lua('return remote.call("kc", "library")')["panels"] == [{"slug": slug, "title": "Сохранённая"}]
    stack.rcon.lua('return remote.call("kc", "panel_remove", "l2-saved")')
