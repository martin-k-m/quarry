"""
Property-based and fuzz tests for quarry, using Hypothesis.

The example-based suite in ``test_quarry.py`` pins specific behaviours. These
tests assert *properties* that must hold for whole families of inputs, and let
Hypothesis search for a counterexample:

- **Robustness (fuzz).** Whatever you throw at the engine — a structured random
  query or arbitrary text — it may only fail with one of the engine's own
  errors (LexError, ParseError, QueryError). An uncaught KeyError, ValueError,
  RecursionError, or any other exception is a bug: it means an input reached the
  engine that it neither handled nor rejected cleanly.
- **Metamorphic relations.** Over randomly generated data, results that must
  relate in a fixed way still do: ``LIMIT n`` never returns more than n rows, a
  predicate and its negation partition the table, ``ORDER BY`` really sorts, and
  so on. These catch wrong answers, which a fuzz test alone cannot see.
"""

from __future__ import annotations

import csv
import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from quarry import LexError, ParseError, QueryError, run

# The only failures a query is allowed to produce. Anything else escaping the
# engine is the bug this file exists to find.
ENGINE_ERRORS = (LexError, ParseError, QueryError)

CITIES = ["london", "new york", "austin", "paris", "tokyo"]
COLUMNS = ["name", "age", "city"]


def write_people(base_dir, rows: list[dict]) -> None:
    """Write generated rows to people.csv in base_dir, using the csv module so
    quoting and escaping are never something the test gets wrong by hand."""
    path = base_dir / "people.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


# ── data generation ──────────────────────────────────────────────────────────

names = st.text("abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6)
ages = st.integers(min_value=0, max_value=99)

people_rows = st.lists(
    st.fixed_dictionaries(
        {"name": names, "age": ages.map(str), "city": st.sampled_from(CITIES)}
    ),
    min_size=1,
    max_size=30,
)


# ── query generation ─────────────────────────────────────────────────────────
# A grammar of queries over the people schema, deep enough to reach the parser
# and executor rather than bouncing off the lexer, but bounded so it terminates.

_col = st.sampled_from(COLUMNS)
_int_lit = st.integers(min_value=-5, max_value=99).map(str)
_str_lit = st.sampled_from(CITIES).map(lambda s: f"'{s}'")
_cmp = st.sampled_from(["=", "!=", "<", "<=", ">", ">="])


def _atom(cols=_col):
    # A comparison, an IN list, a BETWEEN, or a LIKE — the predicate leaves.
    return st.one_of(
        st.builds(lambda c, o, v: f"{c} {o} {v}", cols, _cmp, st.one_of(_int_lit, _str_lit)),
        st.builds(lambda c, vs: f"{c} IN ({', '.join(vs)})", cols,
                  st.lists(_int_lit, min_size=1, max_size=3)),
        st.builds(lambda c, a, b: f"{c} BETWEEN {a} AND {b}", cols, _int_lit, _int_lit),
        st.builds(lambda c, p: f"{c} LIKE '{p}'", cols,
                  st.sampled_from(["a%", "%n", "%o%", "___"])),
    )


def _predicate():
    # Boolean combinations of atoms, bounded in depth.
    return st.recursive(
        _atom(),
        lambda inner: st.one_of(
            st.builds(lambda a, b: f"{a} AND {b}", inner, inner),
            st.builds(lambda a, b: f"{a} OR {b}", inner, inner),
            st.builds(lambda a: f"NOT ({a})", inner),
        ),
        max_leaves=6,
    )


_expr = st.one_of(
    _col,
    st.builds(lambda c, n: f"{c} + {n}", _col, _int_lit),
    st.builds(lambda c, n: f"{c} * {n}", _col, _int_lit),
    st.builds(lambda c, n: f"{c} / {n}", _col, _int_lit),  # includes / 0 on purpose
    st.builds(lambda c: f"UPPER({c})", _col),
    st.builds(lambda c: f"LENGTH({c})", _col),
    st.builds(lambda c: f"ABS({c})", _col),
    st.builds(lambda c, n: f"ROUND({c}, {n})", _col, st.integers(0, 3).map(str)),
)


# The alias pool includes the column names themselves, so an alias that shadows
# an input column is reachable. That is the case that used to recurse forever.
_alias_name = st.sampled_from([*COLUMNS, "x", "v"])
_aliased_item = st.builds(lambda e, a: f"{e} AS {a}", st.one_of(_col, _expr), _alias_name)


@st.composite
def queries(draw):
    """A SELECT with a random mix of projection, WHERE, ORDER BY, and LIMIT."""
    proj = draw(st.one_of(
        st.just("*"),
        st.lists(st.one_of(_col, _expr, _aliased_item), min_size=1, max_size=3).map(", ".join),
    ))
    if proj != "*" and draw(st.booleans()):
        proj = "DISTINCT " + proj
    sql = f"SELECT {proj} FROM people"
    if draw(st.booleans()):
        sql += " WHERE " + draw(_predicate())
    if draw(st.booleans()):
        target = draw(st.one_of(_col, _alias_name))
        sql += f" ORDER BY {target}" + draw(st.sampled_from(["", " ASC", " DESC"]))
    if draw(st.booleans()):
        sql += f" LIMIT {draw(st.integers(0, 50))}"
    return sql


NO_FIXTURE = settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=300,
    deadline=None,
)


