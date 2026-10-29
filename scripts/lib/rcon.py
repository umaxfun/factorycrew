#!/usr/bin/env python3
"""Minimal RCON client for the headless test game.

  rcon.py PORT PASSWORD 'lua body'      runs /silent-command <lua body>, prints what it rcon.print()s
  rcon.py PORT PASSWORD --wait SECS     waits until RCON answers
Lines of the body are joined with spaces (console commands are one line), so no `--` comments.
"""
import socket
import struct
import sys
import time


def connect(port: int, pw: str, timeout: float = 600) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    send(s, 1, 3, pw)
    while True:
        rid, kind, _ = read(s)
        if kind == 2:
            if rid != 1:
                raise SystemExit("rcon auth failed")
            return s


def send(s: socket.socket, i: int, kind: int, body: str) -> None:
    data = struct.pack("<ii", i, kind) + body.encode() + b"\0\0"
    s.sendall(struct.pack("<i", len(data)) + data)


def exact(s: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("rcon closed")
        b += c
    return b


def read(s: socket.socket):
    size, rid, kind = struct.unpack("<iii", exact(s, 12))
    return rid, kind, exact(s, size - 8)[:-2].decode("utf-8", "replace")


def run(port: int, pw: str, lua: str) -> str:
    s = connect(port, pw)
    body = " ".join(l.strip() for l in lua.splitlines() if l.strip())
    send(s, 2, 2, "/silent-command " + body)
    _, _, out = read(s)
    s.close()
    return out


def main() -> None:
    port, pw = int(sys.argv[1]), sys.argv[2]
    if sys.argv[3] == "--wait":
        deadline = time.monotonic() + float(sys.argv[4])
        while time.monotonic() < deadline:
            try:
                if run(port, pw, "rcon.print('up')").strip() == "up":
                    return
            except OSError:
                pass
            time.sleep(1)
        raise SystemExit(f"rcon on {port} did not answer in {sys.argv[4]} s")
    print(run(port, pw, sys.argv[3]))


if __name__ == "__main__":
    main()
