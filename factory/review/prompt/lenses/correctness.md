CORRECTNESS. Logic errors, edge cases, empty or null handling, off-by-one, async misuse
(blocking calls in async code, un-awaited work), race conditions on shared state, error paths
that crash or silently return wrong results, guards or validation removed with no
replacement, interface or response-shape changes that break existing callers. Check the
invariants under "Must stay true" in the touched areas' docs. NOT yours: style, naming,
typing, lint, missing tests.
