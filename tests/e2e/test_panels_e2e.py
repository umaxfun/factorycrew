"""Level 3: Kira builds live panels from a question in the game, and they stay live.

Full cycle as in test_e2e.py (game -> bridge -> dispatcher -> Kiro Crew -> Kira -> MCP -> game).
No panel code exists for these requests: Kira writes the body herself with factorio_panel.
Checks are made against numbers the test planted in the game, and against changes the test makes
after Kira's turn ended, which only a self-refreshing panel can show. Real model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
sys.path.insert(0, str(ROOT / "mcp"))
from conftest import lua_str, wait_until  # noqa: E402
from panel_scenarios import (LOGI_SETUP, REQUESTER_CONTENTS, REQUESTS, SCIENCE, SCIENCE_SETUP,  # noqa: E402
                             add_robots, rows_about)
from test_e2e import KC_HOME, Player, cyrillic, http  # noqa: E402

LONG = 360


@pytest.fixture(scope="module")
def kira():
    assert http("/health")["game_online"], "stand is not up: run scripts/test-e2e.sh"
    p = Player()
    p.lua(f'remote.call("kc", "set_run_name", {lua_str(time.strftime("e2e panels %m-%d %H:%M"))}) return true')
    # the e2e gateway keeps its artifacts between runs: start from an empty panel library, so
    # "my science panel" means the one this run made (the e2e home belongs to this worktree's slot)
    listed = artifact("list", "--tag", "factorio-panel")
    for slug in set(re.findall(r"factorio-panel-[a-z0-9-]+", listed.stdout)):
        artifact("delete", slug)
    assert "factorio-panel-" not in artifact("list", "--tag", "factorio-panel").stdout
    yield p
    p.rcon.close()


def panels(kira):
    got = kira.lua('return remote.call("kc", "panels")')
    return [] if got in (None, {}) else got


def new_panel(kira, before):
    names = {p["name"] for p in before}
    made = [p for p in panels(kira) if p["name"] not in names]
    assert len(made) == 1, f"expected one new panel, got {[p['name'] for p in made]}"
    return made[0]


def current(kira, name):
    return [p for p in panels(kira) if p["name"] == name][0]


def transcript(tab):
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slots = [v for k, v in st["slots"].items() if k.endswith(f"\t{tab}")]
    assert slots, f"no chat for tab {tab}"
    return (KC_HOME / "sessions" / f"dashboard_{slots[-1]}.jsonl").read_text(encoding="utf-8")


def ask(kira, tab, text):
    # the e2e harness has no long-answer override; patch the shared wait for these long turns
    import test_e2e
    old, test_e2e.ANSWER_TIMEOUT = test_e2e.ANSWER_TIMEOUT, max(test_e2e.ANSWER_TIMEOUT, LONG)
    try:
        return kira.ask_and_wait(tab, text)
    finally:
        test_e2e.ANSWER_TIMEOUT = old


def artifact(*argv):
    """The e2e gateway's artifacts (the store the player sees in the dashboard)."""
    env = dict(os.environ, KIROCREW_HOME=str(KC_HOME))
    if os.environ.get("FC_KC_PORT"):
        env["KIROCREW_PORT"] = os.environ["FC_KC_PORT"]
    return subprocess.run(["kirocrew", "artifact", *argv], capture_output=True, text=True, timeout=60, env=env)


def saved_panel(name):
    import factorio_mcp  # noqa: E402  (same slug rule as the tool)
    slug = factorio_mcp.artifact_slug(name, http("/health")["run_id"])  # the run's own library
    shown = artifact("show", slug)
    assert shown.returncode == 0, f"panel {name} not saved as artifact {slug}: {shown.stderr or shown.stdout}"
    return slug, shown.stdout


def option_like(options, *patterns):
    for o in options:
        if any(re.search(p, o, re.I) for p in patterns):
            return o
    return None


