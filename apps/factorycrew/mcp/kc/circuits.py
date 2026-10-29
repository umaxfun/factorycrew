"""Circuit networks: validate, simulate, explain a snapshot from the mod's Lua `kc.circuits` reader.

Format: docs/circuits-format.md (v1). Signals are "<type>/<name>[@quality]" keys, a signal set is
{key: int} with zero counts omitted, networks are keyed by string ids.

  load(snapshot, order=None, exogenous=None, strict=True) -> Circuit   (raises ValueError on errors)
  check(snapshot) -> [problem]                format problems ("warning: ..." ones are not fatal)
  Circuit.step(inputs=None) -> values         advance one tick
  Circuit.run(ticks, inputs=None, until_stable=True) -> {history, stable_at, cycle, final, ticks}
  Circuit.exogenous() -> {net: signals}       what non-combinator entities put on each network
  Circuit.with_inputs({net_or_entity: signals}) -> Circuit   scenario: replace those contributions
  Circuit.controls(values=None) -> [{id, name, enabled, set}]  what controlled entities see
  Circuit.trace(signal, network) -> [contributor]
  evaluate_condition / decider_output / arithmetic_output / constant_output / selector_output
  explain(circuit | snapshot | entity) -> str,  diff(a, b),  add(*sets),  sub(a, b),  wrap(n)

Semantics (Factorio 2.0), checked against the wiki on 2026-10-29:
  https://wiki.factorio.com/Decider_combinator   multi-condition, AND binds tighter than OR,
      per-signal red/green selection (both = sum), output constant or input count, 1 tick latency,
      the Each/Anything/Everything output table.
  https://wiki.factorio.com/Arithmetic_combinator  operations, truncating division, modulo takes the
      sign of the left operand, arithmetic right shift, each-in/each-out vs each-in/signal-out (sum).
  https://wiki.factorio.com/Circuit_network  32-bit two's complement wraparound; Everything is true
      on no signals, Anything false; a signal on the right of Everything/Anything is excluded from
      the set, but not for Each; Each only iterates non-zero signals; Anything in input and output
      returns the first match in Factoriopedia order.
  https://wiki.factorio.com/Selector_combinator  select (descending default, ascending = lowest,
      index out of bounds -> nothing unless there is a single input), count, stack size.
Factoriopedia order is not in the snapshot; pass `order` (list of keys) to get the game's choice
for Anything outputs and select ties, otherwise sorted keys are used.

Not simulated (outputs treated as unknown; the observed output is folded into the network's
exogenous part, so a steady snapshot stays steady): selector random, rocket-capacity,
quality-filter, quality-transfer, quality-select, time; stack-size without `stack_sizes`.
Not verified against the game (documented choice): shift counts are masked to 5 bits (x86/C++
behaviour); `^` is exact integer power wrapped to 32 bits, negative exponent gives 0 except for
bases 1 and -1; constant section multiplier truncates toward zero; INT_MIN / -1 wraps to INT_MIN.
Optional `else_outputs` on a decider (2.1) is honoured when present.

Prior art searched (GitHub, 2026-10-29): nforrester/factorio-hdl (MIT, C++, commit 309979a,
1.1-era single-condition model) was used to cross-check the arithmetic operations and each
handling; nothing ported (its `^` goes through double pow and its shifts are C UB beyond 31).
Factsimguru/Factsim (GPL-3.0) and feilb/Conduit (GPL-3.0) are simulators but not portable under
their licenses; factorio-draftsman (MIT) models 2.0 combinator parameters but does not simulate.
This module is written from the wiki pages above.

Stdlib only, Python 3.9.
"""
from __future__ import annotations

import copy
import json

EVERYTHING = "virtual/signal-everything"
ANYTHING = "virtual/signal-anything"
EACH = "virtual/signal-each"
WILDCARDS = (EVERYTHING, ANYTHING, EACH)
COMPARATORS = ("<", ">", "=", ">=", "<=", "!=")
OPERATIONS = ("+", "-", "*", "/", "%", "^", "<<", ">>", "AND", "OR", "XOR")
KINDS = ("decider", "arithmetic", "constant", "selector", "other")
COMBINATORS = ("decider", "arithmetic", "constant", "selector")
SELECTOR_MODES = ("select", "count", "random", "stack-size", "rocket-capacity", "quality-filter",
                  "quality-transfer", "quality-select", "time")
_OP_ALIASES = {"&": "AND", "|": "OR", "and": "AND", "or": "OR", "xor": "XOR", "**": "^"}
_CMP_ALIASES = {"≥": ">=", "≤": "<=", "≠": "!=", "==": "="}
_BOTH = {"red": True, "green": True}


# Entity statuses (snapshot `status`) in which a powered combinator computes nothing.
OFF_STATUSES = frozenset({"no_power", "not_plugged_in_electric_network", "disabled_by_script", "marked_for_deconstruction"})

class NotSimulated(Exception):
    """A combinator mode this module does not compute."""


