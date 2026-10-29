"""Minimal RCON client: python3 rcon.py PORT PASS < lua_file  -> prints rcon output."""
import socket, struct, sys

port, pw = int(sys.argv[1]), sys.argv[2]
s = socket.create_connection(("127.0.0.1", port), timeout=600)


def pkt(i, kind, body):
    data = struct.pack("<ii", i, kind) + body.encode() + b"\0\0"
    s.sendall(struct.pack("<i", len(data)) + data)


def exact(n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("closed")
        b += c
    return b


def read():
    size, rid, kind = struct.unpack("<iii", exact(12))
    return rid, kind, exact(size - 8)[:-2].decode()


pkt(1, 3, pw)
while True:
    rid, kind, _ = read()
    if kind == 2:
        assert rid == 1, "auth failed"
        break
lua = " ".join(l.strip() for l in sys.stdin.read().splitlines() if l.strip() and not l.strip().startswith("--"))
pkt(2, 2, "/silent-command " + lua)
rid, kind, body = read()
print(body)