def test_science_panel_with_a_period_filter(kira):
    kira.lua(SCIENCE_SETUP)  # packs made "just now", so Kira sees real numbers while exploring
    before = panels(kira)
    tab = time.strftime("Science %H:%M:%S")
    text, _, t = ask(kira, tab, (
        "Make me a live panel with science production: how many of each science pack was made, "
        "with a time filter (last minute, last 10 minutes, last hour)."))
    print(f"\nscience panel reply ({t:.0f}s): {text}")
    assert not cyrillic(text)
    p = new_panel(kira, before)
    assert not p.get("error"), p
    options = [o for inp in (p["inputs"] if p["inputs"] != {} else []) for o in inp["options"]]
    print("inputs:", p["inputs"])
    key = [inp["key"] for inp in p["inputs"]][0] if p["inputs"] != {} else None
    hour = option_like(options, r"\b1\s*h|one_hour|hour")
    minute = option_like(options, r"^1\s*m|one_minute|^(last\s+)?(1\s+)?min")
    assert key and hour and minute, f"no minute/hour period in the panel's inputs: {p['inputs']}"
    assert "factorio_panel" in transcript(tab)
    slug, md = saved_panel(p["name"])
    print(f"artifact {slug}:\n{md[:800]}")
    assert "automation-science-pack" in md and "120" in md, "the snapshot in the artifact shows the planted numbers"
    assert "kc-panel" in md and "get_flow_count" in md, "the artifact keeps the panel code"
    desc = (p.get("description") or "").strip()
    assert 15 <= len(desc) <= 140, f"the panel needs a short one-line description for its artifact: {desc!r}"
    assert desc in md, "the artifact carries the description"

    # after the turn: more packs are made; the panel shows them by itself
    kira.lua(SCIENCE_SETUP)
    assert kira.lua(f'return remote.call("kc", "panel_input", {lua_str(p["name"])}, {lua_str(key)}, {lua_str(hour)})')
    seen = {}

    def doubled():
        seen["p"] = current(kira, p["name"])
        rows = {k: rows_about(seen["p"]["view"], k) for k in SCIENCE}
        return rows if all(r and 2 * v in r[0][1] for (k, r), v in zip(rows.items(), SCIENCE.values())) else None
    try:
        rows = wait_until(doubled, timeout=30, step=1, what="the hour view counting both batches")
    except AssertionError:
        raise AssertionError(f"expected {({k: 2 * v for k, v in SCIENCE.items()})} for {hour}: {seen.get('p')}")
    print("hour rows:", rows)


def test_logistics_panel_shows_what_is_missing_live(kira):
    made = kira.lua(LOGI_SETUP)
    assert made["network"], made
    before = panels(kira)
    tab = time.strftime("Logistics %H:%M:%S")
    text, _, t = ask(kira, tab, (
        "Make a live panel of what my requester chests are still missing: items requested but not delivered "
        "yet, and separately the requested items the logistic network has none of at all."))
    print(f"\nlogistics panel reply ({t:.0f}s): {text}")
    p = new_panel(kira, before)
    assert not p.get("error"), p
    assert 15 <= len((p.get("description") or "").strip()) <= 140, f"description missing or too long: {p.get('description')!r}"

    def blocks_of(view, item):
        return {i for i, b in enumerate(view) if rows_about([b], item)}
    view = p["view"]
    print("view:", json.dumps(view, ensure_ascii=False)[:1500])
    iron, copper, steel = (blocks_of(view, k) for k in ("iron-plate", "copper-plate", "steel-plate"))
    assert iron and copper and steel, "every requested item that is missing must be on the panel"
    # copper and steel may also count as "not delivered"; the "none in the network" list must
    # hold them and not the iron, which storage has
    assert (copper & steel) - iron, "a separate list of items the network has none of (copper, steel, not iron)"
    assert any(40 in nums for _, nums in rows_about(view, "copper-plate")), "copper short by 40"
    assert any(20 in nums for _, nums in rows_about(view, "steel-plate")), "steel short by 20"
    assert "factorio_panel" in transcript(tab)

    # after the turn: robots arrive and deliver the iron; the panel drops it without being asked
    assert add_robots(kira.lua, made["roboport"]) == 20
    wait_until(lambda: (kira.lua(REQUESTER_CONTENTS) or {}).get("iron-plate", 0) >= REQUESTS["iron-plate"],
               timeout=180, step=2, what="robots delivering the iron")
    seen = {}

    def iron_gone():
        seen["p"] = current(kira, p["name"])
        v = seen["p"]["view"]
        return not rows_about(v, "iron-plate") and rows_about(v, "copper-plate") and rows_about(v, "steel-plate")
    try:
        wait_until(iron_gone, timeout=60, step=1, what="the panel dropping the delivered iron")
    except AssertionError:
        raise AssertionError(f"iron delivered but the panel still lists it (or lost copper/steel): {seen.get('p')}")


