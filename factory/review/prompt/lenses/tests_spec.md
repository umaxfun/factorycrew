TESTS & SPEC CONSISTENCY. Description-to-diff fidelity in BOTH directions: every claim in the
intent has backing code (no phantom claims), and every non-trivial hunk is covered by the
intent (no undocumented behaviour change). Docs, area docs, README, API specs and config
examples change in the same merge request as the behaviour they describe; a stale
load-bearing doc is a candidate (advisory). Tests: new or changed core logic or entry points
with no test exercising them; tests that assert the mock instead of the behaviour;
tautological or always-passing tests; nondeterminism (real time, sleeps, random seeds, order
dependence, shared state); brittle UI selectors. Use "How to check a change" in the touched
areas' docs for what the repository expects to be tested. Do NOT ask for coverage numbers. A
candidate needs a named untested path whose breakage would ship unnoticed.
