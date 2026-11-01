"""A fake Sentry on 127.0.0.1 for tests of report/kc_report.py and of what the parts report
(docs/sentry.md). Answers each envelope POST with `status`; keeps every envelope, decoded."""
from __future__ import annotations

import gzip
import http.server
import json
import threading

KEY = "0" * 32


def parse(body: bytes):
    """(envelope header, [(item header, payload bytes)]), split by each item's `length` as
    Sentry does: a payload may hold newlines."""
    nl = body.index(b"\n")
    head, out, k = json.loads(body[:nl]), [], nl + 1
    while k < len(body):
        nl = body.index(b"\n", k)
        h = json.loads(body[k:nl])
        out.append((h, body[nl + 1:nl + 1 + h["length"]]))
        k = nl + 1 + h["length"] + 1
    return head, out


class FakeSentry:
    def __init__(self, port: int = 0):
        self.status, self.headers, self.got = 200, {}, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                outer.got.append((self.path, dict(self.headers), body))
                self.send_response(outer.status)
                for k, v in outer.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.dsn = f"http://{KEY}@127.0.0.1:{self.server.server_port}/7"

    def items(self, i: int = -1):
        return parse(self.got[i][2])

    def events(self) -> list:
        """Every event or feedback payload received, decoded, with its attachments:
        [(payload dict, {filename: bytes})]."""
        out = []
        for _, _, body in list(self.got):
            _, items = parse(body)
            ev = next((json.loads(p) for h, p in items if h["type"] in ("event", "feedback")), None)
            files = {h["filename"]: p for h, p in items if h["type"] == "attachment"}
            if ev is not None:
                out.append((ev, files))
        return out

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
