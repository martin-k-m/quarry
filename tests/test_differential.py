"""Differential test: quarry against SQLite, which is the oracle this suite
was missing.

Every other property test here asserts that quarry agrees with itself: LIMIT
returns at most n, a predicate and its negation partition the table, DISTINCT
is idempotent. All of those hold whether or not the answers are right. This
file asks a second engine the same question and compares the rows.

Two things are deliberately out of the comparison, because quarry and SQLite
model them differently on purpose rather than by mistake:

- Empty fields. quarry reads a blank CSV field as missing and every aggregate
  ignores it, which is SQL's NULL rule, but WHERE still compares it as the
  empty string, which is not. Generated data here has no blanks, so the two
  models coincide and a mismatch means a real disagreement about SQL.
- Mixed columns. quarry compares numerically when a value looks numeric and
  lexically otherwise, so a column holding both would diverge from SQLite by
  design. Each generated column is all numbers or all text.
"""

from __future__ import annotations

import csv
import random
import sqlite3

from quarry import run

NAMES = ["ana", "bo", "cy", "dee", "eli", "fay", "gus", "hal"]
CITIES = ["london", "austin", "paris", "tokyo"]
COUNTRIES = {"london": "uk", "austin": "us", "paris": "fr", "tokyo": "jp"}
COLS = ["name", "city", "age", "score"]
TYPES = {"name": "TEXT", "city": "TEXT", "age": "INTEGER", "score": "REAL"}
NUMERIC = {"age", "score"}

TRIALS = 300
SEED_BASE = 4000


def _rows(rng, n):
    return [
        {
            "name": rng.choice(NAMES),
            "city": rng.choice(CITIES),
            "age": rng.randint(1, 90),
            "score": round(rng.uniform(0, 100), 2),
        }
        for _ in range(n)
    ]


def _predicate(rng, depth=0, qualify=None):
    if depth < 2 and rng.random() < 0.35:
        op = rng.choice(["AND", "OR"])
        left, right = _predicate(rng, depth + 1, qualify), _predicate(rng, depth + 1, qualify)
        return f"({left} {op} {right})"
    if rng.random() < 0.15:
        return f"NOT ({_predicate(rng, depth + 1, qualify)})"
    col = rng.choice(COLS)
    bare = col
    if qualify:
        col = f"{qualify}.{col}"
    if bare in NUMERIC:
        lit = str(rng.randint(1, 90)) if bare == "age" else str(round(rng.uniform(0, 100), 2))
        return f"{col} {rng.choice(['=', '!=', '<', '<=', '>', '>='])} {lit}"
    return f"{col} {rng.choice(['=', '!='])} '{rng.choice(NAMES + CITIES)}'"


def _query(rng):
    if rng.random() < 0.2:
        where = f" WHERE {_predicate(rng, qualify='people')}" if rng.random() < 0.6 else ""
        cols = rng.sample(
            ["people.name", "people.age", "cities.city", "cities.country"],
            k=rng.randint(1, 3),
        )
        return (
            f"SELECT {', '.join(cols)} FROM people JOIN cities ON people.city = cities.city{where}"
        )

    where = f" WHERE {_predicate(rng)}" if rng.random() < 0.8 else ""
    if rng.random() < 0.4:
        group = rng.choice(["name", "city"])
        aggs = rng.sample(
            ["COUNT(*)", "SUM(age)", "AVG(age)", "MIN(age)", "MAX(age)", "MIN(name)", "MAX(name)"],
            k=rng.randint(1, 2),
        )
        having = f" HAVING COUNT(*) > {rng.randint(0, 3)}" if rng.random() < 0.3 else ""
        return f"SELECT {group}, {', '.join(aggs)} FROM people{where} GROUP BY {group}{having}"

    distinct = "DISTINCT " if rng.random() < 0.25 else ""
    if rng.random() < 0.2:
        cols = "age + 1, score * 2"
    else:
        cols = "*" if rng.random() < 0.3 else ", ".join(rng.sample(COLS, k=rng.randint(1, 4)))
        if distinct and cols == "*":
            cols = "city"
    return f"SELECT {distinct}{cols} FROM people{where}"


def _canon(v):
    # quarry returns strings and SQLite returns typed values, so both sides are
    # rendered the same way before comparing. 9 significant digits is wider than
    # any answer these queries produce and narrower than float noise from AVG.
    if isinstance(v, bool):
        return str(int(v))
    s = str(v)
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f.is_integer() else f"{f:.9g}"


def _multiset(rows):
    # ORDER BY is not generated, so row order carries no meaning and comparing
    # as a multiset avoids failing on a tie the two engines break differently.
    return sorted(tuple(_canon(v) for v in row) for row in rows)


def test_quarry_agrees_with_sqlite(tmp_path):
    compared = 0
    for t in range(TRIALS):
        rng = random.Random(SEED_BASE + t)
        d = tmp_path / f"t{t}"
        d.mkdir()
        rows = _rows(rng, rng.randint(1, 25))
        with (d / "people.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
        with (d / "cities.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["city", "country"])
            w.writeheader()
            for city, country in COUNTRIES.items():
                w.writerow({"city": city, "country": country})

        db = sqlite3.connect(":memory:")
        spec = ", ".join(f"{c} {TYPES[c]}" for c in COLS)
        db.execute(f"CREATE TABLE people ({spec})")
        db.executemany(
            "INSERT INTO people VALUES (?, ?, ?, ?)", [tuple(r[c] for c in COLS) for r in rows]
        )
        db.execute("CREATE TABLE cities (city TEXT, country TEXT)")
        db.executemany("INSERT INTO cities VALUES (?, ?)", list(COUNTRIES.items()))

        sql = _query(rng)
        cols_q, rows_q = run(sql, str(d))
        got = _multiset([[r[c] for c in cols_q] for r in rows_q])
        want = _multiset(db.execute(sql).fetchall())
        compared += 1
        assert got == want, f"seed {SEED_BASE + t}\nsql: {sql}\nquarry: {got}\nsqlite: {want}"

    # A generator that silently stopped producing queries would leave every
    # assertion above unreached and the test still green.
    assert compared == TRIALS