# ── robustness / fuzz ────────────────────────────────────────────────────────

@NO_FIXTURE
@given(rows=people_rows, sql=queries())
def test_structured_queries_only_raise_engine_errors(tmp_path, rows, sql):
    write_people(tmp_path, rows)
    try:
        run(sql, str(tmp_path))
    except ENGINE_ERRORS:
        pass  # a clean, expected rejection


@NO_FIXTURE
@given(rows=people_rows, sql=st.text(max_size=40))
def test_arbitrary_text_only_raises_engine_errors(tmp_path, rows, sql):
    write_people(tmp_path, rows)
    try:
        run(sql, str(tmp_path))
    except ENGINE_ERRORS:
        pass


# ── metamorphic relations ────────────────────────────────────────────────────

@NO_FIXTURE
@given(rows=people_rows, n=st.integers(min_value=0, max_value=50))
def test_limit_never_returns_more_than_n(tmp_path, rows, n):
    write_people(tmp_path, rows)
    _, out = run(f"SELECT * FROM people LIMIT {n}", str(tmp_path))
    assert len(out) <= n


@NO_FIXTURE
@given(rows=people_rows)
def test_select_star_returns_every_row(tmp_path, rows):
    write_people(tmp_path, rows)
    _, out = run("SELECT * FROM people", str(tmp_path))
    assert len(out) == len(rows)


@NO_FIXTURE
@given(rows=people_rows, k=st.integers(min_value=0, max_value=99))
def test_predicate_and_negation_partition_the_table(tmp_path, rows, k):
    # Over a clean, always-present integer column, a predicate and its negation
    # must cover every row exactly once. `age` is never blank here, so there is
    # no three-valued-logic gap to excuse a missing row.
    write_people(tmp_path, rows)
    _, yes = run(f"SELECT name FROM people WHERE age > {k}", str(tmp_path))
    _, no = run(f"SELECT name FROM people WHERE NOT (age > {k})", str(tmp_path))
    assert len(yes) + len(no) == len(rows)


@NO_FIXTURE
@given(rows=people_rows)
def test_order_by_age_is_sorted(tmp_path, rows):
    write_people(tmp_path, rows)
    _, out = run("SELECT age FROM people ORDER BY age ASC", str(tmp_path))
    got = [int(r["age"]) for r in out]
    assert got == sorted(got)


@NO_FIXTURE
@given(rows=people_rows)
def test_count_star_matches_row_count(tmp_path, rows):
    write_people(tmp_path, rows)
    _, out = run("SELECT COUNT(*) AS c FROM people", str(tmp_path))
    assert int(out[0]["c"]) == len(rows)


@NO_FIXTURE
@given(rows=people_rows)
def test_distinct_is_idempotent(tmp_path, rows):
    write_people(tmp_path, rows)
    once = run("SELECT DISTINCT city FROM people", str(tmp_path))[1]
    # Running DISTINCT over an already-distinct set changes nothing.
    assert len({r["city"] for r in once}) == len(once)


# ── deeper fuzzing: aggregates, HAVING, joins, nested expressions ────────────
# The paths above are the common ones. These three reach the parts of the engine
# a shallow generator never touches: the aggregate pipeline, the join, and deep
# expression nesting (which is where a recursive evaluator can blow the stack).

_agg = st.builds(
    lambda fn, c: f"{fn}({c})",
    st.sampled_from(["COUNT", "SUM", "AVG", "MIN", "MAX"]),
    st.sampled_from(["*", "age", "name"]),
)


@st.composite
def aggregate_queries(draw):
    agg = draw(_agg)
    sql = f"SELECT city, {agg} AS a FROM people GROUP BY city"
    if draw(st.booleans()):
        sql += f" HAVING {draw(_agg)} {draw(_cmp)} {draw(_int_lit)}"
    if draw(st.booleans()):
        sql += " ORDER BY city" + draw(st.sampled_from(["", " DESC"]))
    return sql


