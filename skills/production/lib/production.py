"""Production planner for factorio_compute (`import production`).

Given recipes, machines and target rates, works out how many machines each recipe needs, the
item flows between them, the raw inputs, and the byproducts left over. Everything is exact
(`fractions.Fraction`), so 2/3 of an assembler stays 2/3 and oil ratios come out clean.

Algorithm, ported from Kirk McDonald's Factorio calculator (Apache-2.0, see SOURCE.md):
1. Recipe graph: from the targets, every recipe that can make a needed item, then the recipes
   for its ingredients, and so on (`solve.js` getRecipeGraph).
2. Cycles: Kosaraju's strongly connected components over "recipe -> recipes making its
   ingredients" (`cycle.js` getCycleRecipes); a recipe in a component of two or more, or one
   that consumes its own product, is cyclic.
3. Ratio walk (`solve.js` traverse): an item with exactly one producing recipe, which has a
   single product and is not cyclic, is solved by plain division and the walk recurses into
   its ingredients. Anything else (several recipes, byproducts, loops) is left as a remainder.
4. Linear program for the remainder (`solve.js` solve + `simplex.js`): minimise cost over
   recipe rates x >= 0 subject to net production >= remainder, solved as its dual with an exact
   rational dense-tableau simplex. Rows are recipes, columns are items; at the optimum the
   objective row holds the recipe rates (under the slack columns) and the surplus of each item
   (under the item columns). Costs follow Kirk's lexicographic priorities: every recipe pays a
   "tax" of 1 per craft/s, raw resources are priced by priority level (default: water first,
   i.e. cheapest, then everything else), each level costing cost_ratio * N times the previous
   one. Unlike Kirk, every item also gets an import row at the highest cost ("producer of last
   resort", Kirk's disableRecipe / FactorioLab's unproduceable penalty), so the LP is always
   feasible and a broken loop shows up as an import instead of an error. Dantzig pivoting with a
   switch to Bland's rule after a run of degenerate pivots (anti-cycling, not in Kirk's code).

Module and beacon effects are applied to each recipe before solving, following FactorioLab's
`src/state/adjustment.ts` (MIT): effects add up from research productivity, the machine's base
effect, modules (effect x count) and beacons (module effect x count x distribution
effectivity x beacon count x profile[total beacons - 1]); speed and consumption are floored at
20%; productivity is clamped to [0, recipe.maximum_productivity] (Factorio 2.0, default +300%)
and multiplies only the part of each product above `ignored_by_productivity`.

Inputs are plain dicts, either the short shape documented on `plan` or the shape a data_lua
dump of `prototypes.recipe` / `prototypes.entity` gives (ingredients/products as lists of
{type, name, amount, ...}); both are accepted everywhere.
"""
from __future__ import annotations

import math
from fractions import Fraction

EFFECTS = ("speed", "productivity", "consumption", "pollution", "quality")
MIN_FACTOR = Fraction(1, 5)
DEFAULT_MAX_PRODUCTIVITY = Fraction(3)
DEFAULT_DISTRIBUTION_EFFECTIVITY = Fraction(3, 2)  # vanilla 2.0 beacon

# Vanilla 2.0 module effects, used only when `module_defs` does not name the module.
VANILLA_MODULES = {
    "speed-module": {"speed": "0.2", "consumption": "0.5", "quality": "-0.01"},
    "speed-module-2": {"speed": "0.3", "consumption": "0.6", "quality": "-0.015"},
    "speed-module-3": {"speed": "0.5", "consumption": "0.7", "quality": "-0.025"},
    "productivity-module": {"productivity": "0.04", "consumption": "0.4", "pollution": "0.05", "speed": "-0.05"},
    "productivity-module-2": {"productivity": "0.06", "consumption": "0.6", "pollution": "0.07", "speed": "-0.1"},
    "productivity-module-3": {"productivity": "0.1", "consumption": "0.8", "pollution": "0.1", "speed": "-0.15"},
    "efficiency-module": {"consumption": "-0.3"},
    "efficiency-module-2": {"consumption": "-0.4"},
    "efficiency-module-3": {"consumption": "-0.5"},
}

ZERO = Fraction(0)
ONE = Fraction(1)


# ---------------------------------------------------------------------------- numbers / json