# ---------------------------------------------------------------------------------------- signals

def wrap(n):
    """n as a signed 32-bit integer (the game's wraparound)."""
    n = int(n) & 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def _clean(d):
    return {k: wrap(v) for k, v in sorted(d.items()) if wrap(v) != 0}


def add(*sets):
    """Sum of signal sets, wrapped, zeros dropped."""
    out = {}
    for s in sets:
        for k, v in (s or {}).items():
            out[k] = out.get(k, 0) + int(v)
    return _clean(out)


def sub(a, b):
    """a - b as a signal set."""
    return add(a, {k: -int(v) for k, v in (b or {}).items()})


def diff(a, b):
    """Signals that differ between two sets: {key: [count_in_a, count_in_b]} (absent = 0), sorted.
    Given two {network: signals} maps instead, returns {network: {key: [a, b]}} for differing nets."""
    a, b = a or {}, b or {}
    vals = list(a.values()) + list(b.values())
    if vals and all(isinstance(v, dict) for v in vals):
        out = {}
        for n in sorted(set(a) | set(b), key=_netkey):
            d = diff(_sig(a.get(n)), _sig(b.get(n)))
            if d:
                out[n] = d
        return out
    return {k: [int(a.get(k, 0)), int(b.get(k, 0))]
            for k in sorted(set(a) | set(b)) if int(a.get(k, 0)) != int(b.get(k, 0))}


def _sig(net):
    """A network entry ({color, signals}) or a bare signal set -> the signal set."""
    if net is None:
        return {}
    if isinstance(net.get("signals"), dict):
        return net["signals"]
    return net


def _netkey(n):
    s = str(n)
    return (0, int(s), s) if s.lstrip("-").isdigit() else (1, 0, s)


def _nets(sel):
    if not sel:
        return _BOTH
    return {"red": bool(sel.get("red", False)), "green": bool(sel.get("green", False))}


def _pick(red, green, sel):
    """Input as seen through a red/green selection (both = sum)."""
    sel = _nets(sel)
    return add(red if sel["red"] else None, green if sel["green"] else None)


def _ordered(keys, order):
    keys = sorted(keys)
    if not order:
        return keys
    rank = {k: i for i, k in enumerate(order)}
    return sorted(keys, key=lambda k: (rank.get(k, len(rank)), k))


# ------------------------------------------------------------------------------------- conditions

def _cmp(a, op, b):
    op = _CMP_ALIASES.get(op, op)
    if op == "<":
        return a < b
    if op == ">":
        return a > b
    if op == "=":
        return a == b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == "!=":
        return a != b
    raise ValueError(f"unknown comparator {op!r}")


def _one(c, red, green, each):
    first = c.get("first")
    if not first:
        return False
    op = c.get("comparator", "<")
    second = c.get("second")
    if second == EACH:
        right = _pick(red, green, c.get("second_networks")).get(each, 0) if each else 0
    elif second:
        right = _pick(red, green, c.get("second_networks")).get(second, 0)
    else:
        right = int(c.get("constant") or 0)
    left = _pick(red, green, c.get("first_networks"))
    if first in (EVERYTHING, ANYTHING):
        vals = [v for k, v in sorted(left.items())
                if not (second and second not in WILDCARDS and k == second)]
        if first == EVERYTHING:
            return all(_cmp(v, op, right) for v in vals)
        return any(_cmp(v, op, right) for v in vals)
    if first == EACH:
        return _cmp(left.get(each, 0) if each else 0, op, right)
    return _cmp(left.get(first, 0), op, right)


def _groups(conds):
    groups = []
    for i, c in enumerate(conds):
        if i == 0 or str(c.get("join", "and")).lower() != "or":
            if not groups:
                groups.append([])
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def _conds(cond):
    if cond is None:
        return []
    return [cond] if isinstance(cond, dict) else list(cond)


def _has_each(conds):
    return any(c.get("first") == EACH or c.get("second") == EACH for c in conds)


def each_signals(cond, red, green=None):
    """Signals an Each in these conditions iterates over: non-zero on the networks of the slots
    that hold Each, sorted."""
    keys = set()
    for c in _conds(cond):
        for slot in ("first", "second"):
            if c.get(slot) == EACH:
                keys |= set(_pick(red, green, c.get(slot + "_networks")))
    return sorted(keys)


def evaluate_condition(cond, red, green=None, each=None):
    """A condition or a list of them, exactly as the game: AND binds tighter than OR, an empty slot
    is false, an absent signal is 0, Everything is true on no signals, Anything false, a signal on
    the right of Everything/Anything is left out of the set. An empty list is false.
    With Each in the conditions: `each` binds the signal; unbound, the result is whether the
    conditions hold for at least one signal (i.e. would a decider output anything)."""
    conds = _conds(cond)
    if not conds:
        return False
    if each is None and _has_each(conds):
        return any(evaluate_condition(conds, red, green, s) for s in each_signals(conds, red, green))
    return any(all(_one(c, red or {}, green or {}, each) for c in g) for g in _groups(conds))


