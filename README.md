# quarry

A small SQL query engine over CSV files, in Python.

quarry reads a CSV file and runs a subset of SQL over it: `SELECT`, `WHERE`,
`ORDER BY`, `LIMIT`. It uses the standard library only. It exists as a readable
version of the two halves of a query engine, a parser and an executor, with
nothing hidden behind a framework.

It pairs with [strata](https://github.com/martin-k-m/strata), an LSM storage
engine. strata stores rows, quarry reads them.

## Use

```bash
python -m quarry "SELECT name, age FROM people.csv WHERE age > 40 ORDER BY age DESC LIMIT 5"
```

A table name written without a path is resolved to `name.csv` in the working
directory. Quote a path that contains a dot or a slash.

## What it supports

- `SELECT` with a column list or `*`
- `WHERE` with `=`, `!=`, `<`, `<=`, `>`, `>=`, combined with `AND`, `OR`, `NOT`
  and parentheses
- Comparisons that are numeric when both sides look like numbers and lexical
  otherwise, so `age > 30` and `name = 'ada'` both do the expected thing with no
  schema to declare
- `ORDER BY` a column or an aggregate output column, ascending or descending
- `LIMIT`
- Aggregates `COUNT(*)`, `COUNT(col)`, `SUM(col)`, `AVG(col)`, `MIN(col)`,
  `MAX(col)`, with `GROUP BY` over one or more columns and an optional `HAVING`
  predicate. With an aggregate but no `GROUP BY`, the whole file is one group.
  `COUNT(*)` counts rows; `COUNT(col)` ignores empty values. `SUM` and `AVG`
  are numeric and skip non-numeric values. `MIN`/`MAX` are numeric when the
  column is all numbers, lexical otherwise. A plain column in the SELECT list
  of an aggregate query must also appear in `GROUP BY`.

Aggregate output columns are named predictably: `COUNT(*)` is `count`, and every
other aggregate is `func(col)` lowercased, so `SUM(age)` is `sum(age)` and
`MIN(name)` is `min(name)`. Use those names in `ORDER BY`.

## How it works

Three stages, one per file:

- `lexer.py` turns the text into a flat stream of tokens.
- `parser.py` is a recursive-descent parser that builds a query tree.
- `engine.py` runs that tree as a pipeline of operators: scan, filter, sort,
  limit, project. Filter and limit stream row by row. Sort is the one step that
  has to see every input row before it can produce the first output row.

## Test

```bash
python -m pytest
```

or, without a local pytest:

```bash
uv run --with pytest python -m pytest
```

## Not done yet

- Joins across two files
- Arithmetic inside expressions

## License

MIT