def F(x):
    """Exact Fraction from an int, float (by its decimal repr: 0.75 -> 3/4), "3/4" or Fraction."""
    if isinstance(x, Fraction):
        return x
    if isinstance(x, bool):
        return Fraction(int(x))
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"not a finite number: {x}")
        return Fraction(repr(x))
    if isinstance(x, str):
        return Fraction(x.strip())
    raise TypeError(f"not a number: {x!r}")


def _s(x):
    return str(x)


def _f(x):
    return float(x)


# ---------------------------------------------------------------------------- normalisation

def _names(v):
    """A set from a list of names or a {name: true} dict (Lua sets dump as dicts)."""
    if v is None:
        return None
    if isinstance(v, dict):
        return {k for k, on in v.items() if on}
    if isinstance(v, str):
        return {v}
    return set(v)


def _ingredients(spec):
    out = {}
    if not spec:
        return out
    if isinstance(spec, dict):
        pairs = spec.items()
    else:
        pairs = [(e["name"], e) for e in spec]
    for name, v in pairs:
        a = F(v["amount"] if isinstance(v, dict) else v)
        out[name] = out.get(name, ZERO) + a
    return out


def _product(v):
    """(expected amount per craft, expected part ignored by productivity)."""
    if not isinstance(v, dict):
        return F(v), ZERO
    p = F(v.get("probability", 1) if v.get("probability") is not None else 1)
    if v.get("amount") is not None:
        base = F(v["amount"])
    else:
        base = (F(v.get("amount_min", 0)) + F(v.get("amount_max", 0))) / 2
    base += F(v.get("extra_count_fraction", 0) or 0)
    ign = min(F(v.get("ignored_by_productivity", 0) or 0), base)
    return p * base, p * ign


def _products(spec):
    out = {}
    if not spec:
        return out
    if isinstance(spec, dict):
        pairs = spec.items()
    else:
        pairs = [(e["name"], e) for e in spec]
    for name, v in pairs:
        e, i = _product(v)
        pe, pi = out.get(name, (ZERO, ZERO))
        out[name] = (pe + e, pi + i)
    return out


def _recipe(name, r):
    time = r.get("time", r.get("energy", r.get("energy_required", 0.5)))
    allowed = r.get("allowed_effects")
    if allowed is None and r.get("allow_productivity") is False:
        allowed = [e for e in EFFECTS if e != "productivity"]
    return {
        "_n": True,
        "name": r.get("name", name),
        "time": F(time),
        "category": r.get("category") or "crafting",
        "ingredients": _ingredients(r.get("ingredients")),
        "products": _products(r.get("products", r.get("results"))),
        "allowed": _names(allowed),
        "max_prod": F(r["maximum_productivity"]) if r.get("maximum_productivity") is not None
        else DEFAULT_MAX_PRODUCTIVITY,
        "prod_bonus": F(r.get("productivity_bonus", 0) or 0),
    }


def _effect_dict(d):
    return {k: F(v) for k, v in (d or {}).items() if k in EFFECTS and v is not None}


def _machine(name, m):
    proto = "crafting_speed" in m or "crafting_categories" in m
    speed = m.get("speed", m.get("crafting_speed", 1))
    base = m.get("base_effect")
    if base is None and isinstance(m.get("effect_receiver"), dict):
        base = m["effect_receiver"].get("base_effect")
    power = m.get("energy_usage")
    if power is not None:
        power = F(power) * (60 if proto else 1)  # prototypes give J/tick
    return {
        "_n": True,
        "name": m.get("name", name),
        "speed": F(speed),
        "categories": _names(m.get("categories", m.get("crafting_categories"))) or set(),
        "slots": int(m.get("module_slots", m.get("module_inventory_size", 0)) or 0),
        "allowed": _names(m.get("allowed_effects")),
        "base": _effect_dict(base),
        "power": power,
    }


