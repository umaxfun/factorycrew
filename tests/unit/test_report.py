"""report/kc_report.py against a fake Sentry on localhost (docs/sentry.md, T1)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "report"))
sys.path.insert(0, str(ROOT / "tests"))
import kc_report as r  # noqa: E402
from fake_sentry import KEY, FakeSentry  # noqa: E402


@pytest.fixture
def sentry():
    s = FakeSentry()
    yield s
    s.close()


def rep(tmp_path, dsn, **kw):
    kw.setdefault("environment", "dev-test")
    return r.Reporter("bridge", root=ROOT, outbox=tmp_path / "outbox", dsn=dsn, settings_url=None, start=False, **kw)


def boom():
    try:
        open(str(Path.home() / "secret-dir" / "x.json"))
    except OSError as exc:
        return exc


def test_an_error_goes_as_an_event_with_versions_and_no_user_name(tmp_path, sentry):
    rp = rep(tmp_path, sentry.dsn)
    assert rp.capture_exception(boom(), tags={"mod": "0.3.7"})
    assert rp.flush() is None and rp.files() == []
    path, headers, body = sentry.got[0]
    assert path == "/api/7/envelope/" and f"sentry_key={KEY}" in headers["X-Sentry-Auth"]
    head, [(h, payload)] = sentry.items()
    ev = json.loads(payload)
    assert h["type"] == "event" and h["length"] == len(payload) and head["event_id"] == ev["event_id"]
    assert ev["release"] == f"factorycrew@{r._version(ROOT)}" and ev["environment"] == "dev-test"
    assert ev["tags"] == {"part": "bridge", "mod": "0.3.7"}
    exc = ev["exception"]["values"][-1]
    assert exc["type"] == "FileNotFoundError" and exc["stacktrace"]["frames"][-1]["function"] == "boom"
    assert str(Path.home()) not in body.decode() and "~/secret-dir" in exc["value"]
    assert "vars" not in body.decode()  # never local variables


def test_paths_of_any_user_are_scrubbed():
    assert r.scrub("/Users/gamer/x /home/bob/y C:\\Users\\Ann\\z") == "~/x ~/y ~\\z"


def test_release_sends_no_host_name_and_dev_does(tmp_path, sentry):
    for env, has in (("prod", False), ("dev-mac", True)):
        rp = rep(tmp_path / env, sentry.dsn, environment=env)
        rp.capture_message("LuaError", f"boom in {env}")
        rp.flush()
        assert ("server_name" in json.loads(sentry.items()[1][0][1])) is has


def test_environment_defaults_to_dev_hostname(tmp_path, monkeypatch):
    monkeypatch.delenv("FC_REPORT_ENV", raising=False)
    monkeypatch.setattr(r.socket, "gethostname", lambda: "Michaels-MacBook.local")
    rp = r.Reporter("bridge", root=ROOT, outbox=tmp_path, dsn="", environment=None, settings_url=None, start=False)
    assert rp.environment == "dev-michaels-macbook"  # no report/environment stamp in a checkout


def test_the_same_error_goes_once_an_hour(tmp_path, sentry):
    rp = rep(tmp_path, sentry.dsn)
    assert rp.capture_exception(boom())
    assert rp.capture_exception(boom()) is None
    assert rp.capture_message("LuaError", "attempt to index nil") and rp.capture_message("LuaError", "other")
    assert len(rp.files()) == 3


def test_telemetry_off_sends_no_error_and_drops_queued_ones_but_reports_still_go(tmp_path, sentry):
    on = rep(tmp_path, sentry.dsn)
    on.capture_exception(boom())
    off = rep(tmp_path, sentry.dsn, telemetry=False)
    assert off.files() == []  # the queued error went with the switch
    assert off.capture_exception(ValueError("x")) is None and off.capture_message("LuaError", "y") is None
    rid = off.send_report("the panel does not update", attachments=[("chat.jsonl", b'{"a":1}\n', "text/plain")])
    off.flush()
    head, items = sentry.items()
    assert [h["type"] for h, _ in items] == ["feedback", "attachment"] and head["event_id"].startswith(rid)
    fb = json.loads(items[0][1])
    assert fb["contexts"]["feedback"] == {"message": "the panel does not update", "source": "button"}
    assert items[1][0]["filename"] == "chat.jsonl" and items[1][1] == b'{"a":1}\n'


def test_telemetry_switch_is_read_from_the_app_config(tmp_path, monkeypatch):
    monkeypatch.delenv("FC_TELEMETRY", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text('{"telemetry": false}')
    rp = r.Reporter("bridge", root=tmp_path, outbox=tmp_path / "o", dsn="", settings_url=None, start=False)
    assert rp.telemetry is False
    (tmp_path / "data" / "config.json").write_text("{}")
    assert rp.telemetry is True  # default on


def test_offline_reports_wait_and_all_go_when_the_network_is_back(tmp_path, sentry):
    port = sentry.server.server_port
    sentry.server.shutdown()
    sentry.server.server_close()  # nothing listens: like no network
    rp = rep(tmp_path, f"http://{KEY}@127.0.0.1:{port}/7")
    t0 = time.monotonic()
    rp.capture_exception(boom())
    rp.capture_message("LuaError", "a")
    rp.send_report("help")
    assert time.monotonic() - t0 < 0.05  # the caller never waits for the network
    assert rp.flush() == r.RETRY_STEPS[0] and rp.flush() == r.RETRY_STEPS[1]
    assert len(rp.files()) == 3
    back = FakeSentry()
    rp._dsn = back.dsn
    assert rp.flush() is None and rp.files() == [] and len(back.got) == 3
    stamps = [json.loads(back.items(i)[1][0][1])["timestamp"] for i in range(3)]
    assert all(s < time.time() - 0 for s in stamps)  # the time it happened, not the time it went
    back.close()


def test_server_errors_are_retried_and_refusals_dropped(tmp_path, sentry):
    rp = rep(tmp_path, sentry.dsn)
    rp.send_report("x")
    sentry.status = 503
    assert rp.flush() == r.RETRY_STEPS[0] and len(rp.files()) == 1
    sentry.status = 401
    assert rp.flush() is None and rp.files() == []


def test_rate_limit_waits_and_drops_automatic_errors_but_keeps_reports(tmp_path, sentry):
    rp = rep(tmp_path, sentry.dsn)
    rp.capture_exception(boom())
    rp.send_report("keep me")
    sentry.status, sentry.headers = 429, {"Retry-After": "120"}
    assert 100 < rp.flush() <= 120
    assert [p.name.split("-")[1] for p in rp.files()] == ["report"]
    assert rp.capture_message("LuaError", "during the limit") is None  # not even queued
    assert len(sentry.got) == 1 and rp.flush() > 100  # nothing sent while limited


def test_outbox_keeps_reports_over_automatic_errors_when_full(tmp_path, sentry, monkeypatch):
    monkeypatch.setattr(r, "MAX_FILES", 3)
    rp = rep(tmp_path, sentry.dsn)
    rp.send_report("first report")
    for i in range(4):
        rp.capture_message("LuaError", f"error {i}")
    kinds = [p.name.split("-")[1] for p in rp.files()]
    assert len(kinds) == 3 and "report" in kinds


def test_no_dsn_means_no_errors_queued_and_reports_wait(tmp_path):
    rp = rep(tmp_path, "")
    assert rp.capture_exception(boom()) is None
    assert rp.send_report("x") and rp.flush() is None and len(rp.files()) == 1


def test_a_fetched_dsn_must_point_at_sentry(tmp_path, monkeypatch):
    monkeypatch.delenv("FC_SENTRY_DSN", raising=False)  # test-unit.sh sets it empty
    rp = rep(tmp_path, None)
    rp._write(rp.outbox / "settings.json", json.dumps({"dsn": f"https://{KEY}@evil.example.com/1"}).encode())
    assert "sentry.io" in rp.dsn()  # the fetched copy is ignored, the shipped one is used
    rp._write(rp.outbox / "settings.json", json.dumps({"dsn": f"https://{KEY}@o1.ingest.de.sentry.io/2"}).encode())
    assert rp.dsn() == f"https://{KEY}@o1.ingest.de.sentry.io/2"


def test_the_background_thread_sends_without_being_asked(tmp_path, sentry):
    rp = r.Reporter("bridge", root=ROOT, outbox=tmp_path, dsn=sentry.dsn, environment="dev-test", settings_url=None)
    rp.send_report("from the thread")
    for _ in range(50):
        if sentry.got:
            break
        time.sleep(0.05)
    assert sentry.got and rp.files() == []


def test_switching_off_while_running_drops_queued_errors_before_sending(tmp_path, sentry):
    (tmp_path / "data").mkdir()
    cfg = tmp_path / "data" / "config.json"
    cfg.write_text("{}")
    rp = r.Reporter("bridge", root=tmp_path, outbox=tmp_path / "o", dsn=sentry.dsn, environment="dev-test",
                    settings_url=None, start=False)
    rp.capture_exception(boom())
    rp.send_report("mine")
    cfg.write_text('{"telemetry": false}')  # the status page's switch, while the part runs
    rp.flush()
    assert [json.loads(sentry.items(i)[1][0][1]).get("contexts", {}).get("feedback", {}).get("message")
            for i in range(len(sentry.got))] == ["mine"]


def test_module_functions_never_raise_when_not_configured():
    r._REPORTER = None
    assert r.capture_exception(ValueError()) is None and r.send_report("x") is None
