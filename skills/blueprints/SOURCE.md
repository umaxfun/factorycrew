# Sources

## Ported code: pierre-dejoue/factorio-blueprints

- URL: https://github.com/pierre-dejoue/factorio-blueprints
- Commit read: `045b59e218c819e82d42a781ac627247a8328ed9` (main, 2026-10-29)
- License: MIT (`gh api repos/pierre-dejoue/factorio-blueprints --jq .license.spdx_id` = MIT; LICENSE file read, copied verbatim into `LICENSE-upstream`), Copyright (c) 2019 Pierre DEJOUE.
- Ported from `factorio_game/exchange_string/blueprints.py`:
  - `parse_exchange_string*` became `decode`
  - `generate_exchange_string*` became `encode`
  - `decode_game_version` became `version_string`
  - the type and label readers became `kind` and `summary`
- Ported from `blueprints.py`: the book listing (`print_blueprint_book_contents`) became `book_entries`.
- Changes:
  - `decode` also takes raw JSON and ignores whitespace in pasted strings.
  - `encode` uses zlib level 9 (as the game does) instead of the default level, and keeps key order instead of `sort_keys`. It also wraps a bare body.
  - Added `version_number` (the inverse of `version_string`).
  - Asserts were replaced by `ValueError`.
  - The `Type` enum was replaced by plain strings, so the code imports on Python 3.9.
  - `book_entries` returns a list instead of printing, and gives each entry's path through nested books.
- Example strings vendored from the same commit (`tests/examples/blueprints/`, MIT): `examples/red-circuits-block.txt` (2.0.11.3), `blueprint-book.txt`, `decon-planner.txt`, `upgrade-planner.txt`.

## Field reference and example strings: redruin1/factorio-blueprint-schemas

- URL: https://github.com/redruin1/factorio-blueprint-schemas
- Commit read: `682630ffe26aa0a248d44643a72ffa2625de6187` (main, 2026-10-29)
- License: MIT (`gh api ... --jq .license.spdx_id` = MIT; LICENSE file read, copied verbatim into `LICENSE-upstream`), Copyright (c) 2025 redruin1.
- Used from `schemas/2.0.0`:
  - `ENTITY_KEYS` is the union of `properties` across `entity.json` and the 99 `entity/*.json` files, extracted mechanically. Two hyphenated keys seen in their own 2.0.47 example (`result-inventory`, `priority-list`) were added.
  - The blueprint, book and planner key lists, the wires format `[entity1, connector1, entity2, connector2]`, and the 16-way direction values.
- Example strings vendored from `test/examples/` (MIT): `examples/accumulators-2.0.txt`, `scattershot-2.0.txt` and `circuit-connections-2.1.txt`.
- No schema files are vendored. `check()` is a hand-written structural check, not a JSON Schema validator.

## Written for factorycrew (MIT)

`transform`, `bounding_box`, `to_ghosts`, `from_entities`, `wires`, `summary` (counts, size, recipes, quality) and `check`. The placement rule (centre the bounding box on the cursor: odd span on a tile centre, even span on the nearest corner) was checked against `build_blueprint` in the 2.1 stand. Mirror semantics follow the game's F/G flip: the direction becomes 16-d or 8-d, splitter priorities swap sides, and machines toggle `mirror`.