def recipes_from_prototypes(data):
    """Plain recipe dicts from a data_lua dump of prototypes.recipe (dict by name or a list)."""
    items = data.items() if isinstance(data, dict) else [(r["name"], r) for r in data]
    out = {}
    for name, r in items:
        n = _recipe(name, r)
        out[n["name"]] = {
            "time": _s(n["time"]), "category": n["category"],
            "ingredients": {k: _s(v) for k, v in n["ingredients"].items()},
            "products": {k: {"amount": _s(e), "ignored_by_productivity": _s(i)}
                         for k, (e, i) in n["products"].items()},
            "maximum_productivity": _s(n["max_prod"]),
        }
        if n["allowed"] is not None:
            out[n["name"]]["allowed_effects"] = sorted(n["allowed"])
    return out


def machines_from_prototypes(data):
    """Plain machine dicts from a data_lua dump of crafting-machine prototypes."""
    items = data.items() if isinstance(data, dict) else [(m["name"], m) for m in data]
    out = {}
    for name, m in items:
        n = _machine(name, m)
        out[n["name"]] = {"speed": _s(n["speed"]), "categories": sorted(n["categories"]),
                          "module_slots": n["slots"], "base_effect": {k: _s(v) for k, v in n["base"].items()}}
        if n["allowed"] is not None:
            out[n["name"]]["allowed_effects"] = sorted(n["allowed"])
        if n["power"] is not None:
            out[n["name"]]["energy_usage"] = _s(n["power"])
    return out


# ---------------------------------------------------------------------------- effects

def _beneficial(effect, value):
    if effect in ("consumption", "pollution"):
        return value < 0
    return value > 0


def _module_effects(name, module_defs):
    d = (module_defs or {}).get(name)
    if d is None:
        d = VANILLA_MODULES.get(name)
    if d is None:
        raise ValueError(f"unknown module {name!r}: pass its effects in module_defs")
    if "module_effects" in d:
        d = d["module_effects"]
    return _effect_dict(d)


def _counts(mods):
    if not mods:
        return []
    if isinstance(mods, dict):
        return sorted((k, F(v)) for k, v in mods.items())
    return [(m, ONE) for m in mods]  # a list of names, one per slot


def adjust(recipe, machine, config=None, module_defs=None, beacon_defs=None, warnings=None):
    """Effective recipe in `machine` with `config` = {"modules": {...}, "beacons": [...]}.

    Returns {"speed", "productivity", "consumption", "pollution", "quality"} (speed, consumption
    and pollution as multipliers, productivity/quality as bonuses), "craft_time" (seconds per
    craft per machine) and "products" ({item: amount per craft after productivity})."""
    r = recipe if recipe.get("_n") else _recipe(recipe.get("name", "recipe"), recipe)
    m = machine if machine is None or machine.get("_n") else _machine(machine.get("name", "machine"), machine)
    warnings = warnings if warnings is not None else []
    config = config or {}
    allowed = r["allowed"]
    if m is not None and m["allowed"] is not None:
        allowed = m["allowed"] if allowed is None else allowed & m["allowed"]

    def ok(e, v):
        return allowed is None or e in allowed or not _beneficial(e, v)

    eff = {e: ZERO for e in EFFECTS}
    eff["productivity"] += r["prod_bonus"]
    if m is not None:
        for e, v in m["base"].items():
            eff[e] += v
    used = ZERO
    for name, n in _counts(config.get("modules")):
        me = _module_effects(name, module_defs)
        bad = sorted(e for e, v in me.items() if not ok(e, v))
        if bad:
            warnings.append(f"{r['name']}: {name} not allowed ({', '.join(bad)}), left out")
            continue
        used += n
        for e, v in me.items():
            eff[e] += v * n
    if m is not None and used > m["slots"]:
        warnings.append(f"{r['name']}: {used} modules in {m['name']} with {m['slots']} slots")
    groups = config.get("beacons") or []
    total = sum((F(b.get("count", 1)) for b in groups), ZERO)
    for b in groups:
        count = F(b.get("count", 1))
        if count == 0:
            continue
        d = dict((beacon_defs or {}).get(b.get("name"), {}))
        d.update({k: v for k, v in b.items() if k in ("distribution_effectivity", "profile", "module_slots")})
        dist = F(d.get("distribution_effectivity", DEFAULT_DISTRIBUTION_EFFECTIVITY))
        profile = d.get("profile")
        scale = ONE
        if profile:
            i = int(round(float(total))) - 1
            scale = F(profile[min(max(i, 0), len(profile) - 1)])
        slots = d.get("module_slots", d.get("module_inventory_size"))
        held = ZERO
        for name, n in _counts(b.get("modules")):
            me = _module_effects(name, module_defs)
            bad = sorted(e for e, v in me.items() if not ok(e, v))
            if bad:
                warnings.append(f"{r['name']}: beacon {name} not allowed ({', '.join(bad)}), left out")
                continue
            held += n
            for e, v in me.items():
                eff[e] += v * n * dist * count * scale
        if slots is not None and held > int(slots):
            warnings.append(f"{r['name']}: {held} modules in a beacon with {slots} slots")
    for e in EFFECTS:  # base effects of a recipe that does not allow them do not apply either
        if not ok(e, eff[e]):
            eff[e] = ZERO
    speed = max(ONE + eff["speed"], MIN_FACTOR)
    consumption = max(ONE + eff["consumption"], MIN_FACTOR)
    pollution = max(ONE + eff["pollution"], MIN_FACTOR)
    prod = min(max(eff["productivity"], ZERO), r["max_prod"])
    mspeed = m["speed"] if m is not None else ONE
    products = {}
    for item, (e, ign) in r["products"].items():
        products[item] = e + max(e - ign, ZERO) * prod
    return {"speed": speed, "productivity": prod, "consumption": consumption, "pollution": pollution,
            "quality": max(eff["quality"], ZERO), "craft_time": r["time"] / (mspeed * speed),
            "products": products}


