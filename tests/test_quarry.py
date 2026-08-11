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


def test_quoted_string_with_embedded_quote(tmp_path):
    # The stored value has one apostrophe; the SQL literal 'o''brien' must decode
    # its doubled quote to that single one before the comparison can match.
    (tmp_path / "q.csv").write_text("label\no'brien\nsmith\n", encoding="utf-8")
    rows = rows_of("SELECT label FROM q WHERE label = 'o''brien'", str(tmp_path))
    assert [r["label"] for r in rows] == ["o'brien"]
