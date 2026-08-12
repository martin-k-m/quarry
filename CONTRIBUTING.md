# Contributing to quarry

Thanks for taking a look. `quarry` exists to be a *readable* query engine — a
parser and an executor with nothing hidden behind a framework — so the bar for
a change is that it stays readable and dependency-free.

## Setup

The standard library is all quarry needs to run; `pytest` and `ruff` to develop.

```bash
git clone https://github.com/martin-k-m/quarry
cd quarry
pip install -e .
pip install pytest ruff
python -m pytest -q
ruff check .
```

## Ground rules

- **Zero runtime dependencies.** The `dependencies` list stays empty. The whole
  point is that the parser and executor are readable in one sitting.
- **The engine is two halves — a parser (`lexer.py`, `parser.py`) and an
  executor (`engine.py`).** Keep them separate. The parser turns text into an
  AST and knows nothing about CSV; the executor runs the AST and knows nothing
  about tokens.
- **Errors name the problem in SQL terms** ("unknown column 'x'; available:
  ..."), not Python tracebacks. A stack trace reaching the user is a bug.
- **New SQL surface comes with tests.** `tests/test_quarry.py` is organised by
  feature (WHERE, JOIN, GROUP BY, functions, ...); add to the matching section.

## Before you open a pull request

Both gates run in CI; run them locally first:

```bash
ruff check .
python -m pytest -q
```

Keep pull requests focused on one feature or fix. The test file is the
specification — if behaviour changes, a test changes with it.

## Reporting bugs

Open an issue with the exact query, a few rows of the CSV, the result you
expected, and what you got. A failing `pytest` case is the ideal bug report.
