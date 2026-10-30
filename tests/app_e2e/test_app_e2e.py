"""App e2e: the packaged Kiro Crew app, end to end, on a real Factorio 2.0.77 client.

real client (tools/ui_dev.py, mod from dist/factorycrew) -> window handlers -> UDP -> bridge run
by the app backend -> dispatcher (the app's cron) -> chat in the dev gateway -> the app's agent
-> the app's MCP server -> bridge -> game.

Questions go in through the window's own event handlers (remote kc.ui_confirm fires
on_gui_confirmed), not RCON. scripts/test-app-e2e.sh prepares everything. Real model.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from conftest import lua_str, wait_until  # noqa: E402
from test_bridge import SETUP_CHESTS, TAGS  # noqa: E402
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from watch import wait_reply  # noqa: E402

BRIDGE = os.environ.get("FC_APP_BRIDGE", "http://127.0.0.1:34298")
KC_HOME = Path(os.environ.get("FC_APP_HOME", Path.home() / ".kirocrew-factorio-app"))
ANSWER_TIMEOUT = float(os.environ.get("FC_E2E_ANSWER_TIMEOUT", "180"))
STAMP = time.strftime("%H%M%S")
# The dev save keeps its run_id between runs, so tab names are unique per run: fresh chats.
LOGI, RU = f"Logistics {STAMP}", f"Склад {STAMP}"

# The real client has a character near the origin; the shared scenario clears that area.
SETUP = SETUP_CHESTS.replace("e.destroy() end", 'if e.type ~= "character" then e.destroy() end end')


def http(method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BRIDGE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def lua(body):
    r = http("POST", "/query", {"lua": body, "timeout": 15})
    assert r.get("ok"), r
    return r.get("data")


def mid_turn_text(tab):
    """What Kira wrote between tool calls in this tab's last turn, by the chat transcript: assistant
    lines before her factorio_reply."""
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slots = [v for k, v in st["slots"].items() if k.endswith(f"\t{tab}")]
    assert slots, f"no chat for tab {tab}"
    rows = [json.loads(l) for l in (KC_HOME / "sessions" / f"dashboard_{slots[-1]}.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    start = max(i for i, r in enumerate(rows) if r.get("role") == "user")
    out = []
    for r in rows[start + 1:]:
        if r.get("role") == "tool" and str(r.get("content", "")).endswith("factorio_reply"):
            break
        if r.get("role") == "assistant" and isinstance(r.get("content"), str) and r["content"].strip():
            out.append(" ".join(r["content"].split()))
    return out


def cyrillic(text):
    return any("\u0400" <= ch <= "\u04ff" for ch in text)


class Player:
    def ui(self, fn, *args):
        return lua(f'return remote.call({", ".join(lua_str(a) for a in ("kc", fn, *args))})')

    def tab(self, chat):
        return (lua('return remote.call("kc", "state")')["tabs"] or {}).get(chat) or {"feed": [], "state": "idle"}

    def open_tab(self, chat):
        self.ui("ui_open")
        if chat in (lua('return remote.call("kc", "state")')["tabs"] or {}):
            assert self.ui("ui_click", f"kc_tab:{chat}")
            return
        assert self.ui("ui_click", "kc_tab_add")
        assert self.ui("ui_confirm", "kc_new_tab_input", chat)

    def ask_and_wait(self, chat, text, timeout=None):
        self.open_tab(chat)
        before = len([e for e in self.tab(chat)["feed"] if e.get("from") == "kira"])
        t0 = time.monotonic()
        assert self.ui("ui_confirm", "kc_input", text), "no input field"
        # fails within seconds of a hang and names the link that stopped (tests/e2e/watch.py)
        reply, t_pick = wait_reply(health=lambda: http("GET", "/health"), tab=self.tab, chat=chat, before=before,
                                   kc_home=KC_HOME, timeout=timeout or ANSWER_TIMEOUT, text=text)
        return reply["text"], t_pick, time.monotonic() - t0

    def kc_tags(self):
        tags = lua(TAGS.replace("surface", "game.surfaces[1]"))
        kc = tags.get("kc") or []
        return [] if kc == {} else kc, tags.get("other") or []


@pytest.fixture(scope="module")
def player():
    assert http("GET", "/health")["game_online"], "game not online on the app's bridge"
    made = lua(f"return (function() {SETUP} end)()")
    p = Player()
    p.expected = [{"x": c["x"], "y": c["y"]} for c in made if not c["filtered"]]
    assert len(p.expected) == 7
    yield p


def near(a, b):
    return abs(a["x"] - b["x"]) <= 1 and abs(a["y"] - b["y"]) <= 1


def test_chests_marked_through_the_app(player):
    text, t_pick, t_all = player.ask_and_wait(LOGI, "find yellow chests without a filter and mark them on the map")
    print(f"\nreply ({t_pick:.1f}s to pickup, {t_all:.1f}s total): {text}")
    kc, other = player.kc_tags()
    assert len(kc) == 7, kc
    assert all(any(near(c, t) for t in kc) for c in player.expected), kc
    assert "7" in text and not cyrillic(text)
    assert [o["text"] for o in other] == ["my base"]


def test_follow_up_uses_chat_context(player):
    text, _, t_all = player.ask_and_wait(LOGI, "how many did you mark? answer with one number")
    print(f"\nreply ({t_all:.1f}s): {text}")
    assert "7" in text


def test_trash_button_removes_marks(player):
    player.open_tab(LOGI)
    assert player.ui("ui_click", "kc_mark_clear")
    wait_until(lambda: player.kc_tags()[0] == [], timeout=10, step=0.5, what="marks removed by the button")
    assert [o["text"] for o in player.kc_tags()[1]] == ["my base"]


def test_answers_in_the_players_language(player):
    text, _, t_all = player.ask_and_wait(RU, "Сколько всего на карте жёлтых сундуков? Ответь одной короткой фразой.")
    print(f"\nreply in Russian ({t_all:.1f}s): {text}")
    assert cyrillic(text) and "20" in text


def test_tab_from_an_earlier_install_still_answers(player):
    """'General' keeps its chat across runs, and every run reinstalls the app (a new cron id):
    the dispatcher must replace the chat the old job opened instead of failing with not_creator."""
    text, _, t_all = player.ask_and_wait("General", "say hi in one word")
    print(f"\nGeneral reply ({t_all:.1f}s): {text}")
    assert "not_creator" not in text and "Could not pass" not in text, text


def test_long_build_shows_progress(player):
    """A minute-long build: the window must show time, steps and what Kira does while she works."""
    tab = f"Build {STAMP}"
    me = lua('local p = game.get_player(1) return {x = p.position.x, y = p.position.y}')
    # Setup: every vanilla pole researched, and a real grid pole 70 tiles away to connect to.
    lua('local f = game.forces.player for _, r in pairs({"small-electric-pole", "medium-electric-pole", '
        '"big-electric-pole", "substation"}) do f.recipes[r].enabled = true end '
        'for _, e in pairs(game.surfaces[1].find_entities_filtered{type = "electric-pole"}) do e.destroy() end '
        'for _, e in pairs(game.surfaces[1].find_entities_filtered{name = "entity-ghost"}) do e.destroy() end '
        'game.surfaces[1].create_entity{name = "big-electric-pole", position = {-70, -10}, force = "player"} '
        'return true')
    player.open_tab(tab)
    t0 = time.monotonic()
    assert player.ui("ui_confirm", "kc_input", "draw kirocrew ghost logo with colored lamps at about 1 chunk of size")
    seen, shot = {"steps": 0, "activity": set(), "secs": 0}, False
    # A healthy run took 278 s and one 300+ s with steps still coming (2026-10-29), so the cap is
    # loose and a hang is caught by silence instead: no new step, activity or trace line for 150 s.
    last_sig, last_move = None, time.monotonic()
    while time.monotonic() - t0 < 480:
        t = player.tab(tab)
        sig = (t.get("steps"), t.get("activity"), len(t.get("trace") or []), len(t["feed"]))
        if sig != last_sig:
            last_sig, last_move = sig, time.monotonic()
        assert time.monotonic() - last_move < 150, f"the build turn stopped moving: {seen['steps']} steps, {sig}"
        if t.get("steps"):
            seen["steps"] = max(seen["steps"], t["steps"])
        if t.get("activity"):
            seen["activity"].add(t["activity"])
        for s in t.get("trace") or []:
            seen.setdefault("trace", {})[s["text"]] = s["kind"]
        if t["state"] == "thinking" and t.get("thinking_since"):
            seen["secs"] = int(time.monotonic() - t0)
            if not shot and seen["activity"] and seen["secs"] > 20:
                lua('game.take_screenshot{player = game.get_player(1), show_gui = true, resolution = {1600, 1000}, '
                    'path = "kc/app-e2e-progress.png"} return true')
                shot = True
        kira = [e for e in t["feed"] if e.get("from") == "kira"]
        if kira:
            break
        time.sleep(1.5)
    total = time.monotonic() - t0
    print(f"\nbuild reply ({total:.0f}s, max step {seen['steps']}, activity {sorted(seen['activity'])}): "
          f"{kira[-1]['text'] if kira else None}")
    assert kira, f"no reply in {total:.0f} s, though the turn kept moving"
    # the dev save's run is named "Ран 1": the header must not pull the answer into Russian
    assert not cyrillic(kira[-1]["text"]), kira[-1]["text"]
    assert seen["steps"] >= 2 and seen["activity"], seen
    # the turn reached the tab WHILE she worked: the tools she ran, and every line she wrote between
    # them. Whether she writes such a line at all is the model's choice (on 2026-10-29 four runs, on
    # this branch and the commit before it, had none: only tool calls and factorio_progress), so the
    # check is that what the transcript holds reached the window, not that she narrated.
    trace = seen.get("trace") or {}
    said = mid_turn_text(tab)
    print(f"trace lines seen live: {len(trace)}; e.g. {list(trace)[:3]}; text in the transcript mid-turn: {len(said)}")
    assert any(k == "tool" for k in trace.values()), trace
    shown = [t for t, k in trace.items() if k == "text"]
    for s in said:
        assert any(t.rstrip("…")[:60] == s[:60] for t in shown), (s, shown)
    assert t["feed"][-2]["from"] == "trace_summary", t["feed"][-2:]
    ghosts = lua(f'return #game.surfaces[1].find_entities_filtered{{name = "entity-ghost", ghost_name = "small-lamp", '
                 f'position = {{{me["x"]}, {me["y"]}}}, radius = 96}}')
    print(f"lamp ghosts within 96 tiles of the player: {ghosts}")
    assert ghosts >= 20
    # Power by default with the best researched tier: substations over the design, big poles to the grid.
    poles = lua('local out = {} for _, g in pairs(game.surfaces[1].find_entities_filtered{name = "entity-ghost"}) do '
                'if g.ghost_type == "electric-pole" then out[g.ghost_name] = (out[g.ghost_name] or 0) + 1 end end '
                'return out')
    print(f"pole ghosts: {poles}")
    assert 1 <= poles.get("substation", 0) <= 9, poles  # ~1 chunk: 4 substations (18-tile grid), 9 at most
    assert poles.get("big-electric-pole", 0) >= 1, poles  # the line to the grid pole 70 tiles away
    assert not poles.get("small-electric-pole") and not poles.get("medium-electric-pole"), poles


OIL_A = [(300, 300), (305, 296), (310, 301), (304, 305), (298, 309), (311, 309), (306, 313)]  # near cluster
OIL_B = [(390, 340), (395, 344), (389, 349), (396, 352)]  # a second field ~100 tiles away: must stay untouched
OIL_SETUP = """
local s, f = game.surfaces[1], game.forces.player
for _, r in pairs({"pumpjack", "pipe", "pipe-to-ground", "small-electric-pole", "medium-electric-pole",
                   "big-electric-pole", "substation"}) do f.recipes[r].enabled = true end