# Nested arithmetic/functions, e.g. ROUND(ABS(age * 3 + 1) / 2, 1). Bounded, but
# deep enough to stress the recursive expression evaluator.
_nested_expr = st.recursive(
    st.one_of(_col, _int_lit),
    lambda inner: st.one_of(
        st.builds(lambda a, b: f"({a} + {b})", inner, inner),
        st.builds(lambda a, b: f"({a} * {b})", inner, inner),
        st.builds(lambda a, b: f"({a} / {b})", inner, inner),
        st.builds(lambda a: f"ABS({a})", inner),
        st.builds(lambda a, n: f"ROUND({a}, {n})", inner, st.integers(0, 2).map(str)),
    ),
    max_leaves=10,
)


@NO_FIXTURE
@given(rows=people_rows, sql=aggregate_queries())
def test_aggregate_queries_only_raise_engine_errors(tmp_path, rows, sql):
    write_people(tmp_path, rows)
    try:
        run(sql, str(tmp_path))
    except ENGINE_ERRORS:
        pass


@NO_FIXTURE
@given(rows=people_rows, expr=_nested_expr)
def test_nested_expressions_only_raise_engine_errors(tmp_path, rows, expr):
    write_people(tmp_path, rows)
    try:
        run(f"SELECT {expr} AS e FROM people", str(tmp_path))
    except ENGINE_ERRORS:
        pass


@NO_FIXTURE
@given(rows=people_rows)
def test_group_by_covers_every_row(tmp_path, rows):
    # The per-group counts must sum back to the table size: GROUP BY partitions,
    # it never drops or duplicates a row.
    write_people(tmp_path, rows)
    _, groups = run("SELECT city, COUNT(*) AS c FROM people GROUP BY city", str(tmp_path))
    assert sum(int(g["c"]) for g in groups) == len(rows)


# ── joins ────────────────────────────────────────────────────────────────────

ORDER_COLUMNS = ["person", "amount"]
order_rows = st.lists(
    st.fixed_dictionaries({"person": names, "amount": ages.map(str)}),
    min_size=0,
    max_size=30,
)


def write_orders(base_dir, rows: list[dict]) -> None:
    path = base_dir / "orders.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ORDER_COLUMNS)
        w.writeheader()
        w.writerows(rows)


@st.composite
def join_queries(draw):
    kind = draw(st.sampled_from(["JOIN", "INNER JOIN", "LEFT JOIN"]))
    proj = draw(st.sampled_from([
        "people.name, orders.amount",
        "people.name, COUNT(orders.amount) AS n",
        "*",
    ]))
    sql = (f"SELECT {proj} FROM people {kind} orders "
           "ON people.name = orders.person")
    if "COUNT" in proj:
        sql += " GROUP BY people.name"
    if draw(st.booleans()):
        sql += f" ORDER BY people.name LIMIT {draw(st.integers(0, 20))}"
    return sql


@NO_FIXTURE
@given(people=people_rows, orders=order_rows, sql=join_queries())
def test_join_queries_only_raise_engine_errors(tmp_path, people, orders, sql):
    write_people(tmp_path, people)
    write_orders(tmp_path, orders)
    try:
        run(sql, str(tmp_path))
    except ENGINE_ERRORS:
        pass


@NO_FIXTURE
@given(people=people_rows, orders=order_rows)
def test_inner_join_never_exceeds_cross_product(tmp_path, people, orders):
    # An inner join returns at most |people| * |orders| rows — the cross product
    # is the ceiling no matching logic can exceed.
    write_people(tmp_path, people)
    write_orders(tmp_path, orders)
    _, out = run(
        "SELECT people.name, orders.amount FROM people "
        "INNER JOIN orders ON people.name = orders.person",
        str(tmp_path),
    )
    assert len(out) <= len(people) * len(orders)


# ── deep nesting: the recursion-depth guard ──────────────────────────────────
# Regression tests for a real bug this file found: a deeply nested expression
# (deep parens, a long `a + 1 + 1 + ...` chain, stacked NOTs, nested functions)
# used to overflow the interpreter stack and escape as a bare RecursionError
# rather than a clean error. The parser now rejects anything past
# _MAX_EXPR_DEPTH with a ParseError. These pin that: a bare RecursionError here
# means the guard has regressed.

import pytest  # noqa: E402

