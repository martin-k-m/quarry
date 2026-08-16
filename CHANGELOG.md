# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CI now runs `ruff check` as a lint gate and tests a matrix of Python 3.11 and
  3.13 on Linux and Windows.
- Contribution, security, and changelog documentation, and status badges in the
  README.
- Property-based and fuzz tests (`tests/test_properties.py`, using Hypothesis):
  robustness checks that any query only ever fails with an engine error, and
  metamorphic relations (LIMIT bounds, predicate/negation partitioning, ORDER BY
  ordering, GROUP BY coverage, join cross-product bounds).
- More property tests: `WHERE` is checked against an independent reference
  implementation rather than only against other engine results, a computed
  column's default name is checked to recompute that same column, `ORDER BY`
  over an `AS` alias is checked against ordering by the column it renamed, and
  ragged CSV rows are checked to produce only strings and an honest `COUNT`.
  The structured query generator now also emits `AS` aliases and `DISTINCT`.
- Tests for previously uncovered branches: unqualified names in `JOIN ON` and
  their error paths, scalar function arity and argument errors, `HAVING` with
  `AND` / `OR` / `NOT` and over functions of aggregates, aggregation edge cases,
  and the one-shot command line entry point. Line coverage is now 99%.

### Fixed
- `ORDER BY` over an `AS` alias that shadows one of the table's own column names
  (`SELECT age AS age FROM people ORDER BY age`, or a swapped
  `SELECT a AS b, b AS a ... ORDER BY a`) no longer recurses until the
  interpreter stack runs out and escapes as a bare `RecursionError`. An alias
  now resolves exactly once, to the input column or expression it renamed.
  Found by the new property tests.
- A computed column's default name no longer drops parentheses it needs.
  `a - (b + c)` was named `a - b + c` and `a / (b * c)` was named `a / b * c`,
  names that denote a different computation from the column they label. Whether
  a subexpression needs parentheses depends on the parent operator, not the
  child's. Found by the new property tests.
- A CSV row with fewer fields than the header no longer leaks `None` values into
  the result, which broke the documented `list[dict[str, str]]` return type and
  made `COUNT(col)` count a field the row never had. A missing field now reads
  as the empty string, exactly like a written-but-blank one.
- A pathologically nested expression — deep parentheses, a long `a + 1 + 1 + ...`
  chain, stacked `NOT`s, or nested function calls — no longer overflows the
  interpreter stack with a bare `RecursionError`. The parser now caps expression
  nesting depth and rejects anything deeper with a clean `ParseError`. Found by
  the new fuzz tests.

### Changed
- `zip()` calls over columns and rows now pass `strict=True`, turning a length
  mismatch (which would indicate a bug) into an explicit error instead of a
  silently truncated result.

## [0.1.0]

### Added
- Core query engine over CSV: `SELECT`, `WHERE`, `ORDER BY`, `LIMIT`, standard
  library only, structured as a lexer, a parser, and an executor.
- Aggregation: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, with `GROUP BY` and
  `HAVING`.
- Joins: qualified column names, `INNER JOIN`, `LEFT JOIN`, `DISTINCT`, and
  column aliases (`AS`).
- Expressions: arithmetic, scalar functions, `IN` / `NOT IN`, `LIKE`, and
  `BETWEEN`.
- An interactive REPL (`python -m quarry` with no argument).
- MIT license and continuous integration.

[Unreleased]: https://github.com/martin-k-m/quarry/compare/main...HEAD
[0.1.0]: https://github.com/martin-k-m/quarry/releases/tag/v0.1.0
