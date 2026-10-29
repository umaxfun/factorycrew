"""Atlas e2e on the real 2.0.77 client with a player's save and mods (docs/atlas.md, task 1).

  FC_UI_SAVE=<save.zip> FC_UI_MODS=<mods dir> scripts/test-app-e2e.sh -k atlas

The reported case: a box over one furnace of a 32-machine titanium-tetrachloride block, "why
doesn't @1 work". Kira must take the whole block through factorio_atlas and say which input is
short and that it arrives by train. Also measured here: UPS before and during a full dump.
Skips unless the fixture save is the one loaded.
"""
from __future__ import annotations

import json
import re
import threading
import time

import pytest

from test_app_e2e import KC_HOME, STAMP, Player, http, lua  # noqa: F401  (shared harness)

TETRA = "nullius-titanium-tetrachloride"
BOX = [[-358.5, -124], [-356, -121]]  # one furnace on the side of the block, as the player drew it
LONG = 360


@pytest.fixture(scope="module")
def kira():
    assert http("GET", "/health")["game_online"], "game not online on the app's bridge"
    n = lua(f'return game.surfaces.nauvis.count_entities_filtered{{area = {{{{-376, -137}}, {{-347, -111}}}}, '
            f'type = "assembling-machine"}}')
    if n < 32:
        pytest.skip("the fixture save is not loaded (FC_UI_SAVE / FC_UI_MODS, docs/atlas.md Fixture)")
    return Player()


def transcript(tab):
    st = json.loads((KC_HOME / "factorio" / "dispatch-state.json").read_text())
    slots = [v for k, v in st["slots"].items() if k.endswith(f"\t{tab}")]
    assert slots, f"no chat for tab {tab}"
    return (KC_HOME / "sessions" / f"dashboard_{slots[-1]}.jsonl").read_text(encoding="utf-8")


def ups(seconds):
    t0, w0 = lua("return game.tick"), time.monotonic()
    time.sleep(seconds)
    return (lua("return game.tick") - t0) / (time.monotonic() - w0)


def test_ups_baseline(kira):
    """The base's own dips (autosave, big events) without any dump, for comparison."""
    samples = [ups(2) for _ in range(30)]
    print(f"\nUPS without a dump over 60 s: {[round(x) for x in samples]}")


def test_ups_during_a_full_dump(kira):
    before = ups(10)
    res = {}
    th = threading.Thread(target=lambda: res.update(r=http("POST", "/atlas", {"timeout": 300, "raw": True}, timeout=320)))
    th.start()
    samples = []
    # 5-s windows: each reading waits for the game's 10-tick UDP poll (up to ~170 ms), so a 2-s window
    # read 46-47 UPS while the game itself had no such hitch (a lower budget did not move those dips)
    while th.is_alive():
        samples.append(ups(5))
    th.join()
    r = res["r"]
    assert r.get("ok"), {k: v for k, v in r.items() if k != "text"}
    during = min(samples) if samples else None
    print(f"\nUPS before {before:.1f}, during a full dump min {during} over {len(samples)} samples {[round(x) for x in samples]}; dump {r['data']} "
          f"in {r['ms'] / 1000:.1f} s")
    assert during is None or during >= 50, samples


def test_a_box_on_one_furnace_is_about_the_whole_block(kira):
    tab = f"Atlas {STAMP}"
    kira.open_tab(tab)
    (x0, y0), (x1, y1) = BOX
    p = lua(f'return remote.call("kc", "point", {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}})')
    assert p, "pointer not made"
    text, _, t_all = kira.ask_and_wait(tab, "why doesn't @1 work? where is all the rutile?", timeout=LONG)
    print(f"\nreply ({t_all:.0f}s): {text}")
    log = transcript(tab)
    assert "factorio_atlas" in log, "Kira did not use factorio_atlas"
    assert "32" in text, "the reply does not name the whole block"
    assert re.search(r"train|station|stop", text, re.I), "the reply does not say rutile comes by train"


