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

from .parser import Aggregate, And, Column, Compare, Literal, Not, Or, Query


class QueryError(ValueError):
    """A query that parses but cannot run, like an unknown column or a missing file."""


def execute(query: Query, base_dir: str = ".") -> tuple[list[str], list[dict[str, str]]]:
    path = _resolve(query.table, base_dir)
    if not os.path.isfile(path):
        raise QueryError(f"no such table: {path}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []

        rows = iter(reader)
        if query.where is not None:
            rows = (r for r in rows if _truth(query.where, r, fields))

        materialized = list(rows)  # sort, limit and grouping need the whole relation

        if _is_aggregation(query):
            return _aggregate(query, materialized, fields)

        out_cols = list(fields) if query.columns == ["*"] else query.columns
        for c in out_cols:
            _require_column(c, fields)

        if query.order_by is not None:
            col, descending = query.order_by
            _require_column(col, fields)
            materialized = _sorted_rows(materialized, col, descending)

        if query.limit is not None:
            materialized = materialized[: query.limit]

        projected = [{c: r[c] for c in out_cols} for r in materialized]
        return out_cols, projected


# ── Aggregation ──────────────────────────────────────────────────────────────
def _is_aggregation(query: Query) -> bool:
    return (
        query.group_by is not None
        or query.having is not None
        or any(isinstance(c, Aggregate) for c in query.columns)
    )


def _aggregate(query: Query, rows, fields):
    group_cols = query.group_by or []
    for c in group_cols:
        _require_column(c, fields)

    # A plain column in the SELECT list of an aggregate query must be a group
    # column, the usual SQL rule; anything else has no single value per group.
    for item in query.columns:
        if isinstance(item, str):
            if item == "*":
                raise QueryError("SELECT * cannot be combined with aggregation")
            if item not in group_cols:
                raise QueryError(
                    f"column {item!r} must appear in GROUP BY or an aggregate"
                )

    # Every aggregate that has to be computed: those projected, plus any that
    # only HAVING references.
    aggs: list[Aggregate] = [c for c in query.columns if isinstance(c, Aggregate)]
    seen = {a.name for a in aggs}
    for a in _aggregates_in(query.having):
        if a.name not in seen:
            aggs.append(a)
            seen.add(a.name)
    for a in aggs:
        if a.arg != "*":
            _require_column(a.arg, fields)

    # Group the rows. One group per distinct tuple of group-column values, in
    # first-seen order. With no GROUP BY the whole relation is a single group.
    groups: dict[tuple, list] = {}
    for r in rows:
        key = tuple(r[c] for c in group_cols)
        groups.setdefault(key, []).append(r)
    if not group_cols and not groups:
        groups[()] = []  # aggregates over an empty relation still yield one row

    out_cols = [item if isinstance(item, str) else item.name for item in query.columns]

    result_rows = []
    for key, members in groups.items():
        group_row = dict(zip(group_cols, key))
        for a in aggs:
            group_row[a.name] = _compute(a, members)
        if query.having is not None and not _group_truth(query.having, group_row):
            continue
        result_rows.append(group_row)

    if query.order_by is not None:
        col, descending = query.order_by
        if col not in out_cols and not any(a.name == col for a in aggs):
            _require_column(col, group_cols)
        result_rows = _sorted_rows(result_rows, col, descending)

    if query.limit is not None:
        result_rows = result_rows[: query.limit]

    projected = [{c: r[c] for c in out_cols} for r in result_rows]
    return out_cols, projected


def _aggregates_in(node) -> list:
    if isinstance(node, Aggregate):
        return [node]
    if isinstance(node, (And, Or)):
        return _aggregates_in(node.left) + _aggregates_in(node.right)
    if isinstance(node, Not):
        return _aggregates_in(node.operand)
    if isinstance(node, Compare):
        return _aggregates_in(node.left) + _aggregates_in(node.right)
    return []


def _compute(agg: Aggregate, members: list) -> str:
    if agg.func == "count":
        if agg.arg == "*":
            return str(len(members))
        # COUNT(col) ignores empty and missing values.
        return str(sum(1 for r in members if r.get(agg.arg, "") != ""))

    values = [r[agg.arg] for r in members if r.get(agg.arg, "") != ""]

    if agg.func in ("sum", "avg"):
        nums = [f for f in (_as_float(v) for v in values) if f is not None]
        if agg.func == "sum":
            return _fmt_num(sum(nums))
        if not nums:
            return ""  # AVG over no numeric values has no defined mean
        return _fmt_num(sum(nums) / len(nums))

    # MIN / MAX: numeric when every present value is numeric, else lexical.
    if not values:
        return ""
    numeric = all(_as_float(v) is not None for v in values)
    if numeric:
        chosen = (min if agg.func == "min" else max)(values, key=_as_float)
        return chosen
    return (min if agg.func == "min" else max)(values)


def _fmt_num(x: float) -> str:
    f = float(x)
    return str(int(f)) if f.is_integer() else repr(f)


def _group_truth(node, group_row: dict) -> bool:
    if isinstance(node, And):
        return _group_truth(node.left, group_row) and _group_truth(node.right, group_row)
    if isinstance(node, Or):
        return _group_truth(node.left, group_row) or _group_truth(node.right, group_row)
    if isinstance(node, Not):
        return not _group_truth(node.operand, group_row)
    if isinstance(node, Compare):
        return _compare(node.op, _group_value(node.left, group_row), _group_value(node.right, group_row))
    v = _group_value(node, group_row)
    f = _as_float(v)
    return f != 0.0 if f is not None else bool(v)


def _group_value(node, group_row: dict):
    if isinstance(node, Aggregate):
        return group_row[node.name]
    if isinstance(node, Column):
        if node.name not in group_row:
            raise QueryError(
                f"HAVING may only reference group columns or aggregates, not {node.name!r}"
            )
        return group_row[node.name]
    if isinstance(node, Literal):
        return node.value
    return _group_truth(node, group_row)


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


def _sorted_rows(rows: list[dict[str, str]], col: str, descending: bool) -> list[dict[str, str]]:
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
