"""Execute a parsed query against a CSV file.

The model is the classic volcano one: a query is a pipeline of operators, each
pulling rows from the one below it. Here that is a scan over the CSV, an optional
filter, an optional sort, an optional limit, and a final projection. Filter and
limit stream row by row; sort is the one blocking step, because you cannot know
the first ordered row until you have seen the last input row.

Every CSV value is a string. Comparisons coerce a pair to numbers when both look
numeric and fall back to string order otherwise, so `age > 30` does the numeric
thing while `name = 'ada'` does the lexical one, without a schema to declare.
"""

from __future__ import annotations

import csv
import os

from .parser import And, Column, Compare, Literal, Not, Or, Query


class QueryError(ValueError):
    """A query that parses but cannot run — an unknown column, a missing file."""


def execute(query: Query, base_dir: str = ".") -> tuple[list[str], list[dict[str, str]]]:
    path = _resolve(query.table, base_dir)
    if not os.path.isfile(path):
        raise QueryError(f"no such table: {path}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        out_cols = list(fields) if query.columns == ["*"] else query.columns
        for c in out_cols:
            _require_column(c, fields)

        rows = iter(reader)
        if query.where is not None:
            rows = (r for r in rows if _truth(query.where, r, fields))

        materialized = list(rows)  # sort and limit need the whole relation

        if query.order_by is not None:
            col, descending = query.order_by
            _require_column(col, fields)
            materialized = _sorted(materialized, col, descending)

        if query.limit is not None:
            materialized = materialized[: query.limit]

        projected = [{c: r[c] for c in out_cols} for r in materialized]
        return out_cols, projected


# ── Resolution and validation ────────────────────────────────────────────────
def _resolve(table: str, base_dir: str) -> str:
    looks_like_path = table.endswith(".csv") or os.sep in table or "/" in table
    name = table if looks_like_path else table + ".csv"
    return name if os.path.isabs(name) else os.path.join(base_dir, name)


def _require_column(name: str, fields: list[str]) -> None:
    if name not in fields:
        raise QueryError(f"unknown column {name!r}; available: {', '.join(fields)}")


# ── Expression evaluation ────────────────────────────────────────────────────
def _truth(node, row: dict[str, str], fields: list[str]) -> bool:
    if isinstance(node, And):
        return _truth(node.left, row, fields) and _truth(node.right, row, fields)
    if isinstance(node, Or):
        return _truth(node.left, row, fields) or _truth(node.right, row, fields)
    if isinstance(node, Not):
        return not _truth(node.operand, row, fields)
    if isinstance(node, Compare):
        return _compare(node.op, _value(node.left, row, fields), _value(node.right, row, fields))
    # A bare column or literal in boolean position: truthy if non-zero / non-empty.
    v = _value(node, row, fields)
    f = _as_float(v)
    return f != 0.0 if f is not None else bool(v)


def _value(node, row: dict[str, str], fields: list[str]):
    if isinstance(node, Column):
        _require_column(node.name, fields)
        return row[node.name]
    if isinstance(node, Literal):
        return node.value
    return _truth(node, row, fields)  # a parenthesised boolean used as a value


def _compare(op: str, a, b) -> bool:
    fa, fb = _as_float(a), _as_float(b)
    if fa is not None and fb is not None:
        a, b = fa, fb
    else:
        a, b = str(a), str(b)
    match op:
        case "=":
            return a == b
        case "!=":
            return a != b
        case "<":
            return a < b
        case "<=":
            return a <= b
        case ">":
            return a > b
        case ">=":
            return a >= b
    raise QueryError(f"unknown operator {op!r}")  # unreachable via the parser


def _sorted(rows: list[dict[str, str]], col: str, descending: bool) -> list[dict[str, str]]:
    numeric = all(_as_float(r[col]) is not None for r in rows)
    key = (lambda r: _as_float(r[col])) if numeric else (lambda r: r[col])
    return sorted(rows, key=key, reverse=descending)


def _as_float(x):
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
