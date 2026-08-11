"""Execute a parsed query against one or two CSV files.

The model is the classic volcano one: a query is a pipeline of operators, each
pulling rows from the one below it. Here that is a scan over the CSV, an optional
hash join with a second CSV, an optional filter, an optional sort, an optional
limit, and a final projection. Filter and limit stream row by row; sort and the
join build side are the blocking steps, because you cannot know the first
ordered row until you have seen the last input row.

Every CSV value is a string. Comparisons coerce a pair to numbers when both look
numeric and fall back to string order otherwise, so `age > 30` does the numeric
thing while `name = 'ada'` does the lexical one, without a schema to declare.

A column is named bare (`age`) or qualified (`people.age`). With a single table
either form resolves against it. With a join a bare name resolves only when one
side has it; a name both sides carry is ambiguous and must be qualified.
"""

from __future__ import annotations

import csv
import os

from .parser import Aggregate, Alias, And, Column, Compare, Literal, Not, Or, Query


class QueryError(ValueError):
    """A query that parses but cannot run, like an unknown column or a missing file."""


# ── Schema and column resolution ─────────────────────────────────────────────
class Schema:
    """The set of tables in play and the rules for naming a column across them.

    With one table, a row is keyed by the bare CSV field names. With a join, a
    row is keyed by qualified `table.column` names, so the two sides never
    collide. `resolve` turns a written reference into the key a row actually
    uses, and is where unknown and ambiguous columns are caught.
    """

    def __init__(self, tables: list[tuple[str, list[str]]]):
        self.tables = tables               # list of (alias, fields), in FROM order
        self.joined = len(tables) > 1
        self._owners: dict[str, list[str]] = {}
        for alias, fields in tables:
            for c in fields:
                self._owners.setdefault(c, []).append(alias)

    def key(self, alias: str, col: str) -> str:
        return f"{alias}.{col}" if self.joined else col

    def all_keys(self) -> list[str]:
        return [self.key(a, c) for a, fields in self.tables for c in fields]

    def resolve(self, ref: str) -> str:
        table, dot, name = ref.partition(".")
        if dot:
            match = [fields for a, fields in self.tables if a == table]
            if not match:
                raise QueryError(f"unknown table {table!r} in {ref!r}")
            if name not in match[0]:
                raise QueryError(
                    f"unknown column {ref!r}; available: {', '.join(self.all_keys())}"
                )
            return self.key(table, name)

        owners = self._owners.get(ref, [])
        if not owners:
            raise QueryError(
                f"unknown column {ref!r}; available: {', '.join(self.all_keys())}"
            )
        if len(owners) > 1:
            qualified = " or ".join(f"{a}.{ref}" for a in owners)
            raise QueryError(f"column {ref!r} is ambiguous; qualify it as {qualified}")
        return self.key(owners[0], ref)


# ── Select item helpers ──────────────────────────────────────────────────────
def _item_expr(item):
    # The underlying thing a select item computes: a column reference (str) or
    # an Aggregate. AS only renames the output, it does not change the value.
    return item.item if isinstance(item, Alias) else item


def _item_name(item) -> str:
    if isinstance(item, Alias):
        return item.name
    if isinstance(item, Aggregate):
        return item.name
    return item  # a column reference string, bare or qualified


# ── Entry point ──────────────────────────────────────────────────────────────
def execute(query: Query, base_dir: str = ".") -> tuple[list[str], list[dict[str, str]]]:
    schema, rows = _build_source(query, base_dir)

    if query.where is not None:
        rows = [r for r in rows if _truth(query.where, r, schema)]

    if _is_aggregation(query):
        out_cols, result = _aggregate(query, rows, schema)
    else:
        out_cols, result = _project(query, rows, schema)

    if query.distinct:
        result = _dedup(result, out_cols)
    if query.limit is not None:
        result = result[: query.limit]
    return out_cols, result


# ── Scan and join ────────────────────────────────────────────────────────────
def _load(token: str, base_dir: str) -> tuple[str, list[str], list[dict[str, str]]]:
    path = _resolve(token, base_dir)
    if not os.path.isfile(path):
        raise QueryError(f"no such table: {path}")
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        return _alias(token), fields, list(reader)