@pytest.mark.skipif(not __import__("os").environ.get("FC_ATLAS_UPS_SWEEP"), reason="diagnostic: FC_ATLAS_UPS_SWEEP=1")
@pytest.mark.parametrize("budget,send_bytes,max_chunks", [
    (200, 4000, 24), (10, 4000, 24), (200, 1000, 24), (200, 4000, 2), (10, 500, 2)])
def test_ups_sweep(kira, budget, send_bytes, max_chunks):
    """Which part of a dump tick costs the frames: entities, sending, or looking at chunks."""
    res = {}
    body = {"timeout": 600, "raw": True, "budget": budget, "send_bytes": send_bytes, "max_chunks": max_chunks}
    th = threading.Thread(target=lambda: res.update(r=http("POST", "/atlas", body, timeout=620)))
    th.start()
    samples = []
    while th.is_alive() and len(samples) < 6:
        samples.append(ups(2))
    th.join()
    print(f"\nbudget {budget} send {send_bytes} chunks {max_chunks}: UPS {[round(x) for x in samples]}, "
          f"{res['r'].get('data')}")


def test_why_the_block_is_slow_names_both_limits(kira):
    """docs/atlas.md task 2: the rutile source makes 8.47/s of the 25.6/s the block needs, and graphite
    reaches it on one lane (22.5/s of the 44.8/s needed). Both must be in the answer, with numbers."""
    tab = f"Atlas slow {STAMP}"
    kira.open_tab(tab)
    (x0, y0), (x1, y1) = BOX
    assert lua(f'return remote.call("kc", "point", {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}})')
    text, _, t_all = kira.ask_and_wait(tab, "why is this titanium tetrachloride block @1 so slow? check everything",
                                       timeout=LONG)
    print(f"\nreply ({t_all:.0f}s): {text}")
    log = transcript(tab)
    assert '"level": "trace"' in log or "'level': 'trace'" in log or 'level\\": \\"trace' in log, \
        "Kira did not trace an input"
    low = text.lower()
    assert re.search(r"8[.,]4|8[.,]5", text) and re.search(r"25[.,]6|26", text), "no rutile supply vs need"
    assert "lane" in low or "полос" in low, "the graphite lane limit is missing"
    assert re.search(r"22[.,]5", text), "no lane capacity number"


def test_where_is_glass_made(kira):
    """docs/atlas.md task 3: Kira finds glass through the map level, answers with zones and coordinates
    and marks them."""
    tab = f"Atlas glass {STAMP}"
    kira.open_tab(tab)
    before = len(kira.kc_tags()[0])
    text, _, t_all = kira.ask_and_wait(tab, "where is glass made on my base? mark the places", timeout=LONG)
    print(f"\nreply ({t_all:.0f}s): {text}")
    log = transcript(tab)
    assert re.search(r'level\W+map', log), "Kira did not use the map level"
    # the zones that make glass on the fixture: around (103..185, 38..140), (-51..-6, 78..105), (103..113, 37..61)
    assert re.search(r"\(?-?\d+,\s*-?\d+\)?", text), "no coordinates in the reply"
    assert len(kira.kc_tags()[0]) > before, "nothing marked"


def test_what_the_robot_mall_lacks(kira):
    """docs/atlas.md task 4: a click in the robot mall; Kira names the machines waiting for ingredients
    and the items nobody puts into the logistic network (hard-glass on the fixture)."""
    tab = f"Atlas robots {STAMP}"
    kira.open_tab(tab)
    assert lua('return remote.call("kc", "point", {226, -25.5})')
    text, _, t_all = kira.ask_and_wait(tab, "what does the robot mall @1 lack?", timeout=LONG)
    print(f"\nreply ({t_all:.0f}s): {text}")
    log = transcript(tab)
    assert "factorio_atlas" in log
    assert re.search(r"hard[- ]glass", text, re.I), "the item nothing puts into the network is missing"
    assert re.search(r"\b\d+\b", text) and re.search(r"wait|ingredient|short|lack", text, re.I)
