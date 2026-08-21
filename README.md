# quarry

[![CI](https://github.com/martin-k-m/quarry/actions/workflows/ci.yml/badge.svg)](https://github.com/martin-k-m/quarry/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/lint-ruff-blue.svg)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A hand-written SQL query engine, in about 1,500 lines of Python with no
dependencies. Text goes in one end and rows come out the other, through the two
halves every query engine has: a parser that turns SQL into a tree, and an
executor that runs that tree as a pipeline of operators.

It reads CSV because that is the least interesting choice available. The point is
the machinery in between, written out in three files you can read in a sitting,
with nothing behind a framework and no query planner deciding things off stage.

If you want to query a CSV file, use [sift](https://github.com/martin-k-m/sift)
or DuckDB, which are faster and will stay faster. quarry is here to be read.

It pairs with [strata](https://github.com/martin-k-m/strata), an LSM storage
engine written the same way. strata is the half that stores rows and survives a
crash; quarry is the half that asks for them.

## Use

```bash
python -m quarry "SELECT name, age FROM 'examples/people.csv' WHERE age > 40 ORDER BY age DESC LIMIT 5"
```

```
name  age
----  ---
eve   62
cara  51
ada   44

(3 rows)
```

A table name written without a path is resolved to `name.csv` in the working
directory, so `FROM people` reads `people.csv` from where you are standing.
Anything containing a dot or a slash is not a bare name and has to be quoted,
which is why the path above is in single quotes.

## Interactive

Run `python -m quarry` with no query argument to open a prompt:

```bash
python -m quarry
```

It reads one query per line, runs it against the current working directory, and
prints the same aligned table the one-shot mode does. A query that fails prints
the error and the prompt stays open, so a typo does not end the session. Blank
lines are ignored. Quit with `.exit` or Ctrl-D. Line editing comes from
`readline` when the platform has it, but it is not required.

## How it works

Three stages, one per file, and the file sizes are roughly the honest ratio of
how hard each part is:

- `lexer.py`, 128 lines. Turns the text into a flat stream of tokens. The
  smallest and least interesting stage, which is the usual shape.
- `parser.py`, 588 lines. Recursive descent, building a query tree. Precedence
  is expressed as the nesting of the functions rather than as a table, so the
  grammar is legible in the call structure.
- `engine.py`, 724 lines. Runs the tree as a pipeline of operators: scan,
  optional hash join, filter, group, sort, limit, project. Filter and limit
  stream row by row. The sort and the join build side are the steps that have to
  see every input row before they can produce the first output row, which is the
  distinction that decides what a query engine can do on a file bigger than
  memory.

Two things worth reading for, because they are where the bodies are buried:

- **Expression evaluation** in `engine.py`. Every operator that takes an
  expression shares it, so `WHERE`, `HAVING`, `ORDER BY` and a projected column
  are the same evaluator under different names. Aliases resolve exactly once,
  which sounds obvious and was a real bug: an alias shadowing a column name
  recursed until the stack ran out.
- **The depth guard** in `parser.py`. Recursive descent on adversarial input is
  a stack overflow waiting to happen, so nesting is capped at 100 and reported
  as a query error rather than a `RecursionError`. A property test found that
  one too.

Both of those, and the two other defects the test suite caught, are written up
in the tests rather than smoothed over.

## What it supports

- `SELECT` with a column list or `*`, and `SELECT DISTINCT` to drop duplicate
  output rows, keeping first-seen order
- Column aliases with `AS`, so `SELECT age AS years` renames the output column
  and lets `ORDER BY years` refer to it
- Qualified column names, `table.column`, alongside bare names
- A single `INNER JOIN` or `LEFT JOIN` of two CSV files on an equality key,
  `SELECT ... FROM a JOIN b ON a.k = b.k`, run as a hash join. Output rows carry
  columns from both sides, named `table.column`. A bare column name resolves
  when only one side has it; a name both sides carry is ambiguous and must be
  qualified. With `LEFT JOIN` (or `LEFT OUTER JOIN`), a left row with no match
  still appears once, with the right side's columns set to the empty string, the
  same value a missing CSV field already reads as. `WHERE`, `ORDER BY`, `LIMIT`
  and aggregates all work on the joined rows.
- Arithmetic `+`, `-`, `*`, `/` with the usual precedence (`*` and `/` bind
  tighter than `+` and `-`), unary minus, and parentheses, over numeric values.
  It works in the `SELECT` list, `WHERE`, `HAVING`, and `ORDER BY`. Division by
  zero and a non-numeric operand are both query errors. A computed `SELECT`
  column is named from its expression text, for example `age * 2`, unless
  renamed with `AS`.
- Scalar functions in an expression, usable in the `SELECT` list, `WHERE`,
  `HAVING` and `ORDER BY`: `UPPER(x)`, `LOWER(x)`, `LENGTH(x)` over text,
  `ROUND(x)`, `ROUND(x, n)`, `ABS(x)` over numbers, and `COALESCE(a, b, ...)`
  which returns the first argument that is neither missing nor empty. The set is
  fixed. A string function over a value that reads as a number, or a numeric
  function over text, is a query error, the same way a bad arithmetic operand is.
  A computed function column is named from its expression text, for example
  `UPPER(name)`, unless renamed with `AS`.
- `WHERE` with `=`, `!=`, `<`, `<=`, `>`, `>=`, combined with `AND`, `OR`, `NOT`
  and parentheses
- `IN` and `NOT IN` against a literal list, in `WHERE` and `HAVING`, as in
  `city IN ('london', 'paris')` or `age NOT IN (30, 40)`. Membership uses the
  same numeric-when-both-look-numeric, lexical-otherwise rule as `=`, so the list
  may hold numbers or strings.
- `LIKE` and `NOT LIKE` pattern matching, in `WHERE` and `HAVING`, as in
  `name LIKE 'a%'`. `%` matches any run of characters, `_` matches any single
  character, and a backslash escapes a literal `%` or `_`. Matching is against
  the string form of the value and is case-sensitive. The literal parts of the
  pattern are matched literally, so a `.` or `*` in the pattern is just that
  character.
- `BETWEEN` and `NOT BETWEEN`, in `WHERE` and `HAVING`, as in
  `age BETWEEN 30 AND 40`. Both ends are inclusive, and the bounds use the same
  numeric-or-lexical comparison as the other operators, so numeric and string
  ranges both work.
- Comparisons that are numeric when both sides look like numbers and lexical
  otherwise, so `age > 30` and `name = 'ada'` both do the expected thing with no
  schema to declare
- `ORDER BY` ascending or descending, over a column, an aggregate output column,
  an `AS` alias, or, on a row query, an arithmetic expression. An alias resolves
  to the input column or expression it renamed, so an alias that reuses one of
  the table's own column names is still unambiguous.
- `LIMIT`
- Aggregates `COUNT(*)`, `COUNT(col)`, `SUM(col)`, `AVG(col)`, `MIN(col)`,
  `MAX(col)`, with `GROUP BY` over one or more columns and an optional `HAVING`
  predicate. With an aggregate but no `GROUP BY`, the whole file is one group.
  `COUNT(*)` counts rows; `COUNT(col)` ignores empty values. `SUM` and `AVG`
  are numeric and skip non-numeric values. `MIN`/`MAX` ignore empty values too,
  and are numeric when every value that is left is a number, lexical otherwise:
  a column of numbers with a blank in it still compares numerically, and a
  blank is never the answer while a real value is present. A plain column in the SELECT list
  of an aggregate query must also appear in `GROUP BY`.
- Ragged CSV rows. A row with fewer fields than the header reads its missing
  fields as the empty string, the same value a written-but-blank field has, and
  a row with more fields than the header ignores the surplus. Every value a
  query returns is a string.
- A bound on expression nesting. An expression may nest at most 100 levels deep,
  counting parentheses, chained operators, stacked `NOT`s, unary minus and
  nested function calls. Past that the parser reports a `ParseError` rather than
  exhausting the interpreter stack, so a pathological query fails the same way
  any other bad query does. Ordinary queries nest a handful of levels.

Aggregate output columns are named predictably: `COUNT(*)` is `count`, and every
other aggregate is `func(col)` lowercased, so `SUM(age)` is `sum(age)` and
`MIN(name)` is `min(name)`. Use those names in `ORDER BY`.

## Test

The property tests need `hypothesis`, so install it alongside pytest:

```bash
pip install pytest hypothesis
python -m pytest
```

or, without a local pytest:

```bash
uv run --with pytest --with hypothesis python -m pytest
```

## Not done yet

- Arithmetic is numeric only. There is no string concatenation. The scalar
  function set is fixed at the six listed above; there is no way to register
  more, and no user-defined functions.
- `IN` takes a literal list only, not a subquery. There is no `IN (SELECT ...)`.
- `LIKE` is case-sensitive, with no `ILIKE` and no `ESCAPE` clause to pick a
  different escape character; the escape is always a backslash. It matches the
  string form of the value, so a numeric column is matched as its text.
- The REPL runs every query against the current working directory. There is no
  way to change directories from the prompt, and no history file, multi-line
  query, or command other than `.exit`.
- The string functions decide a wrong argument type by whether the value reads
  as a number, the engine's only notion of type. A text column whose values all
  happen to be digits is therefore treated as numeric, so `UPPER` over it is a
  query error rather than a no-op.
- Joins of more than two files. A join is still a single INNER or LEFT join of
  exactly two CSVs on one equality key. There is no RIGHT or FULL join.
- Arithmetic in `ORDER BY` works on a row query, not on a grouped one; an
  aggregate query still sorts on a selected output column by name.

## Related

Four small tools that each do one thing to a table of data, and are written to
be read rather than to compete with DuckDB:

- [csvpeek](https://github.com/martin-k-m/csvpeek) profiles a file: column
  types, null counts, distributions.
- [sift](https://github.com/martin-k-m/sift) queries one: filter, sort,
  aggregate, in one pass where it can.
- [drift](https://github.com/martin-k-m/drift) diffs two of them, in Rust.
- **quarry** is the long way round, a hand-written SQL parser and executor
  meant to be read.

## License

MIT
