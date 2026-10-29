"""kc.circuits: condition/combinator semantics as in Factorio 2.0 and the tick simulator.

Hand-made snapshots follow docs/circuits-format.md. Real snapshots recorded by the mod's Lua reader
land in tests/unit/fixtures/circuits/*.json; every one found there is loaded and stepped too.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
import kc  # noqa: E402,F401
from kc import circuits as C  # noqa: E402

IRON = "item/iron-plate"
COPPER = "item/copper-plate"
A, B, L, R, S, W = (f"virtual/signal-{x}" for x in "ABLRSW")
EACH, ANY, ALL = C.EACH, C.ANYTHING, C.EVERYTHING
FIX = ROOT / "tests" / "unit" / "fixtures" / "circuits"


def cond(first, cmp, const=None, second=None, join="and", fn=None, sn=None):
    c = {"first": first, "comparator": cmp, "second": second, "constant": const, "join": join}
    if fn:
        c["first_networks"] = fn
    if sn:
        c["second_networks"] = sn
    return c


def out(sig, copy=False, const=None, nets=None):
    o = {"signal": sig, "copy_count_from_input": copy}
    if const is not None:
        o["constant"] = const
    if nets:
        o["networks"] = nets
    return o


def arith(first, op, second=None, output=None, fc=None, sc=None):
    return {"first": first, "first_constant": fc, "operation": op, "second": second,
            "second_constant": sc, "output": output}


def comb(eid, kind, params, in_red=None, in_green=None, out_red=None, out_green=None):
    return {"id": eid, "name": f"{kind}-combinator", "type": f"{kind}-combinator", "kind": kind,
            "x": eid + 0.5, "y": 0.5, "direction": 0, "params": params,
            "connectors": {"in_red": in_red, "in_green": in_green, "out_red": out_red,
                           "out_green": out_green}}


def other(eid, name, typ, red=None, green=None, control=None):
    return {"id": eid, "name": name, "type": typ, "kind": "other", "x": 0.5, "y": eid + 0.5,
            "direction": 0, "connectors": {"red": red, "green": green}, "control": control or {}}


def snap(networks, entities):
    nets = {}
    for nid, v in networks.items():
        color, sigs = v if isinstance(v, tuple) else ("red", v)
        nets[nid] = {"color": color, "signals": sigs}
    return {"v": 1, "tick": 1000, "game_version": "2.0.77", "truncated": False,
            "networks": nets, "entities": entities}


# ------------------------------------------------------------------------------------ arithmetic

@pytest.mark.parametrize("op,a,b,want", [
    ("+", 2, 3, 5), ("-", 2, 3, -1), ("*", -4, 3, -12),
    ("/", 21, 10, 2), ("/", -21, 10, -2), ("/", 21, -10, -2), ("/", -19, -10, 1), ("/", 5, 0, 0),
    ("%", 13, 3, 1), ("%", 13, -3, 1), ("%", -13, 3, -1), ("%", -13, -3, -1), ("%", 7, 0, 0),
    ("^", 2, 10, 1024), ("^", 2, 31, -2147483648), ("^", 2, 32, 0), ("^", 3, -1, 0), ("^", -1, -3, -1),
    ("<<", 1, 4, 16), ("<<", 1, 31, -2147483648), (">>", 256, 4, 16), (">>", -16, 2, -4),
    ("AND", 12, 10, 8), ("OR", 12, 10, 14), ("XOR", 12, 10, 6), ("&", 12, 10, 8),
    ("+", 2147483647, 10, -2147483639), ("*", 65536, 65536, 0), ("-", -2147483648, 1, 2147483647),
    ("/", -2147483648, -1, -2147483648),
])
def test_arith_ops(op, a, b, want):
    assert C.arith(op, a, b) == want


def test_arith_unknown_op():
    with pytest.raises(ValueError):
        C.arith("?", 1, 2)


def test_arithmetic_single_and_constant():
    p = arith(IRON, "/", None, L, sc=1000)
    assert C.arithmetic_output(p, {IRON: 4500}) == {L: 4}
    assert C.arithmetic_output(p, {}) == {}  # 0 / 1000 = 0 -> omitted
    assert C.arithmetic_output(arith(None, "+", None, A, fc=3, sc=4), {}) == {A: 7}
    # red/green selection on operands
    p = dict(arith(IRON, "-", IRON, A), first_networks={"red": True, "green": False},
             second_networks={"red": False, "green": True})
    assert C.arithmetic_output(p, {IRON: 10}, {IRON: 3}) == {A: 7}


def test_arithmetic_each():
    inp = {IRON: 3, COPPER: 5}
    assert C.arithmetic_output(arith(EACH, "*", None, EACH, sc=2), inp) == {IRON: 6, COPPER: 10}
    assert C.arithmetic_output(arith(EACH, "*", None, A, sc=2), inp) == {A: 16}
    assert C.arithmetic_output(arith(EACH, "*", None, EACH, sc=2), {}) == {}
    assert C.arithmetic_output(arith(None, "-", EACH, EACH, fc=10), inp) == {IRON: 7, COPPER: 5}
    # each * X includes X itself (not excluded, unlike everything/anything)
    assert C.arithmetic_output(arith(EACH, "*", A, EACH), {A: 3, IRON: 2}) == {A: 9, IRON: 6}
    # each + 0 through a specific output can cancel to nothing
    assert C.arithmetic_output(arith(EACH, "+", None, A, sc=0), {IRON: 5, COPPER: -5}) == {}


# ------------------------------------------------------------------------------------ conditions

@pytest.mark.parametrize("cmp,v,k,want", [
    ("<", 1, 2, True), ("<", 2, 2, False), (">", 3, 2, True), (">", 2, 2, False),
    ("=", 2, 2, True), ("=", 1, 2, False), (">=", 2, 2, True), (">=", 1, 2, False),
    ("<=", 2, 2, True), ("<=", 3, 2, False), ("!=", 1, 2, True), ("!=", 2, 2, False),
    ("≥", 2, 2, True), ("≠", 2, 2, False),
])
def test_comparators(cmp, v, k, want):
    assert C.evaluate_condition(cond(A, cmp, k), {A: v}) is want


def test_absent_signals_compare_as_zero():
    assert C.evaluate_condition(cond(A, "=", 0), {}) is True
    assert C.evaluate_condition(cond(A, "<", None, second=B), {B: 1}) is True
    assert C.evaluate_condition(cond(A, ">", None, second=B), {A: -1}) is False


def test_empty_slots_and_lists_false():
    assert C.evaluate_condition(cond(None, "=", 0), {}) is False
    assert C.evaluate_condition([], {A: 1}) is False
    assert C.evaluate_condition(None, {A: 1}) is False


def test_everything_anything():
    assert C.evaluate_condition(cond(ALL, ">", 100), {}) is True   # vacuous
    assert C.evaluate_condition(cond(ANY, ">", -100), {}) is False
    assert C.evaluate_condition(cond(ALL, "!=", 0), {A: 5, B: -2}) is True
    assert C.evaluate_condition(cond(ANY, "=", 0), {A: 5}) is False
    assert C.evaluate_condition(cond(ALL, ">", 3), {A: 5, B: 4}) is True
    assert C.evaluate_condition(cond(ALL, ">", 4), {A: 5, B: 4}) is False
    assert C.evaluate_condition(cond(ANY, ">", 4), {A: 5, B: 4}) is True
    # a right-hand signal is excluded from the set
    assert C.evaluate_condition(cond(ALL, ">", None, second=B), {A: 5, B: 4}) is True
    assert C.evaluate_condition(cond(ANY, ">=", None, second=B), {B: 4}) is False


def test_and_binds_tighter_than_or():
    # A or B and C  ==  A or (B and C); left-to-right would be (A or B) and C
    conds = [cond(A, ">", 0), cond(B, ">", 0, join="or"), cond(S, ">", 0, join="and")]
    assert C.evaluate_condition(conds, {A: 1}) is True
    assert C.evaluate_condition(conds, {B: 1}) is False
    assert C.evaluate_condition(conds, {B: 1, S: 1}) is True
    # A and B or C and D
    conds = [cond(A, ">", 0), cond(B, ">", 0), cond(S, ">", 0, join="or"), cond(W, ">", 0)]
    assert C.evaluate_condition(conds, {A: 1, B: 1}) is True
    assert C.evaluate_condition(conds, {A: 1, W: 1}) is False
    assert C.evaluate_condition(conds, {S: 1, W: 1}) is True


def test_condition_networks():
    c = cond(A, ">", 5, fn={"red": True, "green": False})
    assert C.evaluate_condition(c, {A: 3}, {A: 10}) is False
    c = cond(A, ">", 5)
    assert C.evaluate_condition(c, {A: 3}, {A: 3}) is True  # both wires summed
    c = cond(A, ">", None, second=A, fn={"red": True, "green": False}, sn={"red": False, "green": True})
    assert C.evaluate_condition(c, {A: 4}, {A: 3}) is True


# --------------------------------------------------------------------------------------- decider

def test_decider_constant_and_copy():
    p = {"conditions": [cond(IRON, "<", 100)], "outputs": [out(A)]}
    assert C.decider_output(p, {IRON: 50}) == {A: 1}
    assert C.decider_output(p, {IRON: 150}) == {}
    p = {"conditions": [cond(IRON, "<", 100)], "outputs": [out(IRON, copy=True), out(B, const=7)]}
    assert C.decider_output(p, {IRON: 50}) == {IRON: 50, B: 7}
    p = {"conditions": [cond(A, ">", 0)],
         "outputs": [out(IRON, copy=True, nets={"red": False, "green": True})]}
    assert C.decider_output(p, {A: 1, IRON: 9}, {IRON: 4}) == {IRON: 4}


def test_decider_everything_anything_outputs():
    p = {"conditions": [cond(ALL, ">", 0)], "outputs": [out(ALL, copy=True)]}
    assert C.decider_output(p, {IRON: 3, A: 2}) == {IRON: 3, A: 2}
    assert C.decider_output(p, {}) == {}  # true on empty, nothing to copy
    p = {"conditions": [cond(ANY, ">", 4)], "outputs": [out(ANY, copy=True)]}
    # first match in signal order: sorted keys by default, Factoriopedia order when given
    assert C.decider_output(p, {A: 1, B: 9, IRON: 6}) == {IRON: 6}
    assert C.decider_output(p, {A: 1, B: 9, IRON: 6}, order=[B, IRON]) == {B: 9}


def test_decider_each():
    inp = {IRON: 3, COPPER: 10, A: 7}
    p = {"conditions": [cond(EACH, ">", 5)], "outputs": [out(EACH, copy=True)]}
    assert C.decider_output(p, inp) == {COPPER: 10, A: 7}
    p = {"conditions": [cond(EACH, ">", 5)], "outputs": [out(EACH)]}
    assert C.decider_output(p, inp) == {COPPER: 1, A: 1}
    p = {"conditions": [cond(EACH, ">", 5)], "outputs": [out(B)]}
    assert C.decider_output(p, inp) == {B: 2}  # count of passing signals
    p = {"conditions": [cond(EACH, ">", 5)], "outputs": [out(B, copy=True)]}
    assert C.decider_output(p, inp) == {B: 17}
    p = {"conditions": [cond(EACH, ">", 5)], "outputs": [out(ANY, copy=True)]}
    assert C.decider_output(p, inp) == {COPPER: 10}
    # each with no inputs: nothing, even for a condition true on zero
    p = {"conditions": [cond(EACH, "=", 0)], "outputs": [out(EACH)]}
    assert C.decider_output(p, {}) == {}
    # each mixed with a plain condition
    p = {"conditions": [cond(EACH, ">", 5), cond(B, "=", 1)], "outputs": [out(EACH, copy=True)]}
    assert C.decider_output(p, dict(inp, B=0)) == {}
    assert C.decider_output(p, dict(inp, **{B: 1})) == {COPPER: 10, A: 7}
    assert C.evaluate_condition(p["conditions"], dict(inp, **{B: 1})) is True


def test_decider_else_outputs():
    p = {"conditions": [cond(A, ">", 0)], "outputs": [out(B)], "else_outputs": [out(S, const=2)]}
    assert C.decider_output(p, {A: 1}) == {B: 1}
    assert C.decider_output(p, {}) == {S: 2}


# ------------------------------------------------------------------------------ constant, selector

def test_constant_output():
    p = {"enabled": True, "sections": [
        {"active": True, "multiplier": 1, "signals": {IRON: 10, A: 1}},
        {"active": False, "multiplier": 1, "signals": {IRON: 100}},
        {"active": True, "multiplier": 3, "signals": {IRON: 1, B: 2}},
    ]}
    assert C.constant_output(p) == {IRON: 13, A: 1, B: 6}
    assert C.constant_output(dict(p, enabled=False)) == {}
    assert C.constant_output({"sections": [{"signals": {A: 1, B: -1}}, {"signals": {B: 1}}]}) == {A: 1}


def test_selector():
    inp = {IRON: 30, COPPER: 10, A: 20}
    assert C.selector_output({"mode": "select"}, inp) == {IRON: 30}
    assert C.selector_output({"mode": "select", "select_max": False}, inp) == {COPPER: 10}
    assert C.selector_output({"mode": "select", "index_constant": 1}, inp) == {A: 20}
    assert C.selector_output({"mode": "select", "index_constant": 3}, inp) == {}
    assert C.selector_output({"mode": "select", "index_constant": 5}, {A: 4}) == {A: 4}
    assert C.selector_output({"mode": "select", "index_signal": B}, dict(inp, **{B: 2})) == {COPPER: 10}
    assert C.selector_output({"mode": "count", "count_signal": S}, inp) == {S: 3}
    assert C.selector_output({"mode": "count", "count_signal": S}, {}) == {}
    sizes = {"iron-plate": 100, "item/copper-plate": 50}
    assert C.selector_output({"mode": "stack-size"}, inp, stack_sizes=sizes) == {IRON: 100, COPPER: 50}
    assert C.selector_output({"mode": "stack-size"}, {IRON + "@rare": 1}, stack_sizes=sizes) == {
        IRON + "@rare": 100}
    for mode in ("random", "rocket-capacity", "quality-filter", "quality-transfer"):
        with pytest.raises(C.NotSimulated):
            C.selector_output({"mode": mode}, inp)
    with pytest.raises(C.NotSimulated):
        C.selector_output({"mode": "stack-size"}, inp)


# ------------------------------------------------------------------------------------- simulation

def chain():
    """Constant -> arith each+0 -> arith each+0 -> arith each*2, nets 1..4."""
    return snap({"1": {}, "2": {}, "3": {}, "4": {}}, [
        comb(1, "constant", {"enabled": True, "sections": [{"signals": {A: 5}}]}, out_red="1"),
        comb(2, "arithmetic", arith(EACH, "+", None, EACH, sc=0), in_red="1", out_red="2"),
        comb(3, "arithmetic", arith(EACH, "+", None, EACH, sc=0), in_red="2", out_red="3"),
        comb(4, "arithmetic", arith(EACH, "*", None, EACH, sc=2), in_red="3", out_red="4"),
    ])


def test_one_tick_delay_per_combinator():
    c = C.load(chain())
    r = c.run(10)
    h = r["history"]
    assert [h[t]["1"].get(A) for t in range(5)] == [None, 5, 5, 5, 5]
    assert [h[t]["2"].get(A) for t in range(5)] == [None, None, 5, 5, 5]
    assert [h[t]["3"].get(A) for t in range(5)] == [None, None, None, 5, 5]
    assert [h[t]["4"].get(A) for t in range(5)] == [None, None, None, None, 10]
    assert r["stable_at"] == 4 and r["cycle"] is None
    assert r["final"]["4"] == {A: 10}


def latch():
    # loop net "L" (red): decider in_red/out_red; set/reset net "I" (green) into in_green
    d = comb(1, "decider", {"conditions": [cond(S, ">", None, second=R)], "outputs": [out(S)]},
             in_red="L", in_green="I", out_red="L")
    return snap({"L": ("red", {}), "I": ("green", {})}, [d])


def test_sr_latch_holds():
    c = C.load(latch())
    c.step({"I": {S: 1}})          # set pulse; output appears next tick
    c.step()
    assert c.values["L"] == {S: 1}
    for _ in range(20):
        c.step()
    assert c.values["L"] == {S: 1}  # holds without the set signal
    c.step({"I": {R: 1}})
    assert c.values["L"] == {S: 1}  # this tick's output was computed from the old input
    c.step({"I": {R: 1}})
    assert c.values["L"] == {}      # reset took
    r = c.run(30)  # tick 0 still shows the reset input on I; from tick 1 nothing changes
    assert r["final"] == {"L": {}, "I": {}} and r["stable_at"] == 1


def clock(start=None):
    return snap({"1": {A: start} if start else {}}, [
        comb(1, "constant", {"enabled": True, "sections": [{"signals": {A: 1}}]}, out_red="1"),
        comb(2, "decider", {"conditions": [cond(A, "<", 10)], "outputs": [out(A, copy=True)]},
             in_red="1", out_red="1"),
    ])


def test_clock_oscillates_with_period():
    c = C.load(clock())
    r = c.run(30, until_stable=False)
    seq = [h["1"].get(A, 0) for h in r["history"]]
    assert seq[1:13] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1, 2]
    assert r["cycle"] == {"start": 1, "period": 10} and r["stable_at"] is None


def test_clock_snapshot_mid_cycle_not_distorted():
    c = C.load(clock(start=5))
    assert c.exogenous() == {"1": {}}  # no non-combinator source on the net
    r = c.run(100)
    assert r["cycle"]["period"] == 10
    assert [h["1"][A] for h in r["history"][:7]] == [5, 6, 7, 8, 9, 10, 1]


def memory():
    # loop L (red), data/write G (green). hold: W[green]=0 -> everything[red]; write: W>0 -> iron[green]
    g = {"red": False, "green": True}
    rd = {"red": True, "green": False}
    hold = comb(1, "decider", {"conditions": [cond(W, "=", 0, fn=g)], "outputs": [out(ALL, copy=True, nets=rd)]},
                in_red="L", in_green="G", out_red="L")
    write = comb(2, "decider", {"conditions": [cond(W, ">", 0, fn=g)], "outputs": [out(IRON, copy=True, nets=g)]},
                 in_red="L", in_green="G", out_red="L")
    return snap({"L": ("red", {}), "G": ("green", {})}, [hold, write])


def test_memory_cell():
    c = C.load(memory())
    c.step({"G": {IRON: 42, W: 1}})
    c.step()
    assert c.values["L"] == {IRON: 42}
    r = c.run(50)
    assert r["final"]["L"] == {IRON: 42} and r["stable_at"] == 0
    c.step({"G": {IRON: 7, W: 1}})
    c.step({"G": {IRON: 7, W: 1}})   # holding write for 2 ticks still stores 7, not 49
    assert c.values["L"] == {IRON: 7}
    c.step()
    c.step()
    assert c.values["L"] == {IRON: 7}


def station(plates=4000):
    chest = other(10, "steel-chest", "container", red="1")
    stop = other(20, "train-stop", "train-stop", red="2", control={
        "enable_condition": cond(L, ">", 0),
        "read": {"stopped_train": False},
        "set": {"trains_limit": L},
        "raw": {"set_trains_limit": True, "trains_limit_signal": L}})
    div = comb(30, "arithmetic", arith(IRON, "/", None, L, sc=1000), in_red="1", out_red="2")
    return snap({"1": {IRON: plates}, "2": {L: plates // 1000} if plates >= 1000 else {}},
                [chest, stop, div])


def test_station_trains_limit_from_chest():
    c = C.load(station())
    assert c.exogenous() == {"1": {IRON: 4000}, "2": {}}
    assert c.controls() == [{"id": 20, "name": "train-stop", "type": "train-stop", "enabled": True,
                             "set": {"trains_limit": 4}}]
    r = c.run(10)
    assert r["stable_at"] == 0  # snapshot is steady
    empty = c.with_inputs({10: {}})
    empty.run(5)
    assert empty.controls()[0]["enabled"] is False
    assert empty.controls()[0]["set"] == {"trains_limit": 0}
    half = c.with_inputs({"network:1": {IRON: 2500}})
    half.run(5)
    assert half.controls()[0]["set"]["trains_limit"] == 2
    assert c.controls()[0]["set"]["trains_limit"] == 4  # original untouched
    assert c.trace(L, "2") == [{"id": 30, "name": "arithmetic-combinator", "kind": "arithmetic", "value": 4}]
    assert c.trace(IRON, "1") == [{"id": None, "name": None, "kind": "exogenous", "value": 4000,
                                   "sources": [10]}]
    text = C.explain(c)
    assert "virtual/signal-L = item/iron-plate / 1000" in text
    assert "enabled when virtual/signal-L > 0" in text and "trains_limit from virtual/signal-L" in text
    assert "now enabled, trains_limit=4" in text


def test_with_inputs_ambiguous_and_unknown():
    s = station()
    s["entities"].append(other(11, "steel-chest", "container", red="1"))
    c = C.load(s)
    with pytest.raises(ValueError, match="other sources"):
        c.with_inputs({10: {}})
    with pytest.raises(ValueError, match="unknown"):
        c.with_inputs({"99": {}})
    assert c.with_inputs({"1": {IRON: 1000}}).run(3)["final"]["2"] == {L: 1}


def test_unsimulated_selector_folded_into_exogenous():
    s = snap({"1": {A: 3}, "2": {A: 3}}, [
        comb(1, "constant", {"sections": [{"signals": {A: 3}}]}, out_red="1"),
        comb(2, "selector", {"mode": "random"}, in_red="1", out_red="2"),
    ])
    c = C.load(s)
    assert c.unsimulated == [2]
    assert c.exogenous()["2"] == {A: 3}
    assert c.run(5)["stable_at"] == 0
    assert c.trace(A, "2")[0]["sources"] == [2]
    assert "not simulated" in C.explain(c)


def test_determinism_and_json():
    a = C.load(clock()).run(40, until_stable=False)
    b = C.load(clock()).run(40, until_stable=False)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["ticks"] == 40 and a["cycle"]["period"] == 10
    assert C.explain(clock()) == C.explain(clock())


# ------------------------------------------------------------------------------- check, explain, diff

def test_check_clean_and_problems():
    assert C.check(station()) == []
    assert C.check(chain()) == []
    s = station()
    s["v"] = 2
    s["truncated"] = True
    s["networks"]["2"]["color"] = "green"
    s["entities"][2]["params"]["operation"] = "@"
    s["entities"][1]["connectors"]["green"] = "77"
    s["entities"].append(comb(40, "decider", {"conditions": [cond(A, "~", 1)], "outputs": [out(EACH)]}))
    s["networks"]["1"]["signals"]["iron"] = 1.5
    probs = C.check(s)
    text = "\n".join(probs)
    for needle in ("unsupported version", "warning: truncated", "unknown operation '@'",
                   "unknown network 77", "red on green network 2", "unknown comparator '~'",
                   "Each output needs Each", "bad signal key 'iron'", "is not an integer"):
        assert needle in text, needle
    with pytest.raises(ValueError, match="bad circuit snapshot"):
        C.load(s)
    assert isinstance(C.load(s, strict=False), C.Circuit)


def test_check_warnings_not_fatal():
    s = clock()
    s["truncated"] = True
    s["networks"]["1"]["signals"] = {A: 0}
    probs = C.check(s)
    assert probs and all(p.startswith("warning:") for p in probs)
    C.load(s)


def test_explain_entities():
    p = {"conditions": [cond(A, ">", 0), cond(B, "<", 5, join="or"), cond(S, "=", 1)],
         "outputs": [out(EACH, copy=True)], "else_outputs": [out(W)]}
    t = C.explain(comb(1, "decider", p))
    assert t == ("decider-combinator #1: if virtual/signal-A > 0 or (virtual/signal-B < 5 and "
                 "virtual/signal-S = 1) then each = input count else virtual/signal-W = 1")
    t = C.explain(comb(2, "arithmetic", dict(arith(EACH, "*", None, EACH, sc=2),
                                             first_networks={"red": True, "green": False})))
    assert t == "arithmetic-combinator #2: each = each[red] * 2"
    assert "switched off" in C.explain(comb(3, "constant", {"enabled": False}))
    assert C.explain(comb(4, "selector", {"mode": "select", "select_max": False})) == \
        "selector-combinator #4: select lowest #0"


def test_diff_add_sub():
    assert C.diff({A: 1, B: 2}, {B: 3, S: 1}) == {A: [1, 0], B: [2, 3], S: [0, 1]}
    assert C.diff({A: 1}, {A: 1}) == {}
    assert C.diff({"1": {A: 1}, "2": {}}, {"1": {A: 1}, "2": {B: 1}}) == {"2": {B: [0, 1]}}
    assert C.add({A: 2147483647}, {A: 1}) == {A: -2147483648}
    assert C.sub({A: 3, B: 1}, {A: 3}) == {B: 1}


@pytest.mark.skipif(not Path("/usr/bin/python3").exists() or shutil.which("timeout") is None,
                    reason="no system python3 / timeout")
def test_python39_import():
    code = ("import kc.circuits as C;"
            "print(C.arithmetic_output({'first':'virtual/signal-each','operation':'*',"
            "'second_constant':2,'output':'virtual/signal-each'},{'item/a':3}))")
    r = subprocess.run(["timeout", "60", "/usr/bin/python3", "-c", code], cwd=str(ROOT / "mcp"),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "{'item/a': 6}"


# ---------------------------------------------------------------------------- recorded fixtures

FIXTURES = sorted(FIX.rglob("*.json")) if FIX.exists() else []


@pytest.mark.skipif(not FIXTURES, reason="no recorded circuit snapshots yet")
@pytest.mark.parametrize("path", FIXTURES, ids=[str(p.relative_to(FIX)) for p in FIXTURES])
def test_recorded_fixture(path):
    s = json.loads(path.read_text())
    if not isinstance(s, dict) or "networks" not in s:
        pytest.skip("not a snapshot")
    assert [p for p in C.check(s) if not p.startswith("warning:")] == []
    c = C.load(s)
    r = c.run(200)
    assert r["ticks"] >= 1
    json.dumps(r)
    assert C.explain(c)


def test_unpowered_combinator_outputs_nothing():
    """Found by the in-game test: without power a decider passes nothing, so a clock stays at its seed."""
    snap = {"v": 1, "tick": 0, "networks": {"1": {"color": "red", "signals": {"virtual/signal-T": 1}}},
            "entities": [
                {"id": 1, "name": "constant-combinator", "type": "constant-combinator", "x": 0.5, "y": 0.5, "kind": "constant",
                 "connectors": {"red": "1"}, "params": {"enabled": True, "sections": [{"active": True, "multiplier": 1, "signals": {"virtual/signal-T": 1}}]}},
                {"id": 2, "name": "decider-combinator", "type": "decider-combinator", "x": 3, "y": 0.5, "kind": "decider",
                 "connectors": {"in_red": "1", "out_red": "1"},
                 "params": {"conditions": [{"first": "virtual/signal-T", "comparator": "<", "constant": 60}],
                            "outputs": [{"signal": "virtual/signal-T", "copy_count_from_input": True}]}}]}
    on = C.load(snap)
    on.run(5, until_stable=False)
    assert on.values["1"]["virtual/signal-T"] == 6
    snap["entities"][1]["status"] = "no_power"
    off = C.load(snap)
    off.run(5, until_stable=False)
    assert off.values["1"]["virtual/signal-T"] == 1