# ------------------------------------------------------------------------------------ combinators

def _out_value(o, inp, sig):
    if o.get("copy_count_from_input"):
        return inp.get(sig, 0)
    c = o.get("constant")
    return 1 if c is None else int(c)


def _decider_outputs(outputs, conds, red, green, passing, order):
    out = {}
    has_each = passing is not None

    def put(k, v):
        out[k] = out.get(k, 0) + v

    for o in outputs or []:
        sig = o.get("signal")
        if not sig:
            continue
        inp = _pick(red, green, o.get("networks"))
        if has_each:
            if sig == EACH:
                for s in passing:
                    put(s, _out_value(o, inp, s))
            elif sig == ANYTHING:
                if passing:
                    s = _ordered(passing, order)[0]
                    put(s, _out_value(o, inp, s))
            elif sig == EVERYTHING:
                continue  # not allowed with Each (check() reports it)
            else:
                for s in passing:
                    put(sig, _out_value(o, inp, s))
        else:
            if sig == EVERYTHING:
                for s in sorted(inp):
                    put(s, _out_value(o, inp, s))
            elif sig == ANYTHING:
                cands = sorted(inp)
                matched = [s for s in cands if _anything_match(conds, red, green, s)]
                pool = matched or cands
                if pool:
                    s = _ordered(pool, order)[0]
                    put(s, _out_value(o, inp, s))
            elif sig == EACH:
                continue  # not allowed without Each in the input
            else:
                put(sig, _out_value(o, inp, sig))
    return _clean(out)


def _anything_match(conds, red, green, s):
    for c in conds:
        if c.get("first") == ANYTHING:
            one = dict(c, first=s)
            if _one(one, red, green, None):
                return True
    return False


def decider_output(params, red, green=None, order=None):
    """Output signal set of a decider combinator for this input. `params` as in the format:
    {conditions, outputs[, else_outputs]}. Each in a condition evaluates per input signal; an Each
    output emits the passing signals, a specific output sums over them, Anything picks one."""
    red, green = red or {}, green or {}
    conds = _conds(params.get("conditions"))
    if _has_each(conds):
        sigs = each_signals(conds, red, green)
        ok = [s for s in sigs if evaluate_condition(conds, red, green, s)]
        bad = [s for s in sigs if s not in ok]
        return add(_decider_outputs(params.get("outputs"), conds, red, green, ok, order),
                   _decider_outputs(params.get("else_outputs"), conds, red, green, bad, order)
                   if bad else {})
    ok = evaluate_condition(conds, red, green)
    key = "outputs" if ok else "else_outputs"
    return _decider_outputs(params.get(key), conds, red, green, None, order)


def arith(op, a, b):
    """One arithmetic-combinator operation on 32-bit signed ints."""
    op = _OP_ALIASES.get(op, op)
    a, b = wrap(a), wrap(b)
    if op == "+":
        return wrap(a + b)
    if op == "-":
        return wrap(a - b)
    if op == "*":
        return wrap(a * b)
    if op == "/":
        if b == 0:
            return 0
        q = abs(a) // abs(b)
        return wrap(q if (a >= 0) == (b >= 0) else -q)
    if op == "%":
        if b == 0:
            return 0
        r = abs(a) % abs(b)
        return wrap(r if a >= 0 else -r)
    if op == "^":
        if b < 0:
            if a == 1:
                return 1
            if a == -1:
                return 1 if b % 2 == 0 else -1
            return 0
        return wrap(pow(a, b, 0x100000000))
    if op == "<<":
        return wrap(a << (b & 31))
    if op == ">>":
        return wrap(a >> (b & 31))
    if op == "AND":
        return wrap(a & b)
    if op == "OR":
        return wrap(a | b)
    if op == "XOR":
        return wrap(a ^ b)
    raise ValueError(f"unknown operation {op!r}")


def arithmetic_output(params, red, green=None):
    """Output of an arithmetic combinator. Each in one operand runs the operation per non-zero input
    signal; Each output keeps each result on its signal, a specific output gets their sum."""
    red, green = red or {}, green or {}
    op = params.get("operation", "*")
    out_sig = params.get("output")

    def operand(slot):
        s = params.get(slot)
        if s and s != EACH:
            return _pick(red, green, params.get(slot + "_networks")).get(s, 0)
        return int(params.get(slot + "_constant") or 0)

    each_slot = next((s for s in ("first", "second") if params.get(s) == EACH), None)
    if each_slot is None:
        if not out_sig or out_sig in WILDCARDS:
            return {}
        return _clean({out_sig: arith(op, operand("first"), operand("second"))})
    other = "second" if each_slot == "first" else "first"
    ov = operand(other)
    inp = _pick(red, green, params.get(each_slot + "_networks"))
    out = {}
    for s in sorted(inp):
        r = arith(op, inp[s], ov) if each_slot == "first" else arith(op, ov, inp[s])
        if out_sig == EACH:
            out[s] = out.get(s, 0) + r
        elif out_sig and out_sig not in WILDCARDS:
            out[out_sig] = out.get(out_sig, 0) + r
    return _clean(out)