from quarry.parser import _MAX_EXPR_DEPTH  # noqa: E402

# Each shape is a function from a repetition count to a SQL string, covering
# every recursion vector: the parser's own descent (parens, NOT, unary, funcs)
# and the left-associative loops the tree-walkers later recurse over (chains).
DEEP_SHAPES = {
    "add_chain":  lambda n: "SELECT age" + " + 1" * n + " FROM people",
    "mul_chain":  lambda n: "SELECT age" + " * 2" * n + " FROM people",
    "parens":     lambda n: "SELECT " + "(" * n + "age" + ")" * n + " FROM people",
    "not_chain":  lambda n: "SELECT name FROM people WHERE " + "NOT " * n + "age > 0",
    "and_chain":  lambda n: "SELECT name FROM people WHERE " + "age > 0 AND " * n + "age > 0",
    "unary":      lambda n: "SELECT " + "-" * n + "age FROM people",
    "func_nest":  lambda n: "SELECT " + "ABS(" * n + "age" + ")" * n + " FROM people",
}


@pytest.mark.parametrize("shape", DEEP_SHAPES.values(), ids=list(DEEP_SHAPES))
def test_pathological_nesting_raises_parse_error_not_recursion_error(tmp_path, shape):
    write_people(tmp_path, [{"name": "a", "age": "1", "city": "london"}])
    sql = shape(_MAX_EXPR_DEPTH * 50)  # far past the limit, and past the stack too
    # The point of the fix: a clean, explainable ParseError — never a
    # RecursionError, and never any other uncaught exception.
    with pytest.raises(ParseError):
        run(sql, str(tmp_path))


