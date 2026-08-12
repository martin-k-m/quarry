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