def constant_output(params):
    """What a constant combinator emits: active sections times their multiplier (truncated)."""
    if not params or params.get("enabled", True) is False:
        return {}
    out = {}
    sections = params.get("sections")
    if sections is None and isinstance(params.get("signals"), dict):
        sections = [{"signals": params["signals"]}]
    for sec in sections or []:
        if sec.get("active", True) is False:
            continue
        m = sec.get("multiplier", 1)
        for k, v in (sec.get("signals") or {}).items():
            out[k] = out.get(k, 0) + int(v * m)
    return _clean(out)


def _base(key):
    return key.split("@", 1)[0]


def selector_output(params, red, green=None, stack_sizes=None, order=None):
    """Selector combinator output for modes select, count and stack-size (stack_sizes: {item name
    or signal key: size}). Other modes raise NotSimulated."""
    inp = add(red, green)
    mode = params.get("mode") or params.get("operation") or "select"
    if mode == "select":
        desc = params.get("select_max", True)
        ranked = _ordered(inp, order)
        ranked.sort(key=lambda k: -inp[k] if desc else inp[k])  # stable: ties keep `order`
        if params.get("index_signal"):
            idx = inp.get(params["index_signal"], 0)
        else:
            idx = int(params.get("index_constant") or params.get("index") or 0)
        if len(ranked) == 1:
            return {ranked[0]: inp[ranked[0]]}
        if 0 <= idx < len(ranked):
            return {ranked[idx]: inp[ranked[idx]]}
        return {}
    if mode == "count":
        sig = params.get("count_signal")
        return _clean({sig: len(inp)}) if sig else {}
    if mode == "stack-size":
        if stack_sizes is None:
            raise NotSimulated("stack-size needs stack_sizes")
        out = {}
        for k in sorted(inp):
            if not k.startswith("item/"):
                continue
            name = _base(k)[len("item/"):]
            size = stack_sizes.get(k, stack_sizes.get(_base(k), stack_sizes.get(name)))
            if size:
                out[k] = int(size)
        return _clean(out)
    raise NotSimulated(f"selector mode {mode!r} is not simulated")


# ------------------------------------------------------------------------------------------- check

def _is_key(s):
    return isinstance(s, str) and "/" in s and s.split("/", 1)[1] != ""


def _check_sigset(where, d, out):
    if not isinstance(d, dict):
        out.append(f"{where}: signals is not an object")
        return
    for k, v in sorted(d.items()):
        if not _is_key(k):
            out.append(f"{where}: bad signal key {k!r} (want '<type>/<name>')")
        if isinstance(v, bool) or not isinstance(v, int):
            out.append(f"{where}: {k} count {v!r} is not an integer")
        elif v == 0:
            out.append(f"warning: {where}: {k} has a zero count (should be omitted)")


def _check_cond(where, c, out, allow_each):
    if not isinstance(c, dict):
        out.append(f"{where}: condition is not an object")
        return
    op = _CMP_ALIASES.get(c.get("comparator"), c.get("comparator"))
    if op not in COMPARATORS:
        out.append(f"{where}: unknown comparator {c.get('comparator')!r}")
    for slot in ("first", "second"):
        s = c.get(slot)
        if s is not None and not _is_key(s):
            out.append(f"{where}: {slot} {s!r} is not a signal key")
    if c.get("second") in (EVERYTHING, ANYTHING):
        out.append(f"{where}: {c['second']} cannot be the second operand")
    if not allow_each and EACH in (c.get("first"), c.get("second")):
        out.append(f"{where}: Each is only allowed in combinators")
    if c.get("second") is not None and c.get("constant") is not None:
        out.append(f"warning: {where}: both second and constant set; second wins")
    k = c.get("constant")
    if k is not None and (isinstance(k, bool) or not isinstance(k, int)):
        out.append(f"{where}: constant {k!r} is not an integer")
    j = c.get("join", "and")
    if str(j).lower() not in ("and", "or"):
        out.append(f"{where}: join {j!r} is not and/or")


