import pytest

from quarry import ParseError, QueryError, run

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
    assert cols == ["people.id", "people.name", "people.city",
                    "orders.id", "orders.person", "orders.amount"]


def test_join_bare_unambiguous_column_resolves(joined):
    # name is only on people, amount only on orders, so bare names resolve.
    sql = "SELECT name, amount FROM people JOIN orders ON people.id = orders.person"
    _, rows = run(sql, joined)
    assert {(r["name"], r["amount"]) for r in rows} == {("ada", "50"), ("ada", "20"), ("alan", "99")}


def test_join_bare_ambiguous_column_is_a_query_error(joined):
    # id lives on both sides; a bare reference must be rejected.
    sql = "SELECT id FROM people JOIN orders ON people.id = orders.person"
    with pytest.raises(QueryError):
        run(sql, joined)


def test_join_with_where_and_order_by(joined):
    sql = ("SELECT people.name, orders.amount FROM people JOIN orders "
           "ON people.id = orders.person WHERE orders.amount > 10 "
           "ORDER BY orders.amount DESC")
    _, rows = run(sql, joined)
    assert [(r["people.name"], r["orders.amount"]) for r in rows] == [
        ("alan", "99"), ("ada", "50"), ("ada", "20")]


def test_join_aggregate_group_by(joined):
    sql = ("SELECT people.name, SUM(orders.amount) FROM people JOIN orders "
           "ON people.id = orders.person GROUP BY people.name")
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


def test_quoted_string_with_embedded_quote(tmp_path):
    # The stored value has one apostrophe; the SQL literal 'o''brien' must decode
    # its doubled quote to that single one before the comparison can match.
    (tmp_path / "q.csv").write_text("label\no'brien\nsmith\n", encoding="utf-8")
    rows = rows_of("SELECT label FROM q WHERE label = 'o''brien'", str(tmp_path))
    assert [r["label"] for r in rows] == ["o'brien"]