def _build_source(query: Query, base_dir: str):
    la, lf, lrows = _load(query.table, base_dir)
    if query.join is None:
        return Schema([(la, lf)]), lrows

    ra, rf, rrows = _load(query.join.table, base_dir)
    if la == ra:
        raise QueryError(
            f"join needs two differently named tables; both sides are {la!r}"
        )
    schema = Schema([(la, lf), (ra, rf)])

    s1 = _join_side(query.join.left, la, lf, ra, rf)
    s2 = _join_side(query.join.right, la, lf, ra, rf)
    if {s1[0], s2[0]} != {"L", "R"}:
        raise QueryError("JOIN ON must compare a column from each table")
    lcol = s1[1] if s1[0] == "L" else s2[1]
    rcol = s1[1] if s1[0] == "R" else s2[1]

    # Hash join: bucket the right side by its key, then probe once per left row.
    buckets: dict[object, list[dict[str, str]]] = {}
    for rr in rrows:
        buckets.setdefault(_norm(rr[rcol]), []).append(rr)

    joined: list[dict[str, str]] = []
    for lr in lrows:
        for rr in buckets.get(_norm(lr[lcol]), []):
            row = {f"{la}.{c}": lr[c] for c in lf}
            row.update({f"{ra}.{c}": rr[c] for c in rf})
            joined.append(row)
    return schema, joined


def _join_side(col: Column, la, lf, ra, rf) -> tuple[str, str]:
    # Which table an ON column belongs to, and its bare name in that CSV.
    if col.table is not None:
        if col.table == la:
            if col.name not in lf:
                raise QueryError(f"unknown column {col.ref!r}")
            return "L", col.name
        if col.table == ra:
            if col.name not in rf:
                raise QueryError(f"unknown column {col.ref!r}")
            return "R", col.name
        raise QueryError(f"unknown table {col.table!r} in {col.ref!r}")

    in_l, in_r = col.name in lf, col.name in rf
    if in_l and in_r:
        raise QueryError(
            f"column {col.name!r} is ambiguous in JOIN ON; qualify it as "
            f"{la}.{col.name} or {ra}.{col.name}"
        )
    if in_l:
        return "L", col.name
    if in_r:
        return "R", col.name
    raise QueryError(f"unknown column {col.name!r} in JOIN ON")


def _norm(v):
    # A join key that matches the way `=` compares: numeric when the value looks
    # numeric, lexical otherwise, so "6" and "6.0" join but "6" and "abc" do not.
    f = _as_float(v)
    return ("n", f) if f is not None else ("s", str(v))


# ── Projection (no aggregation) ──────────────────────────────────────────────
def _project(query: Query, rows, schema: Schema):
    if query.columns == ["*"]:
        keys = schema.all_keys()
        getters = [(k, k) for k in keys]
        out_cols = keys
    else:
        getters = []
        out_cols = []
        for item in query.columns:
            expr = _item_expr(item)
            key = schema.resolve(expr)  # expr is a column reference string here
            out_cols.append(_item_name(item))
            getters.append((_item_name(item), key))

    if query.order_by is not None:
        col, descending = query.order_by
        rows = _sorted_rows(rows, _order_key(col, query, schema), descending)

    projected = [{name: r[key] for name, key in getters} for r in rows]
    return out_cols, projected


def _order_key(col: str, query: Query, schema: Schema) -> str:
    # ORDER BY may name an output alias, or a column (bare or qualified). An
    # alias maps back to whatever it renamed; anything else resolves directly.
    for item in query.columns:
        if isinstance(item, Alias) and item.name == col:
            return schema.resolve(_item_expr(item))
    return schema.resolve(col)


# ── Aggregation ──────────────────────────────────────────────────────────────
def _is_aggregation(query: Query) -> bool:
    return (
        query.group_by is not None
        or query.having is not None
        or any(isinstance(_item_expr(c), Aggregate) for c in query.columns)
    )


def _aggregate(query: Query, rows, schema: Schema):
    group_keys = [schema.resolve(r) for r in (query.group_by or [])]

    # A plain column in the SELECT list of an aggregate query must be a group
    # column, the usual SQL rule; anything else has no single value per group.
    for item in query.columns:
        expr = _item_expr(item)
        if isinstance(expr, str):
            if expr == "*":
                raise QueryError("SELECT * cannot be combined with aggregation")
            if schema.resolve(expr) not in group_keys:
                raise QueryError(
                    f"column {expr!r} must appear in GROUP BY or an aggregate"
                )

    # Every aggregate to compute: those projected, plus any only HAVING names.
    aggs: list[Aggregate] = [
        _item_expr(c) for c in query.columns if isinstance(_item_expr(c), Aggregate)
    ]
    seen = {a.name for a in aggs}
    for a in _aggregates_in(query.having):
        if a.name not in seen:
            aggs.append(a)
            seen.add(a.name)
    agg_keys = {a.name: (a.arg if a.arg == "*" else schema.resolve(a.arg)) for a in aggs}

    # Group the rows. One group per distinct tuple of group-column values, in
    # first-seen order. With no GROUP BY the whole relation is a single group.
    groups: dict[tuple, list] = {}
    for r in rows:
        key = tuple(r[k] for k in group_keys)
        groups.setdefault(key, []).append(r)
    if not group_keys and not groups:
        groups[()] = []  # aggregates over an empty relation still yield one row

    out_cols = [_item_name(item) for item in query.columns]

    result_rows = []
    for key, members in groups.items():
        group_row = dict(zip(group_keys, key))
        for a in aggs:
            group_row[a.name] = _compute(a, members, agg_keys[a.name])
        if query.having is not None and not _group_truth(query.having, group_row, schema):
            continue
        result_rows.append(_project_group(query.columns, group_row, schema))

    if query.order_by is not None:
        col, descending = query.order_by
        if col not in out_cols:
            raise QueryError(f"ORDER BY {col!r} is not a selected output column")
        result_rows = _sorted_rows(result_rows, col, descending)

    return out_cols, result_rows