# ---------------------------------------------------------------------------- simplex (simplex.js)

def _pivot(A, row, col):
    x = A[row][col]
    A[row] = [v / x for v in A[row]]
    prow = A[row]
    for r in range(len(A)):
        if r == row:
            continue
        ratio = A[r][col]
        if ratio == 0:
            continue
        A[r] = [a - p * ratio for a, p in zip(A[r], prow)]


def simplex(A, basis=None, max_iter=100000):
    """Maximise over a tableau in place (simplex.js `simplex`). The last row is the objective
    row (negated objective coefficients), the last column the right-hand side; every other row
    starts with a basic slack (feasible: RHS >= 0). Returns the number of pivots.

    Dantzig's rule (most negative column, first on ties) as in Kirk's code, plus Bland's rule
    once degenerate pivots repeat, so it cannot cycle."""
    rows, cols = len(A) - 1, len(A[0]) - 1
    if basis is None:
        basis = [None] * rows
    bland, streak, n = False, 0, 0
    while True:
        obj = A[-1]
        col = None
        for c in range(cols):
            if obj[c] < 0 and (col is None or (not bland and obj[c] < obj[col])):
                col = c
                if bland:
                    break
        if col is None:
            return n
        best, best_row = None, None
        for r in range(rows):
            x = A[r][col]
            if x <= 0:
                continue
            ratio = A[r][-1] / x
            if best is None or ratio < best or (
                    bland and ratio == best and (basis[r] if basis[r] is not None else -1)
                    < (basis[best_row] if basis[best_row] is not None else -1)):
                best, best_row = ratio, r
        if best_row is None:
            raise ValueError("failed to pivot: the problem is unbounded")
        _pivot(A, best_row, col)
        basis[best_row] = col
        n += 1
        streak = streak + 1 if best == 0 else 0
        if streak > rows:
            bland = True
        if n > max_iter:
            raise ValueError("simplex did not converge")


# ---------------------------------------------------------------------------- cycles (cycle.js)

def _cycle_recipes(recipes, adj, producers, users):
    """Recipes that sit on a production loop (Kosaraju, as in cycle.js getCycleRecipes)."""
    rset = set(recipes)

    def neighbours(r, invert):
        out = set()
        if invert:
            for item in adj[r]["products"]:
                out.update(u for u in users.get(item, ()) if u in rset)
        else:
            for item in adj[r]["ingredients"]:
                out.update(p for p in producers.get(item, ()) if p in rset)
        return sorted(out)

    def visit(root, seen, invert):
        order, stack = [], [(root, iter(neighbours(root, invert)))]
        seen.add(root)
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop()
                order.append(node)
            elif nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, iter(neighbours(nxt, invert))))
        return order

    seen, L = set(), []
    for r in recipes:
        if r not in seen:
            L.extend(visit(r, seen, False))
    result, seen = set(), set()
    for root in reversed(L):
        if root in seen:
            continue
        comp = visit(root, seen, True)
        if len(comp) > 1 or set(adj[comp[0]]["ingredients"]) & set(adj[comp[0]]["products"]):
            result.update(comp)
    return result