def _check_params(where, kind, p, out):
    if kind == "decider":
        conds = _conds(p.get("conditions"))
        if not conds:
            out.append(f"warning: {where}: no conditions (never true)")
        for i, c in enumerate(conds):
            _check_cond(f"{where} condition {i + 1}", c, out, True)
        each = _has_each(conds)
        for key in ("outputs", "else_outputs"):
            for i, o in enumerate(p.get(key) or []):
                s = o.get("signal")
                w = f"{where} {key[:-1]} {i + 1}"
                if s is not None and not _is_key(s):
                    out.append(f"{w}: signal {s!r} is not a signal key")
                if s == EACH and not each:
                    out.append(f"{w}: Each output needs Each in a condition")
                if s == EVERYTHING and each:
                    out.append(f"{w}: Everything output is not allowed with Each in a condition")
        if not p.get("outputs") and not p.get("else_outputs"):
            out.append(f"warning: {where}: no outputs")
    elif kind == "arithmetic":
        op = _OP_ALIASES.get(p.get("operation"), p.get("operation"))
        if op not in OPERATIONS:
            out.append(f"{where}: unknown operation {p.get('operation')!r}")
        if p.get("first") == EACH and p.get("second") == EACH:
            out.append(f"{where}: Each in both operands")
        for slot in ("first", "second"):
            s = p.get(slot)
            if s in (EVERYTHING, ANYTHING):
                out.append(f"{where}: {s} is not allowed as an operand")
            elif s is not None and not _is_key(s):
                out.append(f"{where}: {slot} {s!r} is not a signal key")
        o = p.get("output")
        if o == EACH and EACH not in (p.get("first"), p.get("second")):
            out.append(f"{where}: Each output needs Each in an operand")
        if o in (EVERYTHING, ANYTHING):
            out.append(f"{where}: {o} is not allowed as the output")
        if o is None:
            out.append(f"warning: {where}: no output signal")
    elif kind == "constant":
        for i, sec in enumerate(p.get("sections") or []):
            _check_sigset(f"{where} section {i + 1}", sec.get("signals") or {}, out)
    elif kind == "selector":
        mode = p.get("mode") or p.get("operation")
        if mode not in SELECTOR_MODES:
            out.append(f"{where}: unknown selector mode {mode!r}")


def check(snapshot):
    """Format problems of a circuit snapshot ([] = fine). Lines starting with "warning:" are not
    fatal (a truncated snapshot, zero counts, empty combinators)."""
    out = []
    if not isinstance(snapshot, dict):
        return ["snapshot is not an object"]
    if snapshot.get("v") != 1:
        out.append(f"unsupported version v={snapshot.get('v')!r} (want 1)")
    if snapshot.get("truncated"):
        out.append("warning: truncated: networks may be incomplete; re-read a smaller area")
    nets = snapshot.get("networks")
    if not isinstance(nets, dict):
        out.append("missing networks")
        nets = {}
    for nid, n in sorted(nets.items(), key=lambda kv: _netkey(kv[0])):
        if not isinstance(n, dict):
            out.append(f"network {nid}: not an object")
            continue
        if n.get("color") not in ("red", "green"):
            out.append(f"network {nid}: color {n.get('color')!r} is not red/green")
        _check_sigset(f"network {nid}", n.get("signals", {}), out)
    ents = snapshot.get("entities")
    if not isinstance(ents, list):
        out.append("missing entities")
        ents = []
    seen = set()
    for i, e in enumerate(ents):
        where = f"entity {e.get('id', '#' + str(i))} ({e.get('name')})"
        if "id" not in e:
            out.append(f"entity #{i}: missing id")
        elif e["id"] in seen:
            out.append(f"{where}: duplicate id")
        seen.add(e.get("id"))
        kind = e.get("kind")
        if kind not in KINDS:
            out.append(f"{where}: unknown kind {kind!r}")
        cons = e.get("connectors") or {}
        if not isinstance(cons, dict):
            out.append(f"{where}: connectors is not an object")
            cons = {}
        for cname, nid in sorted(cons.items()):
            if nid is None:
                continue
            nid = str(nid)
            if nid not in nets:
                out.append(f"{where}: {cname} -> unknown network {nid}")
                continue
            color = "red" if cname.endswith("red") else "green" if cname.endswith("green") else None
            if color and isinstance(nets[nid], dict) and nets[nid].get("color") not in (None, color):
                out.append(f"{where}: {cname} on {nets[nid].get('color')} network {nid}")
        if kind in COMBINATORS:
            _check_params(where, kind, e.get("params") or {}, out)
        ctl = e.get("control") or {}
        if ctl.get("enable_condition"):
            for j, c in enumerate(_conds(ctl["enable_condition"])):
                _check_cond(f"{where} enable condition {j + 1}", c, out, False)
    return out


# ----------------------------------------------------------------------------------------- Circuit

def _canon(values):
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


