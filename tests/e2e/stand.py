#!/usr/bin/env python3
"""Game stand for e2e: headless Factorio 2.1 + bridge inside one container.

Reuses the level 2 harness (tests/integration/conftest.py) for preparing the save and
starting the processes. Runs until stdin closes or SIGTERM. Inside the container the
bridge HTTP is 34198 and RCON 27015; scripts/test-e2e.sh publishes them on the slot's ports.
"""
import signal
import sys
import time

sys.path.insert(0, "/work/tests/integration")
import conftest as c  # noqa: E402

# real timeouts; published only to 127.0.0.1 on the host. The NoReply tab's first factorio_reply
# is dropped: tests/e2e/test_turn_contract_e2e.py plants a turn that "skipped" factorio_reply.
c.BRIDGE_ARGS = ["--http-host", "0.0.0.0", "--drop-first-reply", "NoReply"]
s = c.Stack()
s.prepare()
s.start_bridge()
s.start_game()
s.wait_online(60)
print("STAND_READY", flush=True)

stop = False


def on_term(*_):
    global stop
    stop = True


signal.signal(signal.SIGTERM, on_term)
try:
    while not stop and s.game.poll() is None and s.bridge.poll() is None:
        time.sleep(0.5)
finally:
    print(f"stand stopping (game rc={s.game.poll()}, bridge rc={s.bridge.poll()})", flush=True)
    s.stop()
