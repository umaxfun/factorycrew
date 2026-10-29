"""Fluid networks (skills/pipes/lib/pipes.py): pure Python, no game."""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pipes" / "lib"))
sys.path.insert(0, str(ROOT / "mcp"))
import kc_compute as kc  # noqa: E402
import pipes  # noqa: E402

N, E, S, W = 0, 4, 8, 12


def wall(x, ys):
    return {(x + 0.5, y + 0.5) for y in ys}


def tiles(r):
    return {tuple(t) for t in r["tiles"]}


def connected(ts):
    ts = set(ts)
    return not kc.unreached(ts, next(iter(sorted(ts))), sorted(ts))


def field(seed, n, size):
    rnd = random.Random(seed)
    wells = []
    while len(wells) < n:
        x, y = rnd.randrange(size) + 0.5, rnd.randrange(size) + 0.5
        if all(max(abs(x - a), abs(y - b)) >= 4 for a, b in wells):
            wells.append((x, y))
    return wells


# ── join ──

def test_join_two_points_is_a_shortest_path():
    r = pipes.join([(0.5, 0.5), (6.5, 3.5)])
    assert r["ok"] and r["pipes"] == 10 and connected(tiles(r)), r
    assert {(0.5, 0.5), (6.5, 3.5)} <= tiles(r)


def test_join_goes_around_a_wall():
    blocked = wall(3, range(-2, 3))
    r = pipes.join([(0.5, 0.5), (6.5, 0.5)], blocked)
    assert r["ok"] and connected(tiles(r)) and not tiles(r) & blocked, r
    assert r["pipes"] == 7 + 2 * 3, r["pipes"]  # up 3 rows, over, down again


def test_join_shares_a_trunk_between_three_terminals():
    ts = [(0.5, 0.5), (10.5, 0.5), (5.5, 8.5)]
    r = pipes.join(ts)
    assert r["ok"] and connected(tiles(r)), r
    assert r["pipes"] == 11 + 8, r["pipes"]  # a T: the trunk plus one branch, the Steiner optimum


def test_join_picks_the_option_that_meets_the_others():
    far, near = (0.5, -9.5), (0.5, 4.5)
    r = pipes.join([{"options": [far, near]}, (0.5, 8.5)])
    assert r["ok"] and r["choice"] == [1, 0], r
    assert far not in tiles(r) and r["pipes"] == 5


def test_join_shared_starting_tile_and_single_terminal():
    r = pipes.join([{"options": [(0.5, 0.5), (3.5, 3.5)]}, {"options": [(9.5, 9.5), (3.5, 3.5)]}])
    assert r["ok"] and r["pipes"] == 1 and r["choice"] == [1, 1], r
    one = pipes.join([{"options": [(2.5, 2.5), (5.5, 5.5)]}])
    assert one["ok"] and one["tiles"] == [[2.5, 2.5]] and one["choice"] == [0]


def test_join_reports_unreachable_terminals():
    box = {(x + 0.5, y + 0.5) for x in range(8, 13) for y in range(-2, 3)} - {(10.5, 0.5)}
    r = pipes.join([(0.5, 0.5), (4.5, 0.5), (10.5, 0.5)], box)
    assert not r["ok"] and r["unreachable"] == [2], r
    r = pipes.join([(0.5, 0.5), (1.5, 0.5)], {(1.5, 0.5)})
    assert not r["ok"] and r["unreachable"] == [1], r


def test_join_is_deterministic():
    wells = field(3, 12, 30)
    terms = [{"options": [(x + dx, y + dy) for dx, dy in pipes.PUMPJACK_OUTPUTS.values()]} for x, y in wells]
    occ = set().union(*(pipes.footprint(x, y) for x, y in wells))
    a, b = pipes.join(terms, occ), pipes.join([dict(t) for t in terms], set(sorted(occ, reverse=True)))
    assert a == b and a["ok"]


# ── compress ──

def by_tile(c):
    return {(e["x"], e["y"]): e for e in c["entities"]}


def test_compress_buries_a_straight_run_between_ports():
    run = [(x + 0.5, 0.5) for x in range(0, 9)]
    c = pipes.compress(run, ports=[(0.5, 0.5, W), (8.5, 0.5, E)])
    assert c["pipes"] == 0 and c["pairs"] == 1, c
    t = by_tile(c)
    assert t[(0.5, 0.5)]["direction"] == W and t[(8.5, 0.5)]["direction"] == E
    assert all("type" not in e for e in c["entities"])  # 2.0 pipe-to-ground: direction only