# ---------------------------------------------------------------------------- planner

def _lookup(table, recipe):
    table = table or {}
    for key in (recipe["name"], recipe["category"], "*"):
        if key in table:
            return table[key], key
    return None, None


def _raw_levels(raw_priority):
    if raw_priority is None:
        raw_priority = [{"water": 1}, {"*": 1}]
    levels = []
    for lv in raw_priority:
        if isinstance(lv, dict):
            levels.append({k: F(v) for k, v in lv.items()})
        else:
            levels.append({k: ONE for k in lv})
    if not any("*" in lv for lv in levels):
        levels.append({"*": ONE})
    return levels


def plan(recipes, machines, targets, choices=None, *, module_defs=None, beacon_defs=None,
         raw=(), raw_priority=None, exclude=(), exclude_categories=("recycling",)):
    """Machines, flows, raw inputs and byproducts for `targets`.

    recipes: {name: {time, ingredients: {item: amount}, products: {item: amount | {amount |
        amount_min/amount_max, probability, ignored_by_productivity}}, category,
        allowed_effects?, maximum_productivity?, productivity_bonus?}} or the prototypes.recipe
        dump shape (energy, ingredients/products as lists of {name, amount, ...}).
    machines: {name: {speed, categories, module_slots, allowed_effects?, base_effect?,
        energy_usage? (W)}} or the prototype shape (crafting_speed, crafting_categories,
        module_inventory_size, energy_usage in J/tick, effect_receiver.base_effect).
    targets: {item: rate per second}.
    choices: {"machine": {recipe or category: machine}, "recipe": {item: recipe or [recipes]},
        "modules": {recipe or category or "*": {"modules": {module: count},
        "beacons": [{"name"?, "count", "modules": {module: count},
        "distribution_effectivity"?, "profile"?}]}}}. Without a machine choice the fastest
        machine of the category is used (ties by name).
    module_defs: {module: {speed, productivity, consumption, pollution, quality}} (or
        {"module_effects": {...}}); vanilla 2.0 modules are known by default.
    beacon_defs: {beacon: {distribution_effectivity, profile, module_slots}}.
    raw: items to take as inputs even if a recipe makes them (e.g. plates from a bus). Items no
        recipe makes are raw anyway.
    raw_priority: list of levels, cheapest first, each {item: weight} or [items]; "*" = every
        raw item not named. Default [{"water": 1}, {"*": 1}] (Kirk's default: water before ores
        and crude). The LP minimises use of later levels first.
    exclude / exclude_categories: recipes never used (default: the recycling category).
    """
    choices = choices or {}
    warnings = []
    R = {}
    for name in sorted(recipes):
        r = _recipe(name, recipes[name])
        if r["name"] in set(exclude) or r["category"] in set(exclude_categories or ()):
            continue
        R[r["name"]] = r
    M = {}
    for name in sorted(machines):
        m = _machine(name, machines[name])
        M[m["name"]] = m
    raw_set = set(raw)
    tgt = {}
    for item, rate in sorted((targets or {}).items()):
        rate = F(rate)
        if rate < 0:
            raise ValueError(f"target {item}: negative rate {rate}")
        if rate > 0:
            tgt[item] = tgt.get(item, ZERO) + rate

    # machine + effects per recipe
    adj, machine_of = {}, {}
    for name, r in R.items():
        mname, _ = _lookup(choices.get("machine"), r)
        m = M.get(mname) if mname else None
        if mname and m is None:
            warnings.append(f"{name}: machine {mname!r} is unknown, using the default")
        if m is not None and r["category"] not in m["categories"]:
            warnings.append(f"{name}: {m['name']} cannot craft category {r['category']!r}, using the default")
            m = None
        if m is None:
            cands = sorted((x for x in M.values() if r["category"] in x["categories"]),
                           key=lambda x: (-x["speed"], x["name"]))
            m = cands[0] if cands else None
        cfg, key = _lookup(choices.get("modules"), r)
        w = []
        a = adjust(r, m, cfg, module_defs, beacon_defs, w)
        if key in (r["name"], r["category"]):
            warnings.extend(w)
        a["ingredients"] = dict(r["ingredients"])
        adj[name] = a
        machine_of[name] = m

    producers, users = {}, {}
    for name in sorted(adj):
        for item, amt in adj[name]["products"].items():
            if amt > 0:
                producers.setdefault(item, []).append(name)
        for item in adj[name]["ingredients"]:
            users.setdefault(item, []).append(name)
    pick = {}
    for item, ch in (choices.get("recipe") or {}).items():
        ch = [ch] if isinstance(ch, str) else list(ch)
        for c in ch:
            if c not in adj:
                warnings.append(f"recipe choice {c!r} for {item} is unknown or excluded")
        pick[item] = [c for c in ch if c in adj and adj[c]["products"].get(item, ZERO) > 0]

    def candidates(item):
        if item in raw_set:
            return []
        if item in pick:
            return pick[item]
        return producers.get(item, [])

    def graph(items):
        seen, todo = set(), sorted(items)
        while todo:
            item = todo.pop()
            for r in candidates(item):
                if r not in seen:
                    seen.add(r)
                    todo.extend(sorted(adj[r]["ingredients"]))
        return sorted(seen)

    cyclic = _cycle_recipes(graph(tgt), adj, producers, users)
    rates, remaining, raw_use = {}, {}, {}

    def add(d, k, v):
        d[k] = d.get(k, ZERO) + v

    def traverse(item, rate):  # solve.js traverse
        cands = candidates(item)
        if not cands:
            add(raw_use, item, rate)
            return
        r = cands[0]
        if len(cands) > 1 or len(adj[r]["products"]) > 1 or r in cyclic:
            add(remaining, item, rate)
            return
        crafts = rate / adj[r]["products"][item]
        add(rates, r, crafts)
        for ing, amt in sorted(adj[r]["ingredients"].items()):
            traverse(ing, crafts * amt)

    for item, rate in tgt.items():
        traverse(item, rate)

    imports, lp_info, lp_recipes = {}, None, set()
    if remaining:
        rows = graph(remaining)
        lp_recipes = set(rows)
        items = sorted({i for r in rows for i in adj[r]["products"]}
                       | {i for r in rows for i in adj[r]["ingredients"]} | set(remaining))
        col = {i: k for k, i in enumerate(items)}
        coeff = []
        for r in rows:
            v = [ZERO] * len(items)
            for i, amt in adj[r]["products"].items():
                v[col[i]] += amt
            for i, amt in adj[r]["ingredients"].items():
                v[col[i]] -= amt
            coeff.append(v)
        is_raw = [not candidates(i) for i in items]
        for k, i in enumerate(items):  # one import row per item
            v = [ZERO] * len(items)
            v[k] = ONE
            coeff.append(v)
        nrow = len(coeff)
        # cost_ratio as in solve.js: 2 * max/min over the tableau's nonzero magnitudes
        mags = [abs(x) for v in coeff for x in v if x != 0] + [abs(remaining[i]) for i in remaining] + [ONE]
        ratio = max(max(mags) / min(mags) * 2, Fraction(2))
        cost = [ONE] * nrow  # the "tax": 1 per craft/s
        P = ratio
        levels = _raw_levels(raw_priority)
        named = {i for lv in levels for i in lv if i != "*"}
        for lv in levels:
            present = []
            for k, i in enumerate(items):
                if is_raw[k] and (i in lv or ("*" in lv and i not in named)):
                    present.append((k, lv[i] if i in lv else lv["*"]))
            if not present:
                continue
            minw = min(lv.values())
            N = ZERO
            for k, w in present:
                nw = w / minw
                N += nw
                cost[len(rows) + k] += P * nw
            P = P * ratio * N
        ncol = len(items) + nrow + 1

        def solve(last_resort):
            A = []
            for r in range(nrow):
                c = cost[r] + (last_resort if r >= len(rows) and not is_raw[r - len(rows)] else 0)
                line = coeff[r] + [ZERO] * nrow + [c]
                line[len(items) + r] = ONE
                A.append(line)
            objective = [ZERO] * ncol
            for i, rate in remaining.items():
                objective[col[i]] = -rate
            A.append(objective)
            n = simplex(A, [len(items) + r for r in range(nrow)])
            x = A[-1][len(items):len(items) + nrow]
            used = tuple(x[len(rows) + k] for k in range(len(items)) if not is_raw[k])
            return x, used, n

        # The producer of last resort must cost more than any real production chain. Kirk's
        # level scaling does not guarantee that for deep chains (thousands of ore per item), so
        # while imports are used, make them a million times dearer and solve again.
        last, pivots, tries = P * ratio * 10 ** 6, 0, 0
        x, used, n = solve(last)
        pivots += n
        while any(used) and tries < 6:
            tries += 1
            x2, used2, n = solve(last * 10 ** 6)
            pivots += n
            last *= 10 ** 6
            if used2 == used:
                x = x2
                break
            x, used = x2, used2
        for r, name in enumerate(rows):
            if x[r] > 0:
                add(rates, name, x[r])
        for k, i in enumerate(items):
            v = x[len(rows) + k]
            if v > 0:
                add(raw_use if is_raw[k] else imports, i, v)
        lp_info = {"recipes": len(rows), "items": len(items), "pivots": pivots, "solves": tries + 1,
                   "remainder": {i: _s(v) for i, v in remaining.items()}}
        for i in sorted(imports):
            warnings.append(f"{i}: {_f(imports[i]):.6g}/s could not be produced by the chosen recipes, "
                            "it is listed under imports")

    produced, consumed = {}, {}
    for name, x in rates.items():
        for i, amt in adj[name]["products"].items():
            if amt:
                add(produced, i, x * amt)
        for i, amt in adj[name]["ingredients"].items():
            add(consumed, i, x * amt)
    surplus = {}
    for i in sorted(set(produced) | set(consumed) | set(tgt) | set(raw_use) | set(imports)):
        s = (produced.get(i, ZERO) + raw_use.get(i, ZERO) + imports.get(i, ZERO)
             - consumed.get(i, ZERO) - tgt.get(i, ZERO))
        if s < 0:  # cannot happen with a correct solve; check() reports it too
            warnings.append(f"{i}: short by {_f(-s):.6g}/s")
        elif s > 0:
            surplus[i] = s

    out_recipes, per_machine, power = {}, {}, ZERO
    for name in sorted(rates):
        x, a, m = rates[name], adj[name], machine_of[name]
        e = {"recipe": name, "machine": m["name"] if m else None, "rate": _f(x), "rate_exact": _s(x),
             "solver": "lp" if name in lp_recipes else "ratio",
             "craft_time_exact": _s(a["craft_time"]),
             "effects": {k: _s(a[k]) for k in EFFECTS},
             "ingredients_exact": {i: _s(v) for i, v in sorted(a["ingredients"].items())},
             "products_exact": {i: _s(v) for i, v in sorted(a["products"].items())}}
        if m is None:
            warnings.append(f"{name}: no machine can craft category {R[name]['category']!r}")
            e.update(machines=None, machines_exact=None, machines_ceil=None, power_w=None)
        else:
            n = x * a["craft_time"]
            e.update(machines=_f(n), machines_exact=_s(n), machines_ceil=math.ceil(n))
            per_machine[m["name"]] = per_machine.get(m["name"], 0) + math.ceil(n)
            if m["power"] is not None:
                p = n * m["power"] * a["consumption"]
                power += p
                e["power_w"] = _f(p)
            else:
                e["power_w"] = None
        out_recipes[name] = e

    def fl(d):
        return {k: _f(v) for k, v in sorted(d.items())}

    def ex(d):
        return {k: _s(v) for k, v in sorted(d.items())}

    flows = {}
    for i in sorted(set(produced) | set(consumed)):
        p, c = produced.get(i, ZERO), consumed.get(i, ZERO)
        flows[i] = {"produced": _f(p), "consumed": _f(c), "produced_exact": _s(p), "consumed_exact": _s(c)}
    return {"ok": True, "recipes": out_recipes, "items": flows,
            "targets": fl(tgt), "targets_exact": ex(tgt),
            "raw": fl(raw_use), "raw_exact": ex(raw_use),
            "surplus": fl(surplus), "surplus_exact": ex(surplus),
            "imports": fl(imports), "imports_exact": ex(imports),
            "machines": dict(sorted(per_machine.items())), "power_w": _f(power),
            "lp": lp_info, "warnings": sorted(set(warnings))}


