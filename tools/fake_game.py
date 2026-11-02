#!/usr/bin/env python3
"""Spike: play the game over UDP against a bridge with no Factorio.  fake_game.py BRIDGE_UDP GAME_UDP CHAT TEXT [S]"""
import json
import socket
import sys
import time

bridge_port, game_port, chat, text = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
seconds = float(sys.argv[5]) if len(sys.argv) > 5 and not sys.argv[5].startswith("-") else 240
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.bind(("127.0.0.1", game_port))
rx.settimeout(1)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
RUN = "probe-run-1"


def send(m):
    tx.sendto(json.dumps({"v": 1, **m}).encode(), ("127.0.0.1", bridge_port))


beat_msg = {"type": "heartbeat", "id": "hb", "run_id": RUN, "run_name": "Probe", "tick": 3, "tabs": [chat],
            "mod_version": "probe"}
send({**beat_msg, "type": "hello"})
time.sleep(1)
send({"type": "ask", "id": f"ask-{int(time.time())}", "chat": chat, "text": text, "run_id": RUN, "run_name": "Probe",
      "tick": 2})
end, beat = time.time() + seconds, 0
while time.time() < end:
    if time.time() - beat > 4:
        send(beat_msg)
        beat = time.time()
    try:
        data, _ = rx.recvfrom(65536)
    except socket.timeout:
        continue
    m = json.loads(data)
    if m.get("type") in ("reply", "status", "trace"):
        print(time.strftime("%H:%M:%S"), json.dumps(m, ensure_ascii=False)[:300], flush=True)
        if m.get("type") == "reply" or (m.get("type") == "status" and m.get("state") == "idle"):
            if "--until-reply" in sys.argv:
                break
