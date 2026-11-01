"""Error reports and bug reports to Sentry, stdlib only (docs/sentry.md).

Every part that reports (bridge, dispatcher, MCP server, app backend) calls `configure(part)`
once, then `capture_exception(exc)` for an error, or `send_report(...)` for what the player
asked to send. Nothing here ever raises into the caller or waits for the network: a report is
written to the outbox and a background thread sends it, retrying while there is no network.

Settings, first match wins:
  DSN          env FC_SENTRY_DSN (empty: send nothing) > the copy fetched from the release
               branch (cached a day) > report/settings.json shipped next to this file
  environment  env FC_REPORT_ENV > report/environment (written by the build; `prod` for a
               release) > dev-<hostname>
  telemetry    env FC_TELEMETRY=0 > "telemetry" in <app>/data/config.json > on
  outbox       env FC_REPORT_OUTBOX > <KIROCREW_HOME>/factorio/outbox
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import platform
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    import fcntl  # one sender per outbox; absent on Windows, where Sentry's event_id dedup is enough
except ImportError:  # pragma: no cover
    fcntl = None

log = logging.getLogger("factorycrew.report")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # the app's folder, or the repo checkout
SETTINGS_URL = "https://raw.githubusercontent.com/umaxfun/factorycrew/release/apps/factorycrew/report/settings.json"
# A fetched DSN may only point at Sentry: the release branch must not be able to send the
# player's reports anywhere else.
DSN_RE = re.compile(r"^https://([0-9a-f]{32})@([a-z0-9.-]+\.sentry\.io)/(\d+)$")
TIMEOUT = 5.0
RETRY_STEPS = (60, 300, 1800, 3600)  # seconds after the 1st, 2nd, 3rd, 4th+ failure in a row
PERMANENT = {400, 401, 403, 413}  # retrying cannot help: drop the file
MAX_FILES = 50
MAX_BYTES = 50 * 1024 * 1024
AUTO_MAX_AGE = 30 * 86400
RATE_WINDOW = 3600  # the same automatic error at most once an hour
# ...and at most this many automatic errors an hour from one machine: Kira's Lua mistakes are
# many and varied, and the free plan's 5k events a month must outlast a bad evening
MAX_AUTO_PER_HOUR = 30
SETTINGS_MAX_AGE = 86400
PATH_HOME = re.compile(r"(/Users/|/home/|[A-Za-z]:\\Users\\)[^/\\\s'\"]+")


def scrub(text: str) -> str:
    """Cut the OS user name out of paths."""
    home = str(Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    return PATH_HOME.sub("~", text)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _version(root: Path) -> str:
    for p in (root / "app.json", root / "app" / "app.json"):
        v = (_read_json(p) or {}).get("version") if p.exists() else None
        if v:
            return str(v)
    return "unknown"


def _kirocrew_home(root: Path) -> Path:
    if os.environ.get("KIROCREW_HOME"):
        return Path(os.environ["KIROCREW_HOME"])
    if root.parent.name == "apps":  # <home>/apps/<app>/
        return root.parent.parent
    return Path.home() / ".kiro" / "crew"


def envelope(header: dict, items: list) -> bytes:
    """Sentry envelope: a header line, then a header line and a payload per item."""
    out = [json.dumps(header, separators=(",", ":")).encode()]
    for item_header, payload in items:
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        out.append(json.dumps({**item_header, "length": len(payload)}, separators=(",", ":")).encode())
        out.append(payload)
    return b"\n".join(out) + b"\n"


class Reporter:
    def __init__(self, part: str, root: Path = ROOT, outbox: Path | None = None, dsn: str | None = None,
                 environment: str | None = None, telemetry: bool | None = None,
                 settings_url: str | None = SETTINGS_URL, start: bool = True):
        self.part, self.root = part, Path(root)
        self.version = _version(self.root)
        self.environment = environment or os.environ.get("FC_REPORT_ENV") or self._stamp() or \
            "dev-" + re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-")
        self._dsn = dsn if dsn is not None else os.environ.get("FC_SENTRY_DSN")
        self._telemetry = telemetry
        self.settings_url = settings_url
        self.outbox = Path(outbox or os.environ.get("FC_REPORT_OUTBOX") or
                           _kirocrew_home(self.root) / "factorio" / "outbox")
        self._wake = threading.Event()
        self._fails = 0
        self._said_offline = False
        self._thread = None
        if not self.telemetry:
            self._drop(lambda name: "-auto-" in name)  # switched off: what was queued goes too
        if start:
            self.start()

    # ── settings ──

    def _stamp(self) -> str:
        try:
            return (HERE / "environment").read_text().strip()
        except OSError:
            return ""

    @property
    def telemetry(self) -> bool:
        if self._telemetry is not None:
            return self._telemetry
        if os.environ.get("FC_TELEMETRY") == "0":
            return False
        return (_read_json(self.root / "data" / "config.json") or {}).get("telemetry", True) is not False

    def dsn(self) -> str:
        if self._dsn is not None:
            return self._dsn
        cached = _read_json(self.outbox / "settings.json") or {}
        if DSN_RE.match(str(cached.get("dsn", ""))):
            return cached["dsn"]
        return str((_read_json(HERE / "settings.json") or {}).get("dsn", ""))

    def _refresh_settings(self) -> None:
        """Fetch settings.json from the release branch at most once a day (a rotated DSN)."""
        if not self.settings_url or self._dsn is not None:
            return
        cache = self.outbox / "settings.json"
        try:
            if time.time() - cache.stat().st_mtime < SETTINGS_MAX_AGE:
                return
        except OSError:
            pass
        try:
            with urllib.request.urlopen(self.settings_url, timeout=TIMEOUT) as r:
                got = json.loads(r.read(4096))
        except (OSError, ValueError, urllib.error.URLError):
            return
        if isinstance(got, dict) and DSN_RE.match(str(got.get("dsn", ""))):
            self._write(cache, json.dumps({"dsn": got["dsn"]}).encode())

    # ── building reports ──

    def _event(self, level: str = "error", platform_name: str = "python") -> dict:
        ev = {
            "event_id": uuid.uuid4().hex, "timestamp": time.time(), "platform": platform_name,
            "level": level, "release": f"factorycrew@{self.version}", "environment": self.environment,
            "tags": {"part": self.part},
            "contexts": {"os": {"name": platform.system(), "version": platform.mac_ver()[0] or platform.release()},
                         "runtime": {"name": platform.python_implementation(), "version": platform.python_version()}},
            "sdk": {"name": "factorycrew.report", "version": self.version},
        }
        if self.environment.startswith("dev-"):
            ev["server_name"] = socket.gethostname()  # a release sends no host name
        return ev

    @staticmethod
    def _frames(tb) -> list:
        frames = []
        for fs in traceback.extract_tb(tb):
            path = fs.filename
            frames.append({"filename": scrub(path), "function": fs.name, "lineno": fs.lineno,
                           "context_line": fs.line or None,
                           "in_app": "site-packages" not in path and not path.startswith(sys.base_prefix)})
        return frames

    def capture_exception(self, exc: BaseException, tags: dict | None = None) -> str | None:
        """Queue an automatic error: type, message, stack, versions. No local variables."""
        try:
            if not self.telemetry or not self.dsn():
                return None
            chain, e = [], exc
            while e is not None and len(chain) < 5:
                chain.append(e)
                e = e.__cause__ or (None if e.__suppress_context__ else e.__context__)
            values = [{"type": type(x).__name__, "module": type(x).__module__,
                       "value": scrub(str(x))[:500],
                       "stacktrace": {"frames": self._frames(x.__traceback__)}} for x in reversed(chain)]
            top = next((f for f in reversed(values[-1]["stacktrace"]["frames"]) if f["in_app"]), {})
            key = f"{values[-1]['type']}|{top.get('filename')}|{top.get('function')}|{self.version}"
            return self._auto(key, {**self._event(), "exception": {"values": values}}, tags)
        except Exception:  # noqa: BLE001 - reporting must never break the caller
            log.debug("could not queue an error report", exc_info=True)
            return None

    def capture_message(self, kind: str, message: str, tags: dict | None = None,
                        attachments: list | None = None, platform_name: str = "other",
                        level: str = "error") -> str | None:
        """Queue an automatic error that is not a Python exception (a Lua error, for one).
        `attachments`: [(filename, bytes, content_type)], e.g. the Lua code that failed."""
        try:
            if not self.telemetry or not self.dsn():
                return None
            first = scrub(message).splitlines()[0][:200] if message.strip() else ""
            ev = {**self._event(level=level, platform_name=platform_name),
                  "exception": {"values": [{"type": kind, "value": scrub(message)[:2000]}]}}
            return self._auto(f"{kind}|{first}|{self.version}", ev, tags, attachments)
        except Exception:  # noqa: BLE001
            log.debug("could not queue an error report", exc_info=True)
            return None

    def send_report(self, message: str, source: str = "button", attachments: list | None = None,
                    tags: dict | None = None) -> str | None:
        """Queue what the player asked to send (comment plus files). Works with telemetry off.
        Returns a short report id, or None if it could not even be written."""
        try:
            ev = self._event(level="info")
            ev["tags"].update(tags or {}, source=source)
            ev["contexts"]["feedback"] = {"message": message[:4000], "source": source}
            items = [({"type": "feedback"}, ev)] + self._attachment_items(attachments)
            self._queue("report", ev["event_id"], items)
            return ev["event_id"][:8]
        except Exception:  # noqa: BLE001
            log.warning("could not save the bug report", exc_info=True)
            return None

    @staticmethod
    def _attachment_items(attachments) -> list:
        return [({"type": "attachment", "filename": name, "content_type": ctype}, data)
                for name, data, ctype in attachments or []]

    def _auto(self, key: str, ev: dict, tags, attachments=None) -> str | None:
        if tags:
            ev["tags"].update({k: str(v) for k, v in tags.items()})
        if self._limited():
            return None
        fp = hashlib.sha1(key.encode()).hexdigest()[:16]
        sent = self._sent_log()
        now = time.time()
        sent = {k: t for k, t in sent.items() if now - t < RATE_WINDOW}
        if fp in sent or len(sent) >= MAX_AUTO_PER_HOUR:
            return None
        sent[fp] = now
        self._write(self.outbox / "sent.json", json.dumps(sent).encode())
        items = [({"type": "event"}, ev)] + self._attachment_items(attachments)
        self._queue("auto", ev["event_id"], items)
        return ev["event_id"]

    def _sent_log(self) -> dict:
        got = _read_json(self.outbox / "sent.json")
        return got if isinstance(got, dict) else {}

    # ── the outbox ──

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def _queue(self, kind: str, event_id: str, items: list) -> None:
        body = envelope({"event_id": event_id}, items)
        self._write(self.outbox / f"{int(time.time() * 1000):013d}-{kind}-{event_id}.envelope", body)
        self._trim()
        self._wake.set()

    def files(self) -> list:
        try:
            return sorted(p for p in self.outbox.iterdir() if p.suffix == ".envelope")
        except OSError:
            return []

    def _drop(self, which) -> None:
        for p in self.files():
            if which(p.name):
                try:
                    p.unlink()
                except OSError:
                    pass

    def _trim(self) -> None:
        """At most MAX_FILES and MAX_BYTES: the oldest automatic error goes first, a report last."""
        now = time.time()
        self._drop(lambda n: "-auto-" in n and now - int(n.split("-", 1)[0]) / 1000 > AUTO_MAX_AGE)
        files = self.files()
        order = [p for p in files if "-auto-" in p.name] + [p for p in files if "-auto-" not in p.name]
        sizes = {p: p.stat().st_size for p in files if p.exists()}
        total, count = sum(sizes.values()), len(files)
        for p in order:
            if count <= MAX_FILES and total <= MAX_BYTES:
                break
            try:
                p.unlink()
            except OSError:
                continue
            count, total = count - 1, total - sizes.get(p, 0)

    def _limited(self) -> bool:
        try:
            return time.time() < float((self.outbox / "retry_after").read_text())
        except (OSError, ValueError):
            return False

    # ── sending ──

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="factorycrew-report", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                delay = self.flush()
            except Exception:  # noqa: BLE001
                log.debug("report sender failed", exc_info=True)
                delay = RETRY_STEPS[-1]
            self._wake.wait(delay)
            self._wake.clear()

    def _post(self, body: bytes):
        """(status, headers), or None when the network did not answer."""
        # The shipped and the env DSN are trusted as they are (tests point one at 127.0.0.1);
        # a fetched one already had to match DSN_RE in dsn().
        m = re.match(r"^(https?)://([0-9a-f]+)@([^/]+)/(\d+)$", self.dsn())
        if not m:
            return 400, {}
        scheme, key, host, project = m.groups()
        req = urllib.request.Request(
            f"{scheme}://{host}/api/{project}/envelope/", data=gzip.compress(body), method="POST",
            headers={"Content-Type": "application/x-sentry-envelope", "Content-Encoding": "gzip",
                     "X-Sentry-Auth": f"Sentry sentry_version=7, sentry_key={key}, "
                                      f"sentry_client=factorycrew/{self.version}"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {})
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def flush(self) -> float | None:
        """Send what the outbox holds. Returns seconds until the next try, None when it is empty."""
        if not self.telemetry:  # switched off while running: queued errors do not go
            self._drop(lambda name: "-auto-" in name)
        if not self.files():
            return None
        if self._limited():
            return max(1.0, float((self.outbox / "retry_after").read_text()) - time.time())
        if not self.dsn():
            return None  # reports wait until there is a DSN; automatic errors are not written then
        lock = None
        if fcntl is not None:
            try:
                lock = open(self.outbox / ".send.lock", "w")
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if lock:
                    lock.close()
                return RETRY_STEPS[0]  # another part is sending this outbox
        try:
            self._refresh_settings()
            for p in self.files():
                try:
                    body = p.read_bytes()
                except OSError:
                    continue
                res = self._post(body)
                if res is None or res[0] >= 500:
                    self._fails += 1
                    if not self._said_offline:
                        self._said_offline = True
                        log.warning("reports wait in the outbox (%s): %s", self.outbox,
                                    "no network" if res is None else f"Sentry answered {res[0]}")
                    return RETRY_STEPS[min(self._fails, len(RETRY_STEPS)) - 1]
                status, headers = res
                if status == 429:
                    wait = float(headers.get("Retry-After") or headers.get("retry-after") or 60)
                    self._write(self.outbox / "retry_after", str(time.time() + wait).encode())
                    self._drop(lambda n: "-auto-" in n)  # Sentry asks to drop, not to queue
                    log.warning("Sentry is rate limiting; automatic errors are dropped for %d s", wait)
                    return wait
                if status in PERMANENT or not 200 <= status < 300:
                    log.warning("Sentry refused %s (HTTP %d); dropped", p.name, status)
                try:
                    p.unlink()
                except OSError:
                    pass
            self._fails, self._said_offline = 0, False
            return None
        finally:
            if lock:
                lock.close()


_REPORTER: Reporter | None = None


def configure(part: str, **kw) -> Reporter | None:
    """Once per process. Never raises: a part without reports still runs."""
    global _REPORTER
    try:
        _REPORTER = Reporter(part, **kw)
    except Exception:  # noqa: BLE001
        log.debug("reports are off: could not set up", exc_info=True)
        _REPORTER = None
    return _REPORTER


def capture_exception(exc: BaseException, tags: dict | None = None) -> str | None:
    return _REPORTER.capture_exception(exc, tags) if _REPORTER else None


def capture_message(kind: str, message: str, tags: dict | None = None, attachments: list | None = None,
                    level: str = "error") -> str | None:
    return _REPORTER.capture_message(kind, message, tags, attachments, level=level) if _REPORTER else None


def reporter() -> Reporter | None:
    return _REPORTER


def install_hooks() -> None:
    """Uncaught exceptions of the main thread and of other threads become reports; the previous
    hooks still run (the traceback still goes to stderr). For asyncio, see `asyncio_handler`."""
    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def on_sys(t, e, tb):
        if not issubclass(t, (KeyboardInterrupt, SystemExit)):
            capture_exception(e, {"uncaught": "main"})
            _drain()
        prev_sys(t, e, tb)

    def on_thread(args):
        if args.exc_value is not None and not issubclass(args.exc_type, SystemExit):
            capture_exception(args.exc_value, {"uncaught": "thread"})
        prev_thread(args)

    sys.excepthook, threading.excepthook = on_sys, on_thread


def asyncio_handler(loop, context) -> None:
    """loop.set_exception_handler(asyncio_handler): a task that died with nobody awaiting it."""
    exc = context.get("exception")
    if isinstance(exc, BaseException):
        capture_exception(exc, {"uncaught": "asyncio"})
    loop.default_exception_handler(context)


def _drain(seconds: float = 2.0) -> None:
    """The process is about to die: give the sender a moment to try what is queued."""
    if _REPORTER is not None:
        t = threading.Thread(target=_REPORTER.flush, daemon=True)
        t.start()
        t.join(seconds)


def send_report(message: str, source: str = "button", attachments: list | None = None,
                tags: dict | None = None) -> str | None:
    return _REPORTER.send_report(message, source, attachments, tags) if _REPORTER else None
