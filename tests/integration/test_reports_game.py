"""Automatic error reports from the real mod, bridge and MCP server (docs/sentry.md, T2): each
planted error reaches the fake Sentry once, with the failing code and no player text."""
from __future__ import annotations

import time

from conftest import http, lua_str, wait_until

QUESTION = "why is my secret iron line idle"  # player text: must never appear in an error


def lua_errors(sentry, source, needle=""):
    return [(ev, files) for ev, files in sentry.events()
            if ev.get("tags", {}).get("source") == source and ev["exception"]["values"][0]["type"] == "LuaError"
            and needle in ev["exception"]["values"][0]["value"]]


def assert_no_player_text(sentry):
    for _, _, body in sentry.got:
        assert QUESTION.encode() not in body


def test_kiras_failing_query_goes_with_its_code(stack, mcp, sentry):
    stack.rcon.lua(f'return remote.call("kc", "ask", "General", {lua_str(QUESTION)})')  # a question is in the bridge's queue meanwhile
    lua = "local x = 1\nerror('field_for_report_test: x=' .. x)"
    r = mcp.tool("factorio_query", lua=lua)
    assert r["error"] == "runtime_error"
    got = wait_until(lambda: lua_errors(sentry, "factorio_query", "field_for_report_test"), timeout=15, what="the query's LuaError")
    ev, files = got[-1]
    assert "field_for_report_test" in ev["exception"]["values"][0]["value"]
    assert files["code.lua"].decode() == lua
    assert ev["tags"]["part"] == "bridge" and ev["tags"]["mod_version"] != "?" and ev["environment"] == "dev-l2"
    mcp.tool("factorio_query", lua=lua)  # the same mistake again within the hour: not sent twice
    time.sleep(2)
    assert len(lua_errors(sentry, "factorio_query", "field_for_report_test")) == 1
    assert_no_player_text(sentry)


def test_a_failing_panel_goes_once_with_its_code(stack, mcp, sentry):
    body = "error('panel body broke for the report test')"
    mcp.tool("factorio_query", lua=f"return kc.panel{{name = 'report-test', every = 1, lua = {body!r}}}")
    got = wait_until(lambda: lua_errors(sentry, "panel", "panel body broke"), timeout=20, what="the panel's LuaError")
    ev, files = got[-1]
    assert files["code.lua"].decode() == body and ev["tags"]["name"] == "report-test"
    time.sleep(3)  # it fails on every refresh; only the first failure in a row is sent
    assert len(lua_errors(sentry, "panel", "panel body broke")) == 1
    mcp.tool("factorio_query", lua="kc.panel_remove('report-test') return true")


def test_an_error_in_the_mods_own_handler_answers_and_is_reported(stack, sentry):
    # marks must be a list; a number breaks the mod's handler itself, which used to stop the game
    r = http("POST", "/mark", {"chat": "General", "marks": 5})
    assert r["ok"] is False and r["error"] == "mod_error", r
    got = wait_until(lambda: lua_errors(sentry, "mod", "handling mark"), timeout=15, what="the mod's LuaError")
    assert "handling mark" in got[-1][0]["exception"]["values"][0]["value"]
    assert stack.rcon.lua("return game.tick") > 0  # the game goes on