def _project_group(columns, group_row, schema: Schema) -> dict:
    out = {}
    for item in columns:
        expr = _item_expr(item)
        if isinstance(expr, Aggregate):
            out[_item_name(item)] = group_row[expr.name]
        else:
            out[_item_name(item)] = group_row[schema.resolve(expr)]
    return out


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


def _compute(agg: Aggregate, members: list, key: str) -> str:
    if agg.func == "count":
        if agg.arg == "*":
            return str(len(members))
        # COUNT(col) ignores empty and missing values.
        return str(sum(1 for r in members if r.get(key, "") != ""))

    values = [r[key] for r in members if r.get(key, "") != ""]

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
        return (min if agg.func == "min" else max)(values, key=_as_float)
    return (min if agg.func == "min" else max)(values)


def _fmt_num(x: float) -> str:
    f = float(x)
    return str(int(f)) if f.is_integer() else repr(f)


def _group_truth(node, group_row: dict, schema: Schema) -> bool:
    if isinstance(node, And):
        return _group_truth(node.left, group_row, schema) and _group_truth(node.right, group_row, schema)
    if isinstance(node, Or):
        return _group_truth(node.left, group_row, schema) or _group_truth(node.right, group_row, schema)
    if isinstance(node, Not):
        return not _group_truth(node.operand, group_row, schema)
    if isinstance(node, Compare):
        return _compare(
            node.op,
            _group_value(node.left, group_row, schema),
            _group_value(node.right, group_row, schema),
        )
    v = _group_value(node, group_row, schema)
    f = _as_float(v)
    return f != 0.0 if f is not None else bool(v)


def _group_value(node, group_row: dict, schema: Schema):
    if isinstance(node, Aggregate):
        return group_row[node.name]
    if isinstance(node, Column):
        key = schema.resolve(node.ref)
        if key not in group_row:
            raise QueryError(
                f"HAVING may only reference group columns or aggregates, not {node.ref!r}"
            )
        return group_row[key]
    if isinstance(node, Literal):
        return node.value
    return _group_truth(node, group_row, schema)


# ── Resolution and validation ────────────────────────────────────────────────
def _resolve(table: str, base_dir: str) -> str:
    looks_like_path = table.endswith(".csv") or os.sep in table or "/" in table
    name = table if looks_like_path else table + ".csv"
    return name if os.path.isabs(name) else os.path.join(base_dir, name)


def _alias(token: str) -> str:
    # The name a qualified column uses: the file's stem, so FROM orders.csv and
    # FROM orders both qualify as orders.column.
    return os.path.splitext(os.path.basename(token))[0]


def _dedup(rows: list[dict], cols: list[str]) -> list[dict]:
    seen: set = set()
    out = []
    for r in rows:
        key = tuple(r[c] for c in cols)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ── Expression evaluation ────────────────────────────────────────────────────
def _truth(node, row: dict[str, str], schema: Schema) -> bool:
    if isinstance(node, And):
        return _truth(node.left, row, schema) and _truth(node.right, row, schema)
    if isinstance(node, Or):
        return _truth(node.left, row, schema) or _truth(node.right, row, schema)
    if isinstance(node, Not):
        return not _truth(node.operand, row, schema)
    if isinstance(node, Compare):
        return _compare(node.op, _value(node.left, row, schema), _value(node.right, row, schema))
    # A bare column or literal in boolean position: truthy if non-zero / non-empty.
    v = _value(node, row, schema)
    f = _as_float(v)
    return f != 0.0 if f is not None else bool(v)


def _value(node, row: dict[str, str], schema: Schema):
    if isinstance(node, Column):
        return row[schema.resolve(node.ref)]
    if isinstance(node, Literal):
        return node.value
    return _truth(node, row, schema)  # a parenthesised boolean used as a value


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