class Circuit:
    """A loaded snapshot you can step. `values` is the current {network id: signals}."""

    def __init__(self, snapshot, order=None, exogenous=None, stack_sizes=None):
        self.snapshot = snapshot
        self.order = list(order) if order else None
        self.stack_sizes = stack_sizes
        self.colors = {}
        self.observed = {}
        for nid, n in sorted((snapshot.get("networks") or {}).items(), key=lambda kv: _netkey(kv[0])):
            self.colors[str(nid)] = n.get("color")
            self.observed[str(nid)] = _clean(n.get("signals") or {})
        self.entities = {}
        for e in snapshot.get("entities") or []:
            self.entities[e["id"]] = e
        self.ids = sorted(self.entities, key=lambda i: (str(type(i)), i))
        self.unsimulated = []
        self.values = copy.deepcopy(self.observed)
        self.tick = 0
        if exogenous is None:
            self._exo = self._infer_exogenous()
        else:
            self._exo = {n: _clean(_sig(exogenous.get(n))) for n in self.observed}

    # --- wiring
    def _con(self, e, name):
        n = (e.get("connectors") or {}).get(name)
        return None if n is None else str(n)

    def inputs_of(self, eid, values=None):
        """(red, green) signal sets an entity reads: combinators from in_red/in_green, others from
        red/green."""
        e = self.entities[eid]
        values = self.values if values is None else values
        pre = "in_" if e.get("kind") in COMBINATORS else ""
        r, g = self._con(e, pre + "red"), self._con(e, pre + "green")
        return (values.get(r, {}) if r else {}), (values.get(g, {}) if g else {})

    def out_networks(self, eid):
        """Networks a combinator writes to (constant combinators: any connector they have)."""
        e = self.entities[eid]
        names = ("out_red", "out_green")
        if e.get("kind") == "constant":
            names = ("out_red", "out_green", "red", "green")
        return sorted({n for n in (self._con(e, c) for c in names) if n}, key=_netkey)

    def attached(self, network):
        """Ids of entities with any connector on this network, sorted."""
        network = str(network)
        return [i for i in self.ids
                if network in {str(v) for v in (self.entities[i].get("connectors") or {}).values()
                               if v is not None}]

    def sources(self, network):
        """Non-combinator entities on a network (the possible exogenous contributors)."""
        network = str(network)
        out = []
        for i in self.attached(network):
            e = self.entities[i]
            if e.get("kind") == "other" and network in (self._con(e, "red"), self._con(e, "green")):
                out.append(i)
        return out

    # --- outputs
    def output_of(self, eid, values=None):
        """Signal set one combinator puts on each of its output networks, from `values`.
        Raises NotSimulated for modes that are not computed."""
        e = self.entities[eid]
        kind, p = e.get("kind"), e.get("params") or {}
        if kind == "constant":
            return constant_output(p)
        if e.get("status") in OFF_STATUSES:
            return {}  # deciders, arithmetic and selectors need power; without it they output nothing
        red, green = self.inputs_of(eid, values)
        if kind == "decider":
            return decider_output(p, red, green, self.order)
        if kind == "arithmetic":
            return arithmetic_output(p, red, green)
        if kind == "selector":
            return selector_output(p, red, green, self.stack_sizes, self.order)
        return {}

    def _combinator_sum(self, values):
        per = {n: [] for n in values}
        unsim = []
        for i in self.ids:
            if self.entities[i].get("kind") not in COMBINATORS:
                continue
            try:
                o = self.output_of(i, values)
            except (NotSimulated, ValueError):  # unknown mode/operation: check() reports it
                unsim.append(i)
                continue
            for n in self.out_networks(i):
                per.setdefault(n, []).append(o)
        return {n: add(*sets) for n, sets in per.items()}, unsim

    def _infer_exogenous(self):
        sums, unsim = self._combinator_sum(self.observed)
        self.unsimulated = unsim
        unsim_nets = {n for i in unsim for n in self.out_networks(i)}
        exo = {}
        for n in self.observed:
            if self.sources(n) or n in unsim_nets:
                exo[n] = sub(self.observed[n], sums.get(n, {}))
            else:
                exo[n] = {}
        return exo

    def exogenous(self):
        """{network: signals} that non-combinator entities (and unsimulated combinators) put on
        each network: observed value minus the combinators' steady-state output. Networks with no
        such entity get {} (so a pure combinator circuit caught mid-oscillation is not distorted)."""
        return copy.deepcopy(self._exo)

    def _resolve(self, inputs):
        """{network id: signals} overrides from a {network_or_entity: signals} map."""
        out = {}
        for key, sigs in sorted((inputs or {}).items(), key=lambda kv: str(kv[0])):
            sigs = _clean(_sig(sigs))
            k = key
            if isinstance(k, str) and k.startswith("network:"):
                k = k[len("network:"):]
            elif isinstance(k, str) and k.startswith("entity:"):
                k = int(k[len("entity:"):])
            if not isinstance(k, str) and k in self.entities:
                e = self.entities[k]
                nets = [n for n in (self._con(e, "red"), self._con(e, "green")) if n]
                if e.get("kind") != "other" or not nets:
                    raise ValueError(f"entity {k} is not a wired non-combinator entity")
                for n in nets:
                    others = [i for i in self.sources(n) if i != k]
                    if others:
                        raise ValueError(f"network {n} has other sources {others}; "
                                         f"override network {n} instead")
                    out[n] = sigs
            elif str(k) in self.observed:
                out[str(k)] = sigs
            else:
                raise ValueError(f"unknown network or entity {key!r}")
        return out

    def with_inputs(self, inputs):
        """A copy whose exogenous contributions are replaced: keys are network ids (str), entity ids
        (int unit_number of a non-combinator that is the only source on its networks), or
        "network:<id>" / "entity:<id>". State restarts from the snapshot values."""
        c = Circuit.__new__(Circuit)
        c.__dict__.update(copy.deepcopy({k: v for k, v in self.__dict__.items() if k != "snapshot"}))
        c.snapshot = self.snapshot
        c._exo.update(self._resolve(inputs))
        c.values = copy.deepcopy(self.observed)
        c.tick = 0
        return c

    def reset(self):
        """Back to the snapshot's values at tick 0."""
        self.values = copy.deepcopy(self.observed)
        self.tick = 0
        return self

    def next_values(self, values=None, inputs=None):
        """Network values one tick after `values` (default current), without changing state."""
        values = self.values if values is None else values
        exo = dict(self._exo)
        exo.update(self._resolve(inputs))
        sums, _ = self._combinator_sum(values)
        return {n: add(exo.get(n), sums.get(n)) for n in sorted(self.observed, key=_netkey)}

    def step(self, inputs=None):
        """Advance one tick and return the new values. `inputs` overrides exogenous contributions
        for this tick only (same keys as with_inputs)."""
        self.values = self.next_values(None, inputs)
        self.tick += 1
        return copy.deepcopy(self.values)

    def run(self, ticks, inputs=None, until_stable=True):
        """Step up to `ticks` times from the current values. Returns
        {history: [values at tick 0..n], stable_at: first tick t with values(t+1) == values(t) or
        None, cycle: {start, period} when a state repeats with period > 1 or None, final, ticks}.
        until_stable stops at the first fixed point or cycle. `inputs` holds for the whole run."""
        history = [copy.deepcopy(self.values)]
        seen = {_canon(self.values): 0}
        stable_at, cycle = None, None
        for t in range(1, int(ticks) + 1):
            self.step(inputs)
            history.append(copy.deepcopy(self.values))
            key = _canon(self.values)
            if key in seen and stable_at is None and cycle is None:
                start = seen[key]
                if t - start == 1:
                    stable_at = start
                else:
                    cycle = {"start": start, "period": t - start}
                if until_stable:
                    break
            seen.setdefault(key, t)
        return {"history": history, "stable_at": stable_at, "cycle": cycle,
                "final": copy.deepcopy(self.values), "ticks": len(history) - 1}

    # --- reading
    def controls(self, values=None):
        """For each non-combinator entity with a control block: {id, name, type, enabled (None
        without an enable condition), set: {flag: signal value read, or the flag itself}}."""
        out = []
        for i in self.ids:
            e = self.entities[i]
            if e.get("kind") != "other" or not e.get("control"):
                continue
            red, green = self.inputs_of(i, values)
            inp = add(red, green)
            ctl = e["control"]
            cond = ctl.get("enable_condition")
            sets = {}
            for k, v in sorted((ctl.get("set") or {}).items()):
                if isinstance(v, str) and _is_key(v):
                    sets[k] = inp.get(v, 0)
                elif v:
                    sets[k] = v
            out.append({"id": i, "name": e.get("name"), "type": e.get("type"),
                        "enabled": evaluate_condition(cond, red, green) if cond else None,
                        "set": sets})
        return out

    def trace(self, signal, network, values=None):
        """Who puts `signal` on `network` next tick: combinators (computed from `values`, default
        current) and the exogenous part with the candidate source entities. Sorted, non-zero only."""
        network = str(network)
        out = []
        for i in self.ids:
            e = self.entities[i]
            if e.get("kind") not in COMBINATORS or network not in self.out_networks(i):
                continue
            try:
                v = self.output_of(i, values).get(signal, 0)
            except (NotSimulated, ValueError):
                continue
            if v:
                out.append({"id": i, "name": e.get("name"), "kind": e.get("kind"), "value": v})
        x = self._exo.get(network, {}).get(signal, 0)
        if x:
            out.append({"id": None, "name": None, "kind": "exogenous", "value": x,
                        "sources": self.sources(network) + [i for i in self.unsimulated
                                                            if network in self.out_networks(i)]})
        return out