def test_restores_a_saved_panel_from_the_artifacts(kira):
    saved = [p for p in panels(kira) if "science" in (p["name"] + p["title"]).lower()]
    assert saved, "runs after test_science_panel_with_a_period_filter"
    name, lua = saved[0]["name"], current(kira, saved[0]["name"])["lua"]
    saved_panel(name)
    assert kira.lua(f'return remote.call("kc", "panel_remove", {lua_str(name)})')["ok"]
    assert not [p for p in panels(kira) if p["name"] == name]
    tab = time.strftime("Restore %H:%M:%S")
    text, _, t = ask(kira, tab, "Bring back my saved science panel.")
    print(f"\nrestore reply ({t:.0f}s): {text}")
    back = [p for p in panels(kira) if p["name"] == name]
    assert back and not back[0].get("error"), f"panel {name} not restored: {[p['name'] for p in panels(kira)]}"
    assert back[0]["lua"] == lua, "restored from the artifact, not rewritten"
    assert "restore" in transcript(tab)


def test_window_library_opens_and_deletes_without_kira(kira):
    """The Library button's path: mod -> bridge -> dispatcher (in the gateway) -> artifacts -> mod.
    No chat and no model turn: the transcript of the run's chats must not grow."""
    saved = [p for p in panels(kira) if "science" in (p["name"] + p["title"]).lower()]
    assert saved, "runs after the science panel tests"
    name, lua = saved[0]["name"], saved[0]["lua"]
    assert kira.lua(f'return remote.call("kc", "panel_remove", {lua_str(name)})')["ok"]
    # the previous turn writes its closing message a few seconds after factorio_reply: let it settle
    def sizes_now():
        return {p: p.stat().st_size for p in (KC_HOME / "sessions").glob("dashboard_*.jsonl")}
    sizes, quiet_since = sizes_now(), time.monotonic()
    while time.monotonic() - quiet_since < 8:
        time.sleep(1)
        now = sizes_now()
        if now != sizes:
            sizes, quiet_since = now, time.monotonic()
    sessions = list(sizes)

    def answered(req):
        def check():
            lib = kira.lua('return remote.call("kc", "library")')
            pending = lib.get("pending") or {}
            return lib if (pending == {} or req["id"] not in pending) else None
        return wait_until(check, timeout=30, step=0.5, what=f"the dispatcher answering library {req['op']}")

    t0 = time.monotonic()
    lib = answered(kira.lua('return remote.call("kc", "library_request", "list")'))
    print(f"\nlibrary list in {time.monotonic() - t0:.1f}s: {lib.get('panels')} error={lib.get('error')}")
    assert not lib.get("error"), lib
    entries = [p for p in (lib["panels"] if lib["panels"] != {} else []) if "science" in (p["slug"] + p["title"]).lower()]
    assert entries, f"the run's science panel is not in the library: {lib['panels']}"
    slug = entries[0]["slug"]

    t0 = time.monotonic()
    lib = answered(kira.lua(f'return remote.call("kc", "library_request", "open", {lua_str(slug)})'))
    back = [p for p in panels(kira) if p["name"] == name]
    print(f"open in {time.monotonic() - t0:.1f}s")
    assert back and back[0]["lua"] == lua and not back[0].get("error"), (lib, [p["name"] for p in panels(kira)])

    lib = answered(kira.lua(f'return remote.call("kc", "library_request", "delete", {lua_str(slug)})'))
    assert not lib.get("error") and slug not in [p["slug"] for p in (lib["panels"] if lib["panels"] != {} else [])], lib
    assert artifact("show", slug).returncode != 0, "deleted from the library = gone from Kiro Crew"
    assert [p for p in panels(kira) if p["name"] == name], "deleting from the library leaves the open panel alone"
    after = sizes_now()
    grown = [p.name for p in after if after[p] != sizes.get(p)]
    assert not grown, f"a chat changed during library use (a model turn?): {grown}"