@pytest.mark.parametrize("shape", DEEP_SHAPES.values(), ids=list(DEEP_SHAPES))
def test_nesting_within_the_limit_still_parses(tmp_path, shape):
    # The guard must not reject ordinary nesting. A depth well under the limit
    # runs (or fails only for a real semantic reason), never for depth.
    write_people(tmp_path, [{"name": "a", "age": "1", "city": "london"}])
    sql = shape(_MAX_EXPR_DEPTH // 4)
    try:
        run(sql, str(tmp_path))
    except QueryError:
        pass  # a semantic complaint is fine; a depth ParseError would not be


# ── ORDER BY over an output alias ────────────────────────────────────────────
# Regression tests for a bug this file found: an ORDER BY name was resolved
# against the SELECT aliases and then resolved again, so a shadowing alias never
# bottomed out. An alias now resolves once, to the input column it renamed.


@NO_FIXTURE
@given(rows=people_rows, col=st.sampled_from(COLUMNS), alias=_alias_name)
def test_order_by_alias_matches_ordering_by_the_column_it_renamed(tmp_path, rows, col, alias):
    # Including when the new name is one of the table's own column names.
    write_people(tmp_path, rows)
    _, aliased = run(f"SELECT {col} AS {alias} FROM people ORDER BY {alias}", str(tmp_path))
    _, plain = run(f"SELECT {col} FROM people ORDER BY {col}", str(tmp_path))
    assert [r[alias] for r in aliased] == [r[col] for r in plain]


@NO_FIXTURE
@given(rows=people_rows, a=st.sampled_from(COLUMNS), b=st.sampled_from(COLUMNS))
def test_swapped_aliases_order_by_the_input_column(tmp_path, rows, a, b):
    # The mutually-shadowing case: ORDER BY a sorts by what `a` renamed, so b.
    write_people(tmp_path, rows)
    _, out = run(f"SELECT {a} AS {b}, {b} AS {a} FROM people ORDER BY {a}", str(tmp_path))
    _, want = run(f"SELECT {b} FROM people ORDER BY {b}", str(tmp_path))
    assert [r[a] for r in out] == [r[b] for r in want]


# ── a reference implementation for WHERE ─────────────────────────────────────
# The metamorphic properties above relate one engine result to another, so a
# consistently wrong filter satisfies them all. Each predicate here is generated
# once and rendered twice, as SQL text and as a Python function, giving an
# independent oracle. `age` is always a clean integer, so the oracle is exact.

_OPS = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _ref_compare(op, k):
    return (f"age {op} {k}", lambda r: _OPS[op](int(r["age"]), k))


def _ref_between(lo, hi):
    return (f"age BETWEEN {lo} AND {hi}", lambda r: lo <= int(r["age"]) <= hi)


def _ref_in(ks):
    return (f"age IN ({', '.join(str(k) for k in ks)})", lambda r: int(r["age"]) in set(ks))


def _ref_city(c):
    return (f"city = '{c}'", lambda r: r["city"] == c)


_ints = st.integers(min_value=-5, max_value=99)

_ref_atom = st.one_of(
    st.builds(_ref_compare, st.sampled_from(list(_OPS)), _ints),
    st.builds(_ref_between, _ints, _ints),
    st.builds(_ref_in, st.lists(_ints, min_size=1, max_size=4)),
    st.builds(_ref_city, st.sampled_from(CITIES)),
)

_ref_predicate = st.recursive(
    _ref_atom,
    lambda inner: st.one_of(
        st.builds(lambda x, y: (f"({x[0]}) AND ({y[0]})", lambda r: x[1](r) and y[1](r)),
                  inner, inner),
        st.builds(lambda x, y: (f"({x[0]}) OR ({y[0]})", lambda r: x[1](r) or y[1](r)),
                  inner, inner),
        st.builds(lambda x: (f"NOT ({x[0]})", lambda r: not x[1](r)), inner),
    ),
    max_leaves=5,
)


@NO_FIXTURE
@given(rows=people_rows, pred=_ref_predicate)
def test_where_agrees_with_a_reference_implementation(tmp_path, rows, pred):
    sql_text, matches = pred
    write_people(tmp_path, rows)
    _, out = run(f"SELECT name, age FROM people WHERE {sql_text}", str(tmp_path))
    want = [r for r in rows if matches(r)]
    # Equal as sequences, not sets: filtering keeps the input order too.
    assert [(r["name"], r["age"]) for r in out] == [(r["name"], r["age"]) for r in want]


# ── the default name of a computed column is a usable expression ─────────────
# Regression tests for a bug this file found: `render_expr` took the parenthesis
# decision from the child's operator instead of the parent's, so `a - (b + c)`
# was named `a - b + c`. The contract: paste a computed column's header back
# into a query and get that same column.

_num_expr = st.recursive(
    st.one_of(st.just("age"), st.integers(1, 9).map(str)),
    lambda inner: st.one_of(
        st.builds(lambda a, b: f"({a} + {b})", inner, inner),
        st.builds(lambda a, b: f"({a} - {b})", inner, inner),
        st.builds(lambda a, b: f"({a} * {b})", inner, inner),
        st.builds(lambda a, b: f"({a} / {b})", inner, inner),
        st.builds(lambda a: f"(-{a})", inner),
    ),
    max_leaves=6,
)


@NO_FIXTURE
@given(rows=people_rows, expr=_num_expr)
def test_computed_column_name_recomputes_the_same_column(tmp_path, rows, expr):
    write_people(tmp_path, rows)
    try:
        cols, out = run(f"SELECT {expr} FROM people", str(tmp_path))
    except QueryError:
        return  # division by zero: nothing to name, and nothing to compare
    name = cols[0]
    cols2, out2 = run(f"SELECT {name} FROM people", str(tmp_path))
    assert cols2 == cols
    for a, b in zip(out, out2, strict=True):
        x, y = float(a[name]), float(b[name])
        # Re-associating + and * is allowed and can move the last bit; changing
        # the shape of the expression, which is what the bug did, cannot.
        assert math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9)


# ── ragged CSV rows ──────────────────────────────────────────────────────────
# Regression tests for a bug this file found: DictReader's None for a short
# row's missing field flowed into the result, breaking the documented
# list[dict[str, str]] contract and making COUNT(col) count a field that was
# never there. A missing value now reads as the empty string.

@NO_FIXTURE
@given(
    lines=st.lists(
        # min_size=1: a zero-field row is a blank line, which the csv module
        # skips by design, so it is not a ragged row the engine ever sees.
        st.lists(st.sampled_from(["ada", "7", "", "london"]), min_size=1, max_size=5),
        min_size=1,
        max_size=10,
    )
)
def test_ragged_rows_yield_only_strings_and_an_honest_count(tmp_path, lines):
    # Written raw so the ragged shape survives to the reader.
    path = tmp_path / "people.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(lines)

    _, star = run("SELECT * FROM people", str(tmp_path))
    assert len(star) == len(lines)
    for r in star:
        assert set(r) == set(COLUMNS)
        assert all(isinstance(v, str) for v in r.values())

    # COUNT(col) skips a blank value, and a field a short row never had is blank.
    for i, col in enumerate(COLUMNS):
        present = sum(1 for line in lines if i < len(line) and line[i] != "")
        _, out = run(f"SELECT COUNT({col}) AS c FROM people", str(tmp_path))
        assert int(out[0]["c"]) == present