def load(snapshot, order=None, exogenous=None, stack_sizes=None, strict=True):
    """Validate a snapshot and build a Circuit. strict: raise ValueError listing every non-warning
    problem. order: Factoriopedia signal order for Anything/select ties. exogenous: {net: signals}
    to use instead of inferring. stack_sizes: for selector stack-size mode."""
    probs = [p for p in check(snapshot) if not p.startswith("warning:")]
    if strict and probs:
        raise ValueError("bad circuit snapshot: " + "; ".join(probs))
    return Circuit(snapshot, order=order, exogenous=exogenous, stack_sizes=stack_sizes)


# ----------------------------------------------------------------------------------------- explain

def _nsel(sel):
    s = _nets(sel)
    if s["red"] and s["green"]:
        return ""
    if s["red"]:
        return "[red]"
    if s["green"]:
        return "[green]"
    return "[no wire]"


def _short(k):
    return {EVERYTHING: "everything", ANYTHING: "anything", EACH: "each"}.get(k, k)


def cond_text(cond):
    """A condition list as text, OR groups joined by ' or ', with networks when not both."""
    conds = _conds(cond)
    if not conds:
        return "never (no condition)"
    parts = []
    for g in _groups(conds):
        terms = []
        for c in g:
            if not c.get("first"):
                terms.append("(empty: false)")
                continue
            right = (_short(c["second"]) + _nsel(c.get("second_networks"))
                     if c.get("second") else str(int(c.get("constant") or 0)))
            terms.append(f"{_short(c['first'])}{_nsel(c.get('first_networks'))} "
                         f"{_CMP_ALIASES.get(c.get('comparator'), c.get('comparator'))} {right}")
        parts.append(" and ".join(terms))
    return " or ".join(f"({p})" if len(parts) > 1 and " and " in p else p for p in parts)


