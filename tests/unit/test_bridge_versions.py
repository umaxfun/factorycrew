"""The bridge tells the game the installed app's version and keeps the mod's for /health.

After an app update the game still runs the old mod until Factorio restarts: the pong carries
the app's version so the mod can say so, and the status page reads the mod's version."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import factorio_bridge as fb  # noqa: E402


def _bridge(app_version):
    b = fb.Bridge(fb.Settings(app_version=app_version, atlas_prefetch=False))
    sent = []
    b.send_game = sent.append
    return b, sent


def test_pong_carries_the_app_version_and_health_the_mods():
    b, sent = _bridge("0.3.6")
    b.on_game_message({"type": "heartbeat", "id": "hb-1", "run_id": "r1", "mod_version": "0.3.5"})
    assert sent == [{"type": "pong", "id": "hb-1", "app_version": "0.3.6"}]
    h = b.health()
    assert h["mod_version"] == "0.3.5" and h["app_version"] == "0.3.6"


def test_outside_the_app_the_pong_is_as_before():
    b, sent = _bridge(None)
    b.on_game_message({"type": "hello", "id": "hb-2", "run_id": "r1"})  # a mod before 0.3.7: no version
    assert sent == [{"type": "pong", "id": "hb-2"}] and b.health()["mod_version"] is None