# ---------------------------------------------------------------------------- check

def check(result, recipes=None, targets=None):
    """Problems with a plan as a list of strings ([] = consistent).

    Verifies exactly (from the *_exact strings): every rate and machine count is >= 0, machines
    = rate * craft time and machines_ceil = ceil(machines); the item flows equal the sum of
    rate * amount per craft over the recipes; and flow conservation for every item: produced +
    raw + imports = consumed + target + surplus with raw, imports, surplus >= 0. With `recipes`
    it also checks each used recipe exists with the same ingredients and that productivity did
    not lower or more than quadruple-plus-cap a product; with `targets` that they match."""
    problems = []
    try:
        rec = result["recipes"]
        produced, consumed = {}, {}
        for name, e in sorted(rec.items()):
            x = F(e["rate_exact"])
            if x < 0:
                problems.append(f"{name}: negative rate {x}")
            if abs(_f(x) - e["rate"]) > 1e-9 * max(1.0, abs(_f(x))):
                problems.append(f"{name}: rate {e['rate']} differs from rate_exact {x}")
            if e.get("machines_exact") is not None:
                n = F(e["machines_exact"])
                if n != x * F(e["craft_time_exact"]):
                    problems.append(f"{name}: machines {n} != rate {x} * craft time {e['craft_time_exact']}")
                if e["machines_ceil"] != math.ceil(n):
                    problems.append(f"{name}: machines_ceil {e['machines_ceil']} != ceil({n})")
            for i, a in e["products_exact"].items():
                produced[i] = produced.get(i, ZERO) + x * F(a)
            for i, a in e["ingredients_exact"].items():
                consumed[i] = consumed.get(i, ZERO) + x * F(a)
            if recipes is not None:
                if name not in recipes:
                    problems.append(f"{name}: not in the given recipes")
                    continue
                r = _recipe(name, recipes[name])
                ing = {i: _s(v) for i, v in sorted(r["ingredients"].items())}
                if ing != dict(sorted(e["ingredients_exact"].items())):
                    problems.append(f"{name}: ingredients {e['ingredients_exact']} != recipe {ing}")
                for i, (base, _) in r["products"].items():
                    got = F(e["products_exact"].get(i, "0"))
                    if got < base or got > base * (1 + r["max_prod"]):
                        problems.append(f"{name}: {i} per craft {got} outside [{base}, {base * (1 + r['max_prod'])}]")
        flows = result["items"]
        for i in sorted(set(produced) | set(consumed) | set(flows)):
            f = flows.get(i, {"produced_exact": "0", "consumed_exact": "0"})
            p, c = produced.get(i, ZERO), consumed.get(i, ZERO)
            if F(f["produced_exact"]) != p or F(f["consumed_exact"]) != c:
                problems.append(f"{i}: flow {f['produced_exact']}/{f['consumed_exact']} != recipes {p}/{c}")
        tg = {k: F(v) for k, v in result["targets_exact"].items()}
        if targets is not None:
            want = {k: F(v) for k, v in targets.items() if F(v) > 0}
            if want != tg:
                problems.append(f"targets {result['targets_exact']} != given {targets}")
        rw = {k: F(v) for k, v in result["raw_exact"].items()}
        im = {k: F(v) for k, v in result["imports_exact"].items()}
        su = {k: F(v) for k, v in result["surplus_exact"].items()}
        for name, d in (("raw", rw), ("imports", im), ("surplus", su)):
            for k, v in d.items():
                if v < 0:
                    problems.append(f"{name} {k}: negative {v}")
        for i in sorted(set(produced) | set(consumed) | set(tg) | set(rw) | set(im) | set(su)):
            bal = (produced.get(i, ZERO) + rw.get(i, ZERO) + im.get(i, ZERO)
                   - consumed.get(i, ZERO) - tg.get(i, ZERO) - su.get(i, ZERO))
            if bal != 0:
                problems.append(f"{i}: not conserved, off by {bal}/s "
                                "(produced + raw + imports - consumed - target - surplus)")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        problems.append(f"malformed plan: {type(exc).__name__}: {exc}")
    return problems