def _outs_text(outs):
    t = []
    for o in outs or []:
        if not o.get("signal"):
            continue
        v = ("input count" + _nsel(o.get("networks")) if o.get("copy_count_from_input")
             else str(1 if o.get("constant") is None else o["constant"]))
        t.append(f"{_short(o['signal'])} = {v}")
    return ", ".join(t) or "nothing"


def _operand(p, slot):
    s = p.get(slot)
    if s:
        return _short(s) + _nsel(p.get(slot + "_networks"))
    return str(int(p.get(slot + "_constant") or 0))


def entity_text(e):
    """One line: what a combinator computes, or what controls/is read from another entity."""
    kind, p = e.get("kind"), e.get("params") or {}
    head = f"{e.get('name')} #{e.get('id')}"
    if kind == "decider":
        s = f"if {cond_text(p.get('conditions'))} then {_outs_text(p.get('outputs'))}"
        if p.get("else_outputs"):
            s += f" else {_outs_text(p.get('else_outputs'))}"
        return f"{head}: {s}"
    if kind == "arithmetic":
        return (f"{head}: {_short(p.get('output')) if p.get('output') else '(no output)'} = "
                f"{_operand(p, 'first')} {p.get('operation')} {_operand(p, 'second')}")
    if kind == "constant":
        o = constant_output(p)
        if p.get("enabled", True) is False:
            return f"{head}: constant, switched off"
        return f"{head}: constant " + (", ".join(f"{k}={v}" for k, v in o.items()) or "(empty)")
    if kind == "selector":
        mode = p.get("mode") or p.get("operation")
        if mode == "select":
            idx = _short(p["index_signal"]) if p.get("index_signal") else int(p.get("index_constant") or 0)
            return f"{head}: select {'highest' if p.get('select_max', True) else 'lowest'} #{idx}"
        if mode == "count":
            return f"{head}: count of input signals -> {p.get('count_signal')}"
        return f"{head}: selector {mode}" + ("" if mode == "stack-size" else " (not simulated)")
    ctl = e.get("control") or {}
    bits = []
    if ctl.get("enable_condition"):
        bits.append("enabled when " + cond_text(ctl["enable_condition"]))
    sets = ctl.get("set") or {}
    for k, v in sorted(sets.items()):
        if isinstance(v, str):
            bits.append(f"{k} from {v}")
        elif v:
            bits.append(f"sets {k}")
    reads = [k for k, v in sorted((ctl.get("read") or {}).items()) if v]
    if reads:
        bits.append("reads " + ", ".join(
            k if (ctl["read"][k] is True) else f"{k} ({ctl['read'][k]})" for k in reads))
    return f"{head}: " + ("; ".join(bits) if bits else "no circuit control")


def explain(x):
    """Short text: one line per combinator (what it computes, its in/out networks) and per
    controlled entity (what enables it, what it sets and reads, with current values when a
    Circuit is given). Accepts a Circuit, a snapshot dict or a single entity dict."""
    if isinstance(x, dict) and "entities" not in x:
        return entity_text(x)
    c = x if isinstance(x, Circuit) else Circuit(x)
    lines = []
    ctl = {r["id"]: r for r in c.controls()}
    for i in c.ids:
        e = c.entities[i]
        cons = {k: v for k, v in sorted((e.get("connectors") or {}).items()) if v is not None}
        wiring = " ".join(f"{k}={v}" for k, v in cons.items())
        line = entity_text(e)
        if e.get("kind") == "other":
            if i not in ctl and not cons:
                continue
            r = ctl.get(i)
            if r:
                now = []
                if r["enabled"] is not None:
                    now.append("enabled" if r["enabled"] else "disabled")
                now += [f"{k}={v}" for k, v in r["set"].items() if not isinstance(v, bool)]
                if now:
                    line += " -> now " + ", ".join(now)
        if i in c.unsimulated:
            line += " (not simulated)"
        lines.append(f"{line}  [{wiring}]" if wiring else line)
    for n in sorted(c.observed, key=_netkey):
        if c._exo.get(n):
            lines.append(f"network {n} ({c.colors.get(n)}): from entities "
                         + ", ".join(f"{k}={v}" for k, v in c._exo[n].items()))
    return "\n".join(lines)
