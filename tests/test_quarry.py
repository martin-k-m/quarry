import io

import pytest

from quarry import LexError, ParseError, QueryError, run

CSV = """\
name,age,city
ada,36,london
grace,42,new york
alan,41,london
edsger,54,austin
"""


@pytest.fixture
def data(tmp_path):
    (tmp_path / "people.csv").write_text(CSV, encoding="utf-8")
    return str(tmp_path)


def rows_of(sql, base):
    _, rows = run(sql, base)
    return rows


def test_select_star_returns_every_row(data):
    cols, rows = run("SELECT * FROM people", data)
    assert cols == ["name", "age", "city"]
    assert len(rows) == 4


def test_projection_keeps_only_named_columns(data):
    cols, rows = run("SELECT name, age FROM people", data)
    assert cols == ["name", "age"]
    assert rows[0] == {"name": "ada", "age": "36"}


def test_where_numeric_is_numeric_not_lexical(data):
    # As strings, "6" > "42"; the engine must compare 6 and 42 as numbers.
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE age > 41", data)}
    assert names == {"grace", "edsger"}


def test_where_string_equality(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE city = 'london'", data)}
    assert names == {"ada", "alan"}


def test_and_or_precedence(data):
    sql = "SELECT name FROM people WHERE city = 'london' AND age > 40 OR name = 'edsger'"
    names = {r["name"] for r in rows_of(sql, data)}
    assert names == {"alan", "edsger"}


def test_not(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE NOT city = 'london'", data)}
    assert names == {"grace", "edsger"}


def test_order_by_numeric_desc(data):
    ages = [r["age"] for r in rows_of("SELECT age FROM people ORDER BY age DESC", data)]
    assert ages == ["54", "42", "41", "36"]


def test_order_by_string_asc_then_limit(data):
    names = [r["name"] for r in rows_of("SELECT name FROM people ORDER BY name ASC LIMIT 2", data)]
    assert names == ["ada", "alan"]


def test_unknown_column_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT height FROM people", data)


def test_unknown_column_in_where_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT name FROM people WHERE height > 10", data)


def test_missing_table_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT * FROM ghosts", data)


def test_syntax_error_is_a_parse_error(data):
    with pytest.raises(ParseError):
        run("SELECT FROM people", data)


def test_count_star_over_whole_file(data):
    cols, rows = run("SELECT COUNT(*) FROM people", data)
    assert cols == ["count"]
    assert rows == [{"count": "4"}]


def test_sum_avg_min_max_no_group_by(data):
    cols, rows = run("SELECT SUM(age), AVG(age), MIN(age), MAX(age) FROM people", data)
    assert cols == ["sum(age)", "avg(age)", "min(age)", "max(age)"]
    assert rows == [{"sum(age)": "173", "avg(age)": "43.25", "min(age)": "36", "max(age)": "54"}]


def test_min_of_text_column_is_lexical(data):
    _, rows = run("SELECT MIN(name), MAX(name) FROM people", data)
    assert rows == [{"min(name)": "ada", "max(name)": "grace"}]


def test_count_col_ignores_empty_values(tmp_path):
    (tmp_path / "t.csv").write_text("name,age\nada,36\nbob,\ncy,41\n", encoding="utf-8")
    _, rows = run("SELECT COUNT(*), COUNT(age) FROM t", str(tmp_path))
    assert rows == [{"count": "3", "count(age)": "2"}]


def test_group_by_one_column(data):
    cols, rows = run("SELECT city, COUNT(*) FROM people GROUP BY city", data)
    assert cols == ["city", "count"]
    by_city = {r["city"]: r["count"] for r in rows}
    assert by_city == {"london": "2", "new york": "1", "austin": "1"}


def test_group_by_multiple_columns(tmp_path):
    csv = "city,team,n\nlondon,a,1\nlondon,a,2\nlondon,b,3\nparis,a,4\n"
    (tmp_path / "g.csv").write_text(csv, encoding="utf-8")
    _, rows = run("SELECT city, team, SUM(n) FROM g GROUP BY city, team", str(tmp_path))
    got = {(r["city"], r["team"]): r["sum(n)"] for r in rows}
    assert got == {("london", "a"): "3", ("london", "b"): "3", ("paris", "a"): "4"}


def test_having_filters_groups(data):
    sql = "SELECT city, COUNT(*) FROM people GROUP BY city HAVING COUNT(*) > 1"
    _, rows = run(sql, data)
    assert rows == [{"city": "london", "count": "2"}]


def test_order_by_aggregate(data):
    sql = "SELECT city, COUNT(*) FROM people GROUP BY city ORDER BY count DESC"
    counts = [r["count"] for r in run(sql, data)[1]]
    assert counts == ["2", "1", "1"]


def test_order_by_aggregate_call_syntax(data):
    sql = "SELECT city, SUM(age) FROM people GROUP BY city ORDER BY sum(age) DESC LIMIT 1"
    _, rows = run(sql, data)
    assert rows == [{"city": "london", "sum(age)": "77"}]


def test_plain_column_not_in_group_by_is_an_error(data):
    with pytest.raises(QueryError):
        run("SELECT name, COUNT(*) FROM people GROUP BY city", data)


# ── Qualified column names ───────────────────────────────────────────────────
def test_qualified_column_resolves_single_table(data):
    # people.age with one table resolves against that table.
    cols, rows = run("SELECT people.name, people.age FROM people WHERE people.age > 41", data)
    assert cols == ["people.name", "people.age"]
    assert {r["people.name"] for r in rows} == {"grace", "edsger"}


def test_qualified_unknown_table_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT other.name FROM people", data)


# ── Joins ────────────────────────────────────────────────────────────────────
@pytest.fixture
def joined(tmp_path):
    people = "id,name,city\n1,ada,london\n2,grace,new york\n3,alan,london\n"
    orders = "id,person,amount\n10,1,50\n11,1,20\n12,3,99\n13,9,5\n"
    (tmp_path / "people.csv").write_text(people, encoding="utf-8")
    (tmp_path / "orders.csv").write_text(orders, encoding="utf-8")
    return str(tmp_path)


def test_inner_join_matches_on_key(joined):
    sql = "SELECT people.name, orders.amount FROM people JOIN orders ON people.id = orders.person"
    cols, rows = run(sql, joined)
    assert cols == ["people.name", "orders.amount"]
    pairs = {(r["people.name"], r["orders.amount"]) for r in rows}
    # order id 13 has person 9 which matches nobody; grace (id 2) has no order.
    assert pairs == {("ada", "50"), ("ada", "20"), ("alan", "99")}


def test_join_star_exposes_both_sides_qualified(joined):
    sql = "SELECT * FROM people JOIN orders ON people.id = orders.person"
    cols, _ = run(sql, joined)
    assert cols == [
        "people.id",
        "people.name",
        "people.city",
        "orders.id",
        "orders.person",
        "orders.amount",
    ]


def test_join_bare_unambiguous_column_resolves(joined):
    # name is only on people, amount only on orders, so bare names resolve.
    sql = "SELECT name, amount FROM people JOIN orders ON people.id = orders.person"
    _, rows = run(sql, joined)
    assert {(r["name"], r["amount"]) for r in rows} == {
        ("ada", "50"),
        ("ada", "20"),
        ("alan", "99"),
    }


def test_join_bare_ambiguous_column_is_a_query_error(joined):
    # id lives on both sides; a bare reference must be rejected.
    sql = "SELECT id FROM people JOIN orders ON people.id = orders.person"
    with pytest.raises(QueryError):
        run(sql, joined)


def test_join_with_where_and_order_by(joined):
    sql = (
        "SELECT people.name, orders.amount FROM people JOIN orders "
        "ON people.id = orders.person WHERE orders.amount > 10 "
        "ORDER BY orders.amount DESC"
    )
    _, rows = run(sql, joined)
    assert [(r["people.name"], r["orders.amount"]) for r in rows] == [
        ("alan", "99"),
        ("ada", "50"),
        ("ada", "20"),
    ]


def test_join_aggregate_group_by(joined):
    sql = (
        "SELECT people.name, SUM(orders.amount) FROM people JOIN orders "
        "ON people.id = orders.person GROUP BY people.name"
    )
    _, rows = run(sql, joined)
    got = {r["people.name"]: r["sum(orders.amount)"] for r in rows}
    assert got == {"ada": "70", "alan": "99"}


# ── DISTINCT ─────────────────────────────────────────────────────────────────
def test_distinct_dedupes_preserving_first_seen_order(data):
    rows = rows_of("SELECT DISTINCT city FROM people", data)
    assert [r["city"] for r in rows] == ["london", "new york", "austin"]


def test_distinct_with_order_by(data):
    rows = rows_of("SELECT DISTINCT city FROM people ORDER BY city ASC", data)
    assert [r["city"] for r in rows] == ["austin", "london", "new york"]


# ── Aliases ──────────────────────────────────────────────────────────────────
def test_column_alias_renames_output(data):
    cols, rows = run("SELECT name AS who, age AS years FROM people LIMIT 1", data)
    assert cols == ["who", "years"]
    assert rows[0] == {"who": "ada", "years": "36"}


def test_aggregate_alias_and_order_by_alias(data):
    sql = "SELECT city, COUNT(*) AS n FROM people GROUP BY city ORDER BY n DESC"
    cols, rows = run(sql, data)
    assert cols == ["city", "n"]
    assert [r["n"] for r in rows] == ["2", "1", "1"]


def test_order_by_column_alias_non_aggregate(data):
    sql = "SELECT name AS who FROM people ORDER BY who ASC LIMIT 2"
    rows = rows_of(sql, data)
    assert [r["who"] for r in rows] == ["ada", "alan"]


# ── Arithmetic ───────────────────────────────────────────────────────────────
def test_arithmetic_precedence_mul_before_add(data):
    # 2 + 3 * 4 is 14, not 20, so * must bind tighter than +.
    cols, rows = run("SELECT age + 3 * 4 AS x FROM people ORDER BY age ASC LIMIT 1", data)
    assert cols == ["x"]
    assert rows[0] == {"x": "48"}  # ada, 36 + 12


def test_arithmetic_parens_override_precedence(data):
    rows = rows_of("SELECT (age + 4) * 2 AS x FROM people ORDER BY age ASC LIMIT 1", data)
    assert rows[0] == {"x": "80"}  # (36 + 4) * 2


def test_arithmetic_unary_minus(data):
    rows = rows_of("SELECT -age AS neg FROM people ORDER BY age ASC LIMIT 1", data)
    assert rows[0] == {"neg": "-36"}


def test_arithmetic_division_is_float(data):
    rows = rows_of("SELECT age / 8 AS q FROM people ORDER BY age ASC LIMIT 1", data)
    assert rows[0] == {"q": "4.5"}  # 36 / 8


def test_computed_column_default_name_is_expression_text(data):
    cols, _ = run("SELECT age * 2 FROM people", data)
    assert cols == ["age * 2"]


def test_division_by_zero_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT age / 0 FROM people", data)


def test_arithmetic_on_non_numeric_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT name + 1 FROM people", data)


def test_arithmetic_in_where(data):
    # age * 2 > 100 selects ages above 50: only edsger at 54.
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE age * 2 > 100", data)}
    assert names == {"edsger"}


def test_arithmetic_in_order_by(data):
    # 100 - age reverses the age order, so the oldest sorts first.
    names = [r["name"] for r in rows_of("SELECT name FROM people ORDER BY 100 - age ASC", data)]
    assert names == ["edsger", "grace", "alan", "ada"]


def test_arithmetic_in_having(tmp_path):
    csv = "city,n\nlondon,10\nlondon,10\nparis,1\n"
    (tmp_path / "h.csv").write_text(csv, encoding="utf-8")
    sql = "SELECT city, SUM(n) FROM h GROUP BY city HAVING SUM(n) / 2 > 5"
    _, rows = run(sql, str(tmp_path))
    assert rows == [{"city": "london", "sum(n)": "20"}]


# ── LEFT JOIN ────────────────────────────────────────────────────────────────
def test_left_join_keeps_unmatched_left_rows_with_empty_right(joined):
    sql = ("SELECT people.name, orders.amount FROM people "
           "LEFT JOIN orders ON people.id = orders.person")
    _, rows = run(sql, joined)
    pairs = {(r["people.name"], r["orders.amount"]) for r in rows}
    # ada has two orders, alan one, grace none (empty amount); order 13 drops out.
    assert pairs == {("ada", "50"), ("ada", "20"), ("alan", "99"), ("grace", "")}


def test_left_join_matched_rows_are_correct(joined):
    sql = ("SELECT people.name, orders.amount FROM people "
           "LEFT JOIN orders ON people.id = orders.person")
    _, rows = run(sql, joined)
    ada = sorted(r["orders.amount"] for r in rows if r["people.name"] == "ada")
    assert ada == ["20", "50"]


def test_left_join_outer_keyword_is_accepted(joined):
    sql = "SELECT people.name FROM people LEFT OUTER JOIN orders ON people.id = orders.person"
    _, rows = run(sql, joined)
    assert {r["people.name"] for r in rows} == {"ada", "alan", "grace"}


def test_left_join_with_where_on_left_column(joined):
    # A WHERE on a left column keeps the unmatched row when it qualifies.
    sql = (
        "SELECT people.name, orders.amount FROM people LEFT JOIN orders "
        "ON people.id = orders.person WHERE people.city = 'new york'"
    )
    _, rows = run(sql, joined)
    assert rows == [{"people.name": "grace", "orders.amount": ""}]


def test_left_join_aggregation_counts_matches_only(joined):
    # COUNT(orders.amount) ignores the empty right side, so grace counts zero.
    sql = (
        "SELECT people.name, COUNT(orders.amount) FROM people LEFT JOIN orders "
        "ON people.id = orders.person GROUP BY people.name"
    )
    _, rows = run(sql, joined)
    got = {r["people.name"]: r["count(orders.amount)"] for r in rows}
    assert got == {"ada": "2", "alan": "1", "grace": "0"}


# ── Scalar functions ─────────────────────────────────────────────────────────
def test_upper_and_lower(data):
    rows = rows_of(
        "SELECT UPPER(name) AS u, LOWER(city) AS l FROM people ORDER BY name ASC LIMIT 1", data
    )
    assert rows[0] == {"u": "ADA", "l": "london"}


def test_length_returns_a_number(data):
    rows = rows_of("SELECT LENGTH(name) AS n FROM people ORDER BY name ASC LIMIT 1", data)
    assert rows[0] == {"n": "3"}  # len("ada")


def test_upper_of_a_number_is_a_query_error(data):
    # age reads as numeric, so a string function over it is the wrong type.
    with pytest.raises(QueryError):
        run("SELECT UPPER(age) FROM people", data)


def test_length_of_a_number_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT LENGTH(age) FROM people", data)


def test_round_without_digits(data):
    rows = rows_of("SELECT ROUND(age / 8) AS r FROM people ORDER BY age ASC LIMIT 1", data)
    assert rows[0] == {"r": "4"}  # round(36 / 8) = round(4.5) = 4


def test_round_with_digits(data):
    rows = rows_of("SELECT ROUND(age / 8, 2) AS r FROM people ORDER BY age ASC LIMIT 1", data)
    assert rows[0] == {"r": "4.5"}  # 36 / 8 = 4.5, to two places


def test_round_digits_three_places(tmp_path):
    (tmp_path / "n.csv").write_text("v\n1\n", encoding="utf-8")
    rows = rows_of("SELECT ROUND(v / 3, 3) AS r FROM n", str(tmp_path))
    assert rows[0] == {"r": "0.333"}


def test_round_of_text_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT ROUND(name) FROM people", data)


def test_abs_happy_path(data):
    rows = rows_of("SELECT ABS(0 - age) AS a FROM people ORDER BY age ASC LIMIT 1", data)
    assert rows[0] == {"a": "36"}


def test_abs_of_text_is_a_query_error(data):
    with pytest.raises(QueryError):
        run("SELECT ABS(name) FROM people", data)


def test_coalesce_picks_first_present_value(tmp_path):
    csv = "a,b\n,x\ny,z\n,\n"
    (tmp_path / "c.csv").write_text(csv, encoding="utf-8")
    rows = rows_of("SELECT COALESCE(a, b, 'none') AS v FROM c", str(tmp_path))
    assert [r["v"] for r in rows] == ["x", "y", "none"]


def test_function_default_name_is_expression_text(data):
    cols, _ = run("SELECT UPPER(name) FROM people", data)
    assert cols == ["UPPER(name)"]


def test_function_composed_with_arithmetic(data):
    # LENGTH returns a number that then takes part in arithmetic.
    rows = rows_of("SELECT LENGTH(name) * 2 AS n FROM people ORDER BY name ASC LIMIT 1", data)
    assert rows[0] == {"n": "6"}  # len("ada") * 2


def test_function_in_where(data):
    names = {
        r["name"] for r in rows_of("SELECT name FROM people WHERE UPPER(city) = 'LONDON'", data)
    }
    assert names == {"ada", "alan"}


def test_function_in_order_by(data):
    # Order by name length ascending: ada(3), alan(4), grace(5), edsger(6).
    names = [r["name"] for r in rows_of("SELECT name FROM people ORDER BY LENGTH(name) ASC", data)]
    assert names == ["ada", "alan", "grace", "edsger"]


# ── IN and NOT IN ────────────────────────────────────────────────────────────
def test_in_string_list(data):
    names = {
        r["name"]
        for r in rows_of("SELECT name FROM people WHERE city IN ('london', 'austin')", data)
    }
    assert names == {"ada", "alan", "edsger"}


def test_in_numeric_list(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE age IN (36, 42)", data)}
    assert names == {"ada", "grace"}


def test_not_in_list(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE age NOT IN (36, 42)", data)}
    assert names == {"alan", "edsger"}


def test_in_combined_with_and_or(data):
    sql = "SELECT name FROM people WHERE city IN ('london') AND age > 40 OR name = 'grace'"
    names = {r["name"] for r in rows_of(sql, data)}
    assert names == {"alan", "grace"}


def test_in_uses_numeric_comparison(tmp_path):
    # "6" and "6.0" match numerically the way = does, not lexically.
    (tmp_path / "m.csv").write_text("v\n6.0\n7\n", encoding="utf-8")
    rows = rows_of("SELECT v FROM m WHERE v IN (6)", str(tmp_path))
    assert [r["v"] for r in rows] == ["6.0"]


def test_not_in_in_having(tmp_path):
    csv = "city,n\nlondon,1\nparis,2\nrome,3\n"
    (tmp_path / "hh.csv").write_text(csv, encoding="utf-8")
    sql = "SELECT city, SUM(n) FROM hh GROUP BY city HAVING SUM(n) NOT IN (2)"
    got = {r["city"]: r["sum(n)"] for r in run(sql, str(tmp_path))[1]}
    assert got == {"london": "1", "rome": "3"}


# ── LIKE and NOT LIKE ────────────────────────────────────────────────────────
def test_like_percent_prefix(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE name LIKE 'a%'", data)}
    assert names == {"ada", "alan"}


def test_like_percent_both_sides(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE city LIKE '%o%'", data)}
    assert names == {"ada", "grace", "alan"}  # london, new york, london


def test_like_underscore_single_char(data):
    # a_a matches a three-letter value starting and ending in a: ada.
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE name LIKE 'a_a'", data)}
    assert names == {"ada"}


def test_like_is_case_sensitive(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE name LIKE 'A%'", data)}
    assert names == set()


def test_not_like(data):
    names = {r["name"] for r in rows_of("SELECT name FROM people WHERE name NOT LIKE 'a%'", data)}
    assert names == {"grace", "edsger"}


def test_like_escaped_literal_percent(tmp_path):
    # A backslash escapes the % so it is matched literally, not as a wildcard.
    (tmp_path / "p.csv").write_text("v\n50%\n50x\n100%\n", encoding="utf-8")
    rows = rows_of(r"SELECT v FROM p WHERE v LIKE '50\%'", str(tmp_path))
    assert [r["v"] for r in rows] == ["50%"]


def test_like_regex_metacharacters_are_literal(tmp_path):
    # A dot in the pattern must match a literal dot, not any character.
    (tmp_path / "d.csv").write_text("v\na.c\naxc\n", encoding="utf-8")
    rows = rows_of("SELECT v FROM d WHERE v LIKE 'a.c'", str(tmp_path))
    assert [r["v"] for r in rows] == ["a.c"]


def test_like_in_having(tmp_path):
    csv = "city,n\nlondon,1\nparis,2\nlisbon,3\n"
    (tmp_path / "lh.csv").write_text(csv, encoding="utf-8")
    sql = "SELECT city, SUM(n) FROM lh GROUP BY city HAVING city LIKE 'l%'"
    got = {r["city"]: r["sum(n)"] for r in run(sql, str(tmp_path))[1]}
    assert got == {"london": "1", "lisbon": "3"}


# ── BETWEEN and NOT BETWEEN ──────────────────────────────────────────────────
def test_between_numeric_inclusive(data):
    names = {
        r["name"] for r in rows_of("SELECT name FROM people WHERE age BETWEEN 36 AND 42", data)
    }
    assert names == {"ada", "grace", "alan"}  # 36, 42, 41; 54 excluded


def test_between_excludes_out_of_range(data):
    names = {
        r["name"] for r in rows_of("SELECT name FROM people WHERE age BETWEEN 40 AND 50", data)
    }
    assert names == {"grace", "alan"}  # 42, 41


def test_between_lexical(data):
    # String bounds compare lexically: names from 'a' up to 'b'.
    names = {
        r["name"] for r in rows_of("SELECT name FROM people WHERE name BETWEEN 'a' AND 'b'", data)
    }
    assert names == {"ada", "alan"}


def test_not_between(data):
    names = {
        r["name"] for r in rows_of("SELECT name FROM people WHERE age NOT BETWEEN 36 AND 42", data)
    }
    assert names == {"edsger"}  # only 54 is outside


def test_between_combined_with_and(data):
    sql = "SELECT name FROM people WHERE age BETWEEN 30 AND 50 AND city = 'london'"
    names = {r["name"] for r in rows_of(sql, data)}
    assert names == {"ada", "alan"}


def test_between_in_having(tmp_path):
    csv = "city,n\nlondon,1\nparis,2\nrome,3\n"
    (tmp_path / "bh.csv").write_text(csv, encoding="utf-8")
    sql = "SELECT city, SUM(n) FROM bh GROUP BY city HAVING SUM(n) BETWEEN 2 AND 3"
    got = {r["city"]: r["sum(n)"] for r in run(sql, str(tmp_path))[1]}
    assert got == {"paris": "2", "rome": "3"}


# ── REPL ─────────────────────────────────────────────────────────────────────
def _run_repl(monkeypatch, capsys, script, cwd):

    from quarry.__main__ import main

    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    code = main([])
    out = capsys.readouterr().out
    return code, out


def test_repl_runs_a_query_and_exits(data, monkeypatch, capsys):
    code, out = _run_repl(
        monkeypatch, capsys, "SELECT name FROM people WHERE age > 50\n.exit\n", data
    )
    assert code == 0
    assert "edsger" in out
    assert "(1 row)" in out


def test_repl_banner_is_printed(data, monkeypatch, capsys):
    code, out = _run_repl(monkeypatch, capsys, ".exit\n", data)
    assert "quarry" in out.splitlines()[0]


def test_repl_bad_query_does_not_stop_session(data, monkeypatch, capsys):
    script = "SELECT nope FROM people\nSELECT name FROM people WHERE age > 50\n.exit\n"
    code, out = _run_repl(monkeypatch, capsys, script, data)
    assert code == 0
    assert "error:" in out  # the bad query reported an error
    assert "edsger" in out  # and the next query still ran


def test_repl_blank_lines_are_ignored(data, monkeypatch, capsys):
    code, out = _run_repl(monkeypatch, capsys, "\n\nSELECT COUNT(*) FROM people\n.exit\n", data)
    assert code == 0
    assert "(1 row)" in out


def test_repl_eof_ends_session(data, monkeypatch, capsys):
    # No .exit, just EOF after one query.
    code, out = _run_repl(monkeypatch, capsys, "SELECT COUNT(*) FROM people\n", data)
    assert code == 0
    assert "4" in out


def test_quoted_string_with_embedded_quote(tmp_path):
    # The stored value has one apostrophe; the SQL literal 'o''brien' must decode
    # its doubled quote to that single one before the comparison can match.
    (tmp_path / "q.csv").write_text("label\no'brien\nsmith\n", encoding="utf-8")
    rows = rows_of("SELECT label FROM q WHERE label = 'o''brien'", str(tmp_path))
    assert [r["label"] for r in rows] == ["o'brien"]


# ── JOIN ON with unqualified column names ────────────────────────────────────
# The ON clause accepts bare names and works out which side each belongs to.
# Every branch of that resolution is reachable from a query, so pin them all.

def test_join_on_bare_name_present_on_both_sides_is_ambiguous(joined):
    # `id` is a column of both tables, so a bare `id` in ON has no single meaning.
    with pytest.raises(QueryError, match="ambiguous in JOIN ON"):
        run("SELECT people.name FROM people JOIN orders ON id = person", joined)


def test_join_on_bare_names_unique_to_one_side_resolve(tmp_path):
    (tmp_path / "people.csv").write_text("pid,name\n1,ada\n2,grace\n", encoding="utf-8")
    (tmp_path / "orders.csv").write_text("oid,pid_ref\n10,1\n11,1\n", encoding="utf-8")
    sql = "SELECT people.name, orders.oid FROM people JOIN orders ON pid = pid_ref"
    _, rows = run(sql, str(tmp_path))
    assert [(r["people.name"], r["orders.oid"]) for r in rows] == [("ada", "10"), ("ada", "11")]


def test_join_on_bare_names_resolve_in_either_order(tmp_path):
    # The right table's column written first still resolves to the right side.
    (tmp_path / "people.csv").write_text("pid,name\n1,ada\n", encoding="utf-8")
    (tmp_path / "orders.csv").write_text("oid,pid_ref\n10,1\n", encoding="utf-8")
    sql = "SELECT people.name, orders.oid FROM people JOIN orders ON pid_ref = pid"
    _, rows = run(sql, str(tmp_path))
    assert rows == [{"people.name": "ada", "orders.oid": "10"}]


def test_join_on_unknown_bare_column(joined):
    with pytest.raises(QueryError, match="unknown column 'nope' in JOIN ON"):
        run("SELECT name FROM people JOIN orders ON nope = person", joined)


def test_join_on_unknown_qualified_column_on_the_left(joined):
    with pytest.raises(QueryError, match="unknown column 'people.nope'"):
        run("SELECT name FROM people JOIN orders ON people.nope = orders.person", joined)


def test_join_on_unknown_qualified_column_on_the_right(joined):
    with pytest.raises(QueryError, match="unknown column 'orders.nope'"):
        run("SELECT name FROM people JOIN orders ON people.id = orders.nope", joined)


def test_join_on_unknown_table_qualifier(joined):
    with pytest.raises(QueryError, match="unknown table 'nope'"):
        run("SELECT name FROM people JOIN orders ON nope.id = orders.person", joined)


def test_join_on_both_columns_from_the_same_side(joined):
    with pytest.raises(QueryError, match="a column from each table"):
        run("SELECT name FROM people JOIN orders ON people.id = people.id", joined)


def test_join_on_must_be_equality(joined):
    with pytest.raises(ParseError, match="only equality"):
        run("SELECT name FROM people JOIN orders ON people.id > orders.person", joined)


def test_join_needs_two_differently_named_tables(joined):
    with pytest.raises(QueryError, match="differently named"):
        run("SELECT id FROM people JOIN people ON id = id", joined)


# ── scalar function arity and argument errors ────────────────────────────────

def test_unknown_function_is_a_query_error(data):
    with pytest.raises(QueryError, match="unknown function NOPE"):
        run("SELECT NOPE(name) FROM people", data)


@pytest.mark.parametrize("fn", ["UPPER", "LOWER", "LENGTH"])
def test_text_functions_take_exactly_one_argument(data, fn):
    with pytest.raises(QueryError, match="takes one argument, got 2"):
        run(f"SELECT {fn}(name, city) FROM people", data)


def test_abs_takes_exactly_one_argument(data):
    with pytest.raises(QueryError, match="ABS takes one argument, got 2"):
        run("SELECT ABS(age, age) FROM people", data)


def test_round_takes_one_or_two_arguments(data):
    with pytest.raises(QueryError, match="ROUND takes one or two arguments, got 3"):
        run("SELECT ROUND(age, 1, 1) FROM people", data)


def test_round_digit_count_must_be_whole(data):
    with pytest.raises(QueryError, match="digit count must be a whole number"):
        run("SELECT ROUND(age, 1.5) FROM people", data)


def test_lower_lowercases(data):
    _, rows = run("SELECT LOWER(city) AS c FROM people WHERE name = 'grace'", data)
    assert rows == [{"c": "new york"}]


def test_coalesce_returns_empty_when_every_argument_is_blank(tmp_path):
    (tmp_path / "t.csv").write_text("a,b\n,\n", encoding="utf-8")
    _, rows = run("SELECT COALESCE(a, b) AS c FROM t", str(tmp_path))
    assert rows == [{"c": ""}]


def test_string_literal_in_a_computed_column_name_keeps_its_quotes(data):
    cols, _ = run("SELECT COALESCE(name, 'n/a') FROM people", data)
    assert cols == ["COALESCE(name, 'n/a')"]


# ── HAVING beyond a single comparison ────────────────────────────────────────

def test_having_combines_conditions_with_and(data):
    sql = ("SELECT city, COUNT(*) AS c FROM people GROUP BY city "
           "HAVING COUNT(*) > 1 AND SUM(age) > 70")
    assert {r["city"] for r in rows_of(sql, data)} == {"london"}


def test_having_combines_conditions_with_or(data):
    sql = ("SELECT city, COUNT(*) AS c FROM people GROUP BY city "
           "HAVING COUNT(*) > 1 OR SUM(age) > 50")
    assert {r["city"] for r in rows_of(sql, data)} == {"london", "austin"}


def test_having_with_not(data):
    sql = ("SELECT city, COUNT(*) AS c FROM people GROUP BY city "
           "HAVING NOT (COUNT(*) > 1)")
    assert {r["city"] for r in rows_of(sql, data)} == {"new york", "austin"}


def test_having_over_a_function_of_an_aggregate(data):
    sql = ("SELECT city, AVG(age) AS a FROM people GROUP BY city "
           "HAVING ROUND(AVG(age), 0) > 45")
    assert {r["city"] for r in rows_of(sql, data)} == {"austin"}


def test_having_on_a_bare_aggregate_is_truthy_when_non_zero(data):
    # An aggregate in boolean position, with no comparison around it: a group
    # survives when its value is neither zero nor blank.
    sql = "SELECT city, COUNT(*) AS c FROM people GROUP BY city HAVING COUNT(*)"
    assert len(rows_of(sql, data)) == 3


def test_having_may_not_reference_a_non_group_column(data):
    with pytest.raises(QueryError, match="only reference group columns or aggregates"):
        run("SELECT city FROM people GROUP BY city HAVING age > 1", data)


def test_having_over_arithmetic_on_a_group_column(tmp_path):
    (tmp_path / "t.csv").write_text("g,v\n1,a\n2,b\n3,c\n", encoding="utf-8")
    sql = "SELECT g, COUNT(*) AS c FROM t GROUP BY g HAVING -g < -1"
    assert [r["g"] for r in rows_of(sql, str(tmp_path))] == ["2", "3"]


def test_computed_column_over_aggregates(data):
    sql = "SELECT city, SUM(age) / COUNT(*) AS mean FROM people GROUP BY city"
    got = {r["city"]: r["mean"] for r in rows_of(sql, data)}
    assert got["austin"] == "54"
    assert got["london"] == "38.5"


def test_aggregate_in_a_computed_column_name(data):
    cols, _ = run("SELECT COUNT(*) + 1 FROM people", data)
    assert cols == ["count + 1"]


# ── aggregate edge cases ─────────────────────────────────────────────────────

def test_select_star_cannot_be_combined_with_aggregation(data):
    with pytest.raises(QueryError, match=r"SELECT \* cannot be combined"):
        run("SELECT * FROM people GROUP BY city", data)


def test_aggregate_over_an_empty_relation_yields_one_row(data):
    _, rows = run("SELECT COUNT(*) AS c, SUM(age) AS s FROM people WHERE age > 999", data)
    assert rows == [{"c": "0", "s": "0"}]


def test_min_max_avg_over_only_blank_values_are_blank(tmp_path):
    (tmp_path / "t.csv").write_text("a\n\n\n", encoding="utf-8")
    _, rows = run("SELECT MIN(a) AS m, MAX(a) AS x, AVG(a) AS v FROM t", str(tmp_path))
    assert rows == [{"m": "", "x": "", "v": ""}]


def test_aggregate_order_by_must_be_a_selected_output(data):
    with pytest.raises(QueryError, match="not a selected output column"):
        run("SELECT city, COUNT(*) AS c FROM people GROUP BY city ORDER BY SUM(age)", data)


# ── parser and lexer error paths ─────────────────────────────────────────────

def test_limit_must_be_a_whole_number(data):
    with pytest.raises(ParseError, match="LIMIT must be a whole number"):
        run("SELECT name FROM people LIMIT 2.5", data)


def test_from_needs_a_table_name(data):
    with pytest.raises(ParseError, match="expected a table name after FROM"):
        run("SELECT name FROM 5", data)


def test_stray_bang_is_a_lex_error(data):
    with pytest.raises(LexError, match="stray '!'"):
        run("SELECT name FROM people WHERE name ! 'ada'", data)


# ── a predicate used where a value is expected ───────────────────────────────

def test_boolean_expression_in_the_select_list(data):
    # `(age > 40)` in a value position evaluates to a boolean, which the
    # projection formats as a number, so it prints as 0 or 1 rather than as
    # Python's False and True.
    _, rows = run("SELECT name, (age > 40) AS old FROM people", data)
    got = {r["name"]: r["old"] for r in rows}
    assert got["ada"] == "0"
    assert got["grace"] == "1"


def test_bare_text_column_in_where_is_truthy_when_non_empty(tmp_path):
    (tmp_path / "t.csv").write_text("a,b\nx,1\n,2\ny,3\n", encoding="utf-8")
    assert [r["b"] for r in rows_of("SELECT b FROM t WHERE a", str(tmp_path))] == ["1", "3"]


def test_bare_numeric_column_in_where_is_truthy_when_non_zero(tmp_path):
    (tmp_path / "t.csv").write_text("a,b\n0,1\n5,2\n0.0,3\n", encoding="utf-8")
    assert [r["b"] for r in rows_of("SELECT b FROM t WHERE a", str(tmp_path))] == ["2"]


# ── one-shot command line ────────────────────────────────────────────────────
# The REPL is covered above; this is the other entry point, the one the
# `quarry` console script installs.

def test_cli_runs_one_query_and_returns_zero(data, monkeypatch, capsys):
    from quarry.__main__ import main
    monkeypatch.chdir(data)
    assert main(["SELECT name FROM people WHERE age > 50"]) == 0
    out = capsys.readouterr().out
    assert "edsger" in out
    assert "(1 row)" in out


def test_cli_reports_a_bad_query_on_stderr_and_returns_one(data, monkeypatch, capsys):
    from quarry.__main__ import main
    monkeypatch.chdir(data)
    assert main(["SELECT nope FROM people"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: unknown column 'nope'" in captured.err


def test_cli_with_no_arguments_starts_the_repl(data, monkeypatch, capsys):
    from quarry import __main__ as m
    monkeypatch.chdir(data)
    monkeypatch.setattr("sys.stdin", io.StringIO(".exit\n"))
    monkeypatch.setattr("builtins.input", lambda _prompt: ".exit")
    assert m.main([]) == 0
    assert "quarry." in capsys.readouterr().out


# ── remaining error and formatting paths ─────────────────────────────────────

def test_qualified_reference_to_an_unknown_column_on_a_known_table(data):
    with pytest.raises(QueryError, match="unknown column 'people.nope'"):
        run("SELECT people.nope FROM people", data)


def test_unterminated_string_is_a_lex_error(data):
    with pytest.raises(LexError, match="unterminated string"):
        run("SELECT name FROM people WHERE city = 'london", data)


def test_predicate_as_a_select_item_is_named_from_its_node(data):
    # A comparison is not part of the arithmetic renderer's grammar, so its
    # default output name falls back to the node's own text form.
    cols, _ = run("SELECT (age > 40) FROM people", data)
    assert len(cols) == 1
    assert "Compare" in cols[0]


def test_predicate_over_aggregates_in_the_select_list(data):
    # A comparison in a value position inside an aggregate query is evaluated
    # against the grouped row, not a raw one.
    sql = "SELECT city, (COUNT(*) > 1) AS many FROM people GROUP BY city"
    got = {r["city"]: r["many"] for r in rows_of(sql, data)}
    assert got == {"london": "1", "new york": "0", "austin": "0"}


def test_order_by_an_alias_over_a_computed_expression(data):
    # The alias names an arithmetic expression rather than a plain column, so
    # ORDER BY has to evaluate it per row instead of reading a stored value.
    sql = "SELECT name, 100 - age AS remaining FROM people ORDER BY remaining"
    assert [r["name"] for r in rows_of(sql, data)] == ["edsger", "grace", "alan", "ada"]


def test_order_by_an_alias_that_shadows_a_different_column(data):
    # `age AS name` makes the output column `name` while the input column
    # `name` still exists. ORDER BY name sorts by what the alias renamed, age.
    sql = "SELECT age AS name FROM people ORDER BY name"
    assert [r["name"] for r in rows_of(sql, data)] == ["36", "41", "42", "54"]


def test_order_by_an_alias_equal_to_the_column_it_renames(data):
    # The self-referential case. This used to recurse until the interpreter
    # stack ran out and escaped as a bare RecursionError.
    sql = "SELECT age AS age FROM people ORDER BY age DESC"
    assert [r["age"] for r in rows_of(sql, data)] == ["54", "42", "41", "36"]
