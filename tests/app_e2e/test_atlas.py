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


def test_ups_during_a_full_dump(kira):
    before = ups(10)
    res = {}
    th = threading.Thread(target=lambda: res.update(r=http("POST", "/atlas", {"timeout": 300}, timeout=320)))
    th.start()
    samples = []
    while th.is_alive():
        samples.append(ups(2))
    th.join()
    r = res["r"]
    assert r.get("ok"), {k: v for k, v in r.items() if k != "text"}
    during = min(samples) if samples else None
    print(f"\nUPS before {before:.1f}, during a full dump min {during} over {len(samples)} samples; dump {r['data']} "
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