def test_compress_chains_pairs_past_the_reach_and_keeps_side_branches():
    run = [(x + 0.5, 0.5) for x in range(-1, 30)]  # 31 tiles, plain pipe at each end
    branch = [(15.5, y + 0.5) for y in range(1, 6)]
    c = pipes.compress(run + branch, pipe_to_ground_reach=10, ports=[(15.5, 5.5, S)])
    t = by_tile(c)
    assert t[(15.5, 0.5)]["name"] == "pipe", "the junction stays a pipe"
    assert t[(-0.5, 0.5)]["name"] == "pipe" and t[(29.5, 0.5)]["name"] == "pipe", "dead ends are not buried"
    assert t[(15.5, 1.5)]["name"] == "pipe-to-ground" and t[(15.5, 1.5)]["direction"] == N
    assert t[(15.5, 5.5)]["direction"] == S
    ents = c["entities"]
    assert pipes.check(ents, pipe_to_ground_reach=10) == [], pipes.check(ents)
    for i, e in enumerate(ents):  # every pair spans at most the reach
        if e["name"] == "pipe-to-ground" and e["direction"] == W:
            out = min((f for f in ents if f["name"] == "pipe-to-ground" and f["direction"] == E and f["y"] == e["y"]
                       and f["x"] > e["x"]), key=lambda f: f["x"])
            assert out["x"] - e["x"] <= 10


def test_compress_respects_keep_short_runs_and_no_reach():
    run = [(0.5, y + 0.5) for y in range(0, 8)]
    ports = [(0.5, 0.5, N), (0.5, 7.5, S)]
    kept = by_tile(pipes.compress(run, ports=ports, keep=[(0.5, 7.5)]))
    assert kept[(0.5, 7.5)]["name"] == "pipe" and kept[(0.5, 6.5)]["direction"] == S, kept
    assert pipes.compress(run[:2], ports=[(0.5, 0.5, N), (0.5, 1.5, S)])["pairs"] == 0
    assert pipes.compress(run, ports=ports, pipe_to_ground_reach=0)["pipes"] == 8


def test_compress_keeps_a_one_tile_side_branch_connected():
    run = [(x + 0.5, 0.5) for x in range(0, 10)]
    ports = [(0.5, 0.5, W), (9.5, 0.5, E), (5.5, 1.5, S)]
    c = pipes.compress(run + [(5.5, 1.5)], ports=ports)
    t = by_tile(c)
    assert t[(5.5, 0.5)]["name"] == "pipe" and t[(5.5, 1.5)]["name"] == "pipe", c
    assert c["pairs"] == 2 and pipes.check(c["entities"]) == []


def test_compress_does_not_bury_a_port_facing_across_the_run():
    run = [(x + 0.5, 0.5) for x in range(0, 9)]
    ports = [(0.5, 0.5, W), (8.5, 0.5, E), (4.5, 0.5, N)]  # a machine above the middle tile
    c = pipes.compress(run, ports=ports)
    assert by_tile(c)[(4.5, 0.5)]["name"] == "pipe", c


# ── oil_field ──

def test_small_field_is_valid_and_points_at_the_sink():
    wells = [(0.5, 0.5), (6.5, 0.5), (0.5, 7.5), (7.5, 8.5)]
    r = pipes.oil_field(wells, toward=(-50.5, 4.5))
    assert r["ok"] and r["problems"] == [], r
    assert r["pumpjacks"] == 4 and r["sink"] == [-1.5, 4.5], r["sink"]  # left edge of the field
    jacks = [e for e in r["entities"] if e["name"] == "pumpjack"]
    assert sorted((j["x"], j["y"]) for j in jacks) == sorted(wells)
    assert all(j["direction"] in (N, E, S, W) for j in jacks)
    assert pipes.check(r["entities"], sink=r["sink"]) == []


def test_field_reads_offsets_size_and_names_from_arguments():
    # a 5x5 modded pumpjack whose output is in the middle of a side
    offs = {N: (0, -3), E: (3, 0), S: (0, 3), W: (-3, 0)}
    wells = [(0.5, 0.5), (8.5, 0.5), (16.5, 2.5)]
    r = pipes.oil_field(wells, pumpjack_size=5, output_offsets=offs, sink=(8.5, 10.5), pumpjack="nullius-pump",
                        pipe="nullius-pipe", pipe_to_ground="nullius-ptg", pipe_to_ground_reach=6)
    assert r["ok"] and r["problems"] == [], r
    assert {e["name"] for e in r["entities"]} <= {"nullius-pump", "nullius-pipe", "nullius-ptg"}