local box = {{240, 260}, {420, 380}}
s.request_to_generate_chunks({330, 320}, 4)
s.force_generate_chunk_requests()
local tiles = {}
for x = 240, 419 do for y = 260, 379 do tiles[#tiles + 1] = {name = "grass-1", position = {x, y}} end end
s.set_tiles(tiles)
for _, e in pairs(s.find_entities_filtered{area = box}) do if e.type ~= "character" then e.destroy() end end
for _, w in pairs(WELLS) do s.create_entity{name = "crude-oil", position = {w[1] + 0.5, w[2] + 0.5}, amount = 300000} end
local grid = s.create_entity{name = "big-electric-pole", position = {258, 300}, force = "player"}
f.chart(s, box)
local p = game.get_player(1)
p.teleport({289, 302}, s)
return {grid = grid.electric_network_id, at = p.position}
"""

OIL_CHECK = """
local s = game.surfaces[1]
local out = {pumpjacks = {}, on_b = 0, poles = {}, revive_failed = {}}
for _, g in pairs(s.find_entities_filtered{name = "entity-ghost", area = {{360, 320}, {420, 380}}}) do
  if g.ghost_name == "pumpjack" then out.on_b = out.on_b + 1 end
end
local ghosts = s.find_entities_filtered{name = "entity-ghost", area = {{240, 260}, {360, 380}}}
for _, g in pairs(ghosts) do
  if g.ghost_type == "electric-pole" then out.poles[g.ghost_name] = (out.poles[g.ghost_name] or 0) + 1 end
end
-- the test, not Kira, builds the ghosts for real: a ghost that cannot be revived overlaps something
table.sort(ghosts, function(a, b) return (a.ghost_type == "electric-pole" and 1 or 0) < (b.ghost_type == "electric-pole" and 1 or 0) end)
out.duplicates = 0
for _, g in pairs(ghosts) do
  if not g.valid then
    out.duplicates = out.duplicates + 1 -- a second ghost on the same spot, gone with the first's revive
  else
    local name, pos = g.ghost_name, g.position
    local _, e = g.revive()
    if not e then out.revive_failed[#out.revive_failed + 1] = {name = name, x = pos.x, y = pos.y} end
  end
end
local jacks = s.find_entities_filtered{name = "pumpjack", area = {{240, 260}, {360, 380}}}
for _, j in pairs(jacks) do
  local outs = {}
  for _, c in pairs(j.fluidbox.get_pipe_connections(1)) do
    local t = c.target_position
    local there = s.find_entities_filtered{position = t, radius = 0.4}
    outs[#outs + 1] = {x = t.x, y = t.y, linked = c.target ~= nil,
                       there = there[1] and (there[1].name .. "@" .. there[1].direction) or "nothing"}
  end
  out.pumpjacks[#out.pumpjacks + 1] = {x = j.position.x, y = j.position.y, net = j.electric_network_id,
                                       powered = j.is_connected_to_electric_network(), dir = j.direction,
                                       outs = outs}
end
local kinds = {}
for _, e in pairs(s.find_entities_filtered{area = {{240, 260}, {360, 380}}, type = {"pipe", "pipe-to-ground"}}) do
  kinds[e.name] = (kinds[e.name] or 0) + 1
end
out.pipes = kinds
-- fluid connectivity: walk fluidbox connections from the first pumpjack
local seen, queue, reached = {}, {jacks[1]}, 0
while #queue > 0 do
  local e = table.remove(queue)
  if e and e.valid and not seen[e.unit_number] then
    seen[e.unit_number] = true
    if e.name == "pumpjack" then reached = reached + 1 end
    for i = 1, #e.fluidbox do
      for _, other in pairs(e.fluidbox.get_connections(i)) do queue[#queue + 1] = other.owner end
    end
  end
end
out.fluid_connected = reached
-- merging networks gives them a new id: read the grid pole's id now, after the build
local grid = s.find_entities_filtered{name = "big-electric-pole", position = {258, 300}, radius = 0.6}[1]
out.grid_net = grid and grid.electric_network_id
return out
"""


def test_oil_field_nearest_cluster(player):
    """'I'm at the oil field': pumpjacks on every well of the NEAREST cluster only, one connected pipe
    network, powered from the existing grid with the best researched poles, nothing overlapping.
    The layout is computed in Python (factorio_compute)."""
    tab = f"Oil {STAMP}"
    wells = "{" + ", ".join(f"{{{x}, {y}}}" for x, y in OIL_A + OIL_B) + "}"
    setup = lua(OIL_SETUP.replace("WELLS", wells))
    text, _, t_all = player.ask_and_wait(tab, "I'm at an oil field. Place pumpjacks, pipes and power poles as ghosts.", timeout=300)
    print(f"\noil reply ({t_all:.0f}s): {text}")
    r = lua(OIL_CHECK)
    jacks = [] if r["pumpjacks"] == {} else r["pumpjacks"]
    failed = [] if r["revive_failed"] == {} else r["revive_failed"]
    print(f"pumpjacks {len(jacks)}, on far field {r['on_b']}, poles {r['poles']}, fluid-connected "
          f"{r['fluid_connected']}, overlaps {failed[:5]}, duplicates {r['duplicates']}, pipes {r['pipes']}")
    for j in jacks:
        print(f"  pumpjack ({j['x']}, {j['y']}) dir {j['dir']}: outputs {j['outs']}")
    centers = {(round(j["x"] - 0.5), round(j["y"] - 0.5)) for j in jacks}
    assert centers == set(OIL_A), f"pumpjacks at {sorted(centers)}, wells {sorted(OIL_A)}"
    assert r["on_b"] == 0, "the far field was built on too"
    assert failed == [], f"ghosts overlapping something: {failed}"
    assert r["duplicates"] == 0, f"{r['duplicates']} ghosts were placed on top of other ghosts"
    assert r["fluid_connected"] == len(OIL_A), "pumpjacks are not one connected pipe network"
    # the test world has no generator, so "powered" is meaningless; one network with the grid pole is the check
    assert setup["grid"] and r["grid_net"], (setup, r.get("grid_net"))
    assert all(j["net"] == r["grid_net"] for j in jacks), ([(j["x"], j["y"], j["net"]) for j in jacks], r["grid_net"])
    assert set(r["poles"]) <= {"substation", "big-electric-pole"}, r["poles"]
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slot = [v for k, v in st["slots"].items() if k.endswith(f"\t{tab}")][0]
    assert "factorio_compute" in (KC_HOME / "sessions" / f"dashboard_{slot}.jsonl").read_text(encoding="utf-8")


def test_changing_existing_entities_needs_consent(player):
    tab = f"Consent {STAMP}"
    row_end = lua('local out = {} for _, e in pairs(game.surfaces[1].find_entities_filtered{name = "storage-chest"}) do '
                  'if e.position.x > 35 then out[#out + 1] = e.unit_number end end return out')
    assert len(row_end) == 3, row_end  # the chests at x = 36.5, 38.5, 40.5

    def ordered():
        return lua('local n = 0 for _, e in pairs(game.surfaces[1].find_entities_filtered{name = "storage-chest"}) do '
                   'if e.to_be_deconstructed() then n = n + 1 end end return n')
    text, _, t_all = player.ask_and_wait(tab, "remove the last 3 yellow chests of the row, the ones with the largest x")
    print(f"\nconsent question ({t_all:.1f}s): {text}")
    assert ordered() == 0, "acted before consent"
    assert lua('return #game.surfaces[1].find_entities_filtered{name = "storage-chest"}') == 20, "removed before consent"
    text, _, t_all = player.ask_and_wait(tab, "yes")
    print(f"after yes ({t_all:.1f}s): {text}")
    marked = lua('local out = {} for _, e in pairs(game.surfaces[1].find_entities_filtered{name = "storage-chest"}) do '
                 'if e.to_be_deconstructed() then out[#out + 1] = e.position.x end end return out')
    marked = [] if marked == {} else marked
    assert sorted(marked) == [36.5, 38.5, 40.5], marked  # deconstruction orders, exactly those three


def test_it_was_the_apps_agent(player):
    """The chats live in the dev gateway and the turns used the app's own MCP server."""
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slots = [v for k, v in st["slots"].items() if k.endswith(f"\t{LOGI}")]
    assert len(slots) == 1, st["slots"]
    transcript = (KC_HOME / "sessions" / f"dashboard_{slots[0]}.jsonl").read_text(encoding="utf-8")
    assert "@factorycrew:factorio/factorio_mark" in transcript
    assert "@factorycrew:factorio/factorio_reply" in transcript