def test_field_avoids_foreign_fluid_and_blocked():
    wells = [(0.5, 0.5), (6.5, 0.5)]
    foreign = [(3.5, y + 0.5) for y in range(-6, 7)]  # someone's water pipe between the pumpjacks
    r = pipes.oil_field(wells, blocked=foreign, foreign_pipes=foreign, toward=(3.5, -40), margin=10)
    assert r["ok"] and r["problems"] == [], r
    near = {(x + dx, y + 0.0) for x, y in foreign for dx in (-1, 1)}
    assert not any((e["x"], e["y"]) in near for e in r["entities"] if e["name"] != "pumpjack"), r
    assert not pipes.oil_field([(0.5, 0.5)], blocked=[(1.5, 1.5)])["ok"]
    assert "overlap" in pipes.oil_field([(0.5, 0.5), (2.5, 0.5)])["reason"]


def test_field_beats_the_kc_connect_baseline_and_is_deterministic():
    tot_base = tot = 0
    for seed, n, size in [(1, 8, 24), (2, 12, 30), (4, 20, 40)]:
        wells = field(seed, n, size)
        r = pipes.oil_field(wells, toward=(-100, size / 2))
        assert r["ok"] and r["problems"] == [], (seed, r["problems"])
        assert r == pipes.oil_field(wells, toward=(-100, size / 2))
        occ = set().union(*(pipes.footprint(x, y) for x, y in wells))
        base = min(len(kc.connect([(x + dx, y + dy) for x, y in wells], r["sink"], occ, margin=4)["tiles"])
                   for dx, dy in pipes.PUMPJACK_OUTPUTS.values())
        tot_base, tot = tot_base + base, tot + r["pipe_tiles"]
        assert r["pipes"] + r["pipe_to_ground"] < r["pipe_tiles"]  # burying saved entities
    assert tot <= 0.9 * tot_base, (tot, tot_base)


# ── check ──

def test_check_catches_broken_layouts():
    r = pipes.oil_field([(0.5, 0.5), (8.5, 0.5)], toward=(4.5, 30))
    ents = r["entities"]
    assert r["ok"] and pipes.check(ents, sink=r["sink"]) == []
    jack = next(e for e in ents if e["name"] == "pumpjack")
    o = pipes.PUMPJACK_OUTPUTS[jack["direction"]]
    out = (jack["x"] + o[0], jack["y"] + o[1])
    no_out = [e for e in ents if (e["x"], e["y"]) != out]
    assert any("output tile" in p for p in pipes.check(no_out, sink=r["sink"]))
    turned = [dict(e, direction=(e["direction"] + 4) % 16) if e is jack else e for e in ents]
    assert any("pumpjack" in p for p in pipes.check(turned, sink=r["sink"]))
    stray = ents + [{"name": "pipe", "x": 40.5, "y": 40.5}]
    assert any("not connected" in p for p in pipes.check(stray))
    assert any("already used" in p for p in pipes.check(ents + [dict(ents[-1])]))
    assert any("blocked" in p for p in pipes.check(ents, [out]))
    pipe = next(e for e in ents if e["name"] == "pipe" and (e["x"], e["y"]) != tuple(r["sink"]))
    foreign = [(pipe["x"] + 1, pipe["y"]), (pipe["x"] - 1, pipe["y"]), (pipe["x"], pipe["y"] + 1), (pipe["x"], pipe["y"] - 1)]
    foreign = [f for f in foreign if not any((e["x"], e["y"]) == f for e in ents)][:1]
    assert foreign and any("mix" in p for p in pipes.check(ents, foreign_pipes=foreign))
    assert any("mix" in p for p in pipes.check(ents, foreign_ports=[(pipe["x"], pipe["y"])]))
    assert any("holds no pipe" in p for p in pipes.check(ents, sink=(99.5, 99.5)))


def test_check_pipe_to_ground_rules():
    pair = [{"name": "pipe-to-ground", "x": 0.5, "y": 0.5, "direction": W},
            {"name": "pipe-to-ground", "x": 10.5, "y": 0.5, "direction": E}]
    assert pipes.check(pair, pipe_to_ground_reach=10) == []
    assert any("not connected" in p for p in pipes.check(pair, pipe_to_ground_reach=9))
    same = [pair[0], dict(pair[1], direction=W)]
    assert any("not connected" in p for p in pipes.check(same))
    # a pipe beside a pipe-to-ground does not connect to it
    side = pair + [{"name": "pipe", "x": 0.5, "y": 1.5}]
    assert any("not connected" in p for p in pipes.check(side))
    # a pipe it faces does
    front = pair + [{"name": "pipe", "x": -0.5, "y": 0.5}]
    assert pipes.check(front) == []
