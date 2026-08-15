"""Turn a token stream into a query tree.

A hand-written recursive-descent parser for the subset:

    SELECT <* | col, ...> FROM <table> [WHERE <expr>]
    [ORDER BY <col> [ASC|DESC]] [LIMIT <n>]

    SELECT <* | item, ...> FROM <table> [WHERE <expr>]
    [GROUP BY <col, ...>] [HAVING <expr>]
    [ORDER BY <col | agg> [ASC|DESC]] [LIMIT <n>]

A select item is a column name or an aggregate call: COUNT(*), COUNT(col),
SUM(col), AVG(col), MIN(col), MAX(col).

The expression grammar has the usual precedence, lowest first: OR, AND, NOT,
then a single comparison (or an IN / NOT IN membership test against a literal
list), then arithmetic (additive below multiplicative below unary minus), then a
primary (a column, an aggregate call, a scalar function call, a literal, or a
parenthesised expression). A scalar function is any name followed by "(" that is
not one of the fixed aggregates. Recursive descent mirrors that grammar one
function per rule, which is why the shape of the code is the shape of the language.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from .lexer import Kind, Token, tokenize

# The deepest an expression tree may nest. A pathologically nested expression
# (deep parentheses, a long `a + a + ...` chain, stacked NOTs) would otherwise
# exhaust the interpreter stack and raise a bare RecursionError instead of a
# clean, explainable error.
#
# The binding case is parentheses: each level re-enters the full precedence
# ladder (_or -> _and -> _not -> _comparison -> _additive -> _multiplicative ->
# _unary -> _primary), roughly eight Python frames per level. At 100 that peaks
# near 800 frames, a safe margin under CPython's ~1000-frame default, while the
# downstream tree-walkers (one to three frames per level) stay well clear at the
# same depth. Real queries nest a handful of levels, so 100 only ever rejects
# the adversarial case.
_MAX_EXPR_DEPTH = 100


# ── Expression nodes ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Column:
    name: str
    table: str | None = None   # the qualifier in table.column, or None if bare

    @property
    def ref(self) -> str:
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass(frozen=True)
class Literal:
    value: object      # float for a number, str for a quoted string
    is_string: bool


@dataclass(frozen=True)
class Compare:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class And:
    left: object
    right: object


@dataclass(frozen=True)
class Or:
    left: object
    right: object


@dataclass(frozen=True)
class Not:
    operand: object


@dataclass(frozen=True)
class BinOp:
    # An arithmetic node: op is one of + - * /, over numeric operands.
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class Neg:
    # Unary minus, as in -age or -(a + b).
    operand: object


@dataclass(frozen=True)
class Func:
    # A scalar function call, name(arg, ...). The name is any identifier that is
    # not one of the fixed aggregates; the engine knows the set it can run.
    name: str          # lowercased: upper, lower, length, round, abs, coalesce
    args: tuple        # a tuple of argument expression nodes


@dataclass(frozen=True)
class Like:
    # Pattern match: value LIKE pattern, or value NOT LIKE pattern when negated.
    # The pattern is an expression, usually a string literal with % and _ wildcards.
    value: object
    pattern: object
    negated: bool = False


@dataclass(frozen=True)
class Between:
    # Range test: value BETWEEN low AND high, inclusive on both ends, or the
    # negation when negated. The bounds are expressions.
    value: object
    low: object
    high: object
    negated: bool = False


@dataclass(frozen=True)
class In:
    # Membership: value IN (items), or value NOT IN (items) when negated. The
    # items are a literal list, not a subquery.
    value: object
    items: tuple       # a tuple of expression nodes, usually literals
    negated: bool = False


# Precedence of the arithmetic operators, higher binds tighter. Used only to
# render a computed column back to readable text for its default name.
_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def render_expr(node) -> str:
    """A readable text form of an expression, used to name a computed column."""
    if isinstance(node, Column):
        return node.ref
    if isinstance(node, Aggregate):
        return node.name
    if isinstance(node, Literal):
        if node.is_string:
            return "'" + str(node.value).replace("'", "''") + "'"
        f = float(node.value)
        return str(int(f)) if f.is_integer() else repr(f)
    if isinstance(node, Neg):
        # Unary minus binds tighter than any binary operator, so an arithmetic
        # operand always needs parens: -(a + b) must not render as -a + b.
        inner = render_expr(node.operand)
        return f"-({inner})" if isinstance(node.operand, BinOp) else f"-{inner}"
    if isinstance(node, Func):
        return f"{node.name.upper()}({', '.join(render_expr(a) for a in node.args)})"
    if isinstance(node, BinOp):
        left = _render_child(node.left, node.op, False)
        right = _render_child(node.right, node.op, True)
        return f"{left} {node.op} {right}"
    return str(node)


def _render_child(child, parent_op: str, is_right: bool) -> str:
    text = render_expr(child)
    if isinstance(child, BinOp):
        parent_prec = _PREC[parent_op]
        cp = _PREC[child.op]
        # Parenthesise a child that binds looser than its parent, or any child of
        # equal precedence sitting on the right of a left-associative operator
        # that is not associative: a - (b - c) and a - (b + c) both need the
        # parens, and so do a / (b * c) and a / (b / c). Whether they are needed
        # is a property of the *parent* operator, not the child's.
        if cp < parent_prec or (cp == parent_prec and is_right and parent_op in "-/"):
            return f"({text})"
    return text


AGGREGATES = {"count", "sum", "avg", "min", "max"}


@dataclass(frozen=True)
class Aggregate:
    func: str      # lowercased: count, sum, avg, min, max
    arg: str       # a column reference, or "*" for COUNT(*)

    @property
    def name(self) -> str:
        # The output column name. COUNT(*) is named "count"; every other
        # aggregate is named "func(col)", lowercased.
        if self.func == "count" and self.arg == "*":
            return "count"
        return f"{self.func}({self.arg})"


@dataclass(frozen=True)
class Alias:
    # A select item renamed with AS. The item is a column reference (str) or an
    # Aggregate; name is what the output column is called and what ORDER BY sees.
    item: object
    name: str


@dataclass(frozen=True)
class Join:
    table: str         # the raw table token of the right side
    left: Column       # one side of the ON equality
    right: Column      # the other side
    kind: str = "inner"   # "inner" or "left"


# ── The statement ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Query:
    columns: list[object]           # ["*"], or a list of str refs, Aggregate and Alias nodes
    table: str
    where: object | None
    group_by: list[str] | None      # explicit group columns, or None
    having: object | None           # a predicate over aggregates and group columns
    order_by: tuple[object, bool] | None   # (target, descending); target is an
                                            # output name str or an expression node
    limit: int | None
    join: Join | None = None        # the single INNER JOIN, or None
    distinct: bool = False          # SELECT DISTINCT


class ParseError(ValueError):
    """A token that does not fit the grammar."""


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
        self._depth = 0   # current expression nesting; guarded by _descend

    @contextmanager
    def _descend(self):
        """Count one level of expression nesting, rejecting a tree too deep to
        walk safely. Used at every point the expression grammar goes a level
        deeper: a parsed operator, a recursion into parentheses, a NOT, a unary
        minus, or a function argument. Because it decrements on exit, the count
        tracks the depth of the path currently being built, so it bounds the
        real tree depth regardless of whether the shape came from recursion or
        from a left-associative loop."""
        self._depth += 1
        if self._depth > _MAX_EXPR_DEPTH:
            raise ParseError(
                f"expression nested too deeply (limit {_MAX_EXPR_DEPTH})"
            )
        try:
            yield
        finally:
            self._depth -= 1

    # -- token helpers --
    def _peek(self) -> Token:
        return self.tokens[self.i]

    def _next(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _at_keyword(self, word: str) -> bool:
        tok = self._peek()
        return tok.kind is Kind.KEYWORD and tok.text.lower() == word

    def _eat_keyword(self, word: str) -> None:
        if not self._at_keyword(word):
            raise ParseError(f"expected {word.upper()} at column {self._peek().pos + 1}")
        self._next()

    def _expect(self, kind: Kind) -> Token:
        tok = self._peek()
        if tok.kind is not kind:
            raise ParseError(f"expected {kind.name} but found {tok.text!r} at column {tok.pos + 1}")
        return self._next()

    # -- statement --
    def parse(self) -> Query:
        self._eat_keyword("select")
        distinct = False
        if self._at_keyword("distinct"):
            self._next()
            distinct = True
        columns = self._columns()
        self._eat_keyword("from")
        table = self._table_name()

        join = None
        if self._at_keyword("inner") or self._at_keyword("left") or self._at_keyword("join"):
            kind = "inner"
            if self._at_keyword("inner"):
                self._next()
            elif self._at_keyword("left"):
                self._next()
                if self._at_keyword("outer"):
                    self._next()
                kind = "left"
            self._eat_keyword("join")
            right_table = self._table_name()
            self._eat_keyword("on")
            left_key = self._join_key()
            op = self._expect(Kind.OP).text
            if op != "=":
                raise ParseError("JOIN ON supports only equality (=)")
            right_key = self._join_key()
            join = Join(right_table, left_key, right_key, kind)

        where = None
        if self._at_keyword("where"):
            self._next()
            where = self._or()

        group_by = None
        if self._at_keyword("group"):
            self._next()
            self._eat_keyword("by")
            group_by = [self._column_ref()]
            while self._peek().kind is Kind.COMMA:
                self._next()
                group_by.append(self._column_ref())

        having = None
        if self._at_keyword("having"):
            self._next()
            having = self._or()

        order_by = None
        if self._at_keyword("order"):
            self._next()
            self._eat_keyword("by")
            col = self._order_target()
            descending = False
            if self._at_keyword("asc"):
                self._next()
            elif self._at_keyword("desc"):
                self._next()
                descending = True
            order_by = (col, descending)

        limit = None
        if self._at_keyword("limit"):
            self._next()
            tok = self._expect(Kind.NUMBER)
            if "." in tok.text:
                raise ParseError(f"LIMIT must be a whole number, got {tok.text}")
            limit = int(tok.text)

        self._expect(Kind.EOF)
        return Query(columns, table, where, group_by, having, order_by, limit, join, distinct)

    def _columns(self) -> list[object]:
        if self._peek().kind is Kind.STAR:
            self._next()
            return ["*"]
        items = [self._select_item()]
        while self._peek().kind is Kind.COMMA:
            self._next()
            items.append(self._select_item())
        return items

    def _select_item(self) -> object:
        item = self._item_from_node(self._expr())
        if self._at_keyword("as"):
            self._next()
            return Alias(item, self._expect(Kind.IDENT).text)
        return item

    def _item_from_node(self, node) -> object:
        # A select item stays a bare string for a plain column (so the output
        # column keeps its written name) and an Aggregate for a bare aggregate.
        # Anything with arithmetic in it is kept as an expression node, named
        # later from its rendered text.
        if isinstance(node, Column):
            return node.ref
        return node

    def _order_target(self) -> object:
        # ORDER BY may name a plain column, an aggregate output column, an output
        # alias, or an arithmetic expression. A plain column and an aggregate
        # resolve to their output name (a str); a computed expression is kept as
        # a node and evaluated per row.
        node = self._expr()
        if isinstance(node, Column):
            return node.ref
        if isinstance(node, Aggregate):
            return node.name
        return node

    def _column_ref(self) -> str:
        # A bare column name, or a qualified table.column. Returned as written,
        # so the engine resolves it against the right input.
        first = self._expect(Kind.IDENT).text
        if self._peek().kind is Kind.DOT:
            self._next()
            second = self._expect(Kind.IDENT).text
            return f"{first}.{second}"
        return first

    def _join_key(self) -> Column:
        ref = self._column_ref()
        table, _, name = ref.partition(".")
        return Column(name, table) if "." in ref else Column(ref)

    def _try_aggregate(self) -> Aggregate | None:
        # An aggregate is an identifier naming a known function, immediately
        # followed by "(". Anything else is left for the caller to read.
        tok = self._peek()
        if tok.kind is not Kind.IDENT or tok.text.lower() not in AGGREGATES:
            return None
        if self.tokens[self.i + 1].kind is not Kind.LPAREN:
            return None
        func = self._next().text.lower()
        self._next()  # the "("
        if func == "count" and self._peek().kind is Kind.STAR:
            self._next()
            arg = "*"
        else:
            arg = self._column_ref()
        self._expect(Kind.RPAREN)
        return Aggregate(func, arg)

    def _try_func(self) -> Func | None:
        # A scalar function is an identifier immediately followed by "(", where
        # the name is not one of the fixed aggregates (those are read first, by
        # _try_aggregate). The engine decides which names it can actually run.
        tok = self._peek()
        if tok.kind is not Kind.IDENT or tok.text.lower() in AGGREGATES:
            return None
        if self.tokens[self.i + 1].kind is not Kind.LPAREN:
            return None
        name = self._next().text.lower()
        self._next()  # the "("
        with self._descend():
            args = [self._expr()]
            while self._peek().kind is Kind.COMMA:
                self._next()
                args.append(self._expr())
        self._expect(Kind.RPAREN)
        return Func(name, tuple(args))

    def _table_name(self) -> str:
        tok = self._peek()
        if tok.kind in (Kind.IDENT, Kind.STRING):
            return self._next().text
        raise ParseError(f"expected a table name after FROM at column {tok.pos + 1}")

    # -- expressions, lowest precedence first --
    def _expr(self):
        # The entry point for a value expression that is not a predicate: the
        # SELECT list and ORDER BY use it, so arithmetic works there without a
        # surrounding comparison.
        return self._additive()

    def _or(self):
        node = self._and()
        with ExitStack() as spine:
            while self._at_keyword("or"):
                spine.enter_context(self._descend())
                self._next()
                node = Or(node, self._and())
        return node

    def _and(self):
        node = self._not()
        with ExitStack() as spine:
            while self._at_keyword("and"):
                spine.enter_context(self._descend())
                self._next()
                node = And(node, self._not())
        return node

    def _not(self):
        if self._at_keyword("not"):
            self._next()
            with self._descend():
                return Not(self._not())
        return self._comparison()

    def _comparison(self):
        left = self._additive()
        # IN and NOT IN read a literal list and test membership. NOT sits between
        # the value and IN here, an infix use the prefix NOT rule never sees.
        if self._at_keyword("in"):
            return self._in_list(left, negated=False)
        if self._at_keyword("like"):
            return self._like(left, negated=False)
        if self._at_keyword("between"):
            return self._between(left, negated=False)
        if self._at_keyword("not") and self._peek_next_is_keyword("in"):
            self._next()  # the NOT
            return self._in_list(left, negated=True)
        if self._at_keyword("not") and self._peek_next_is_keyword("like"):
            self._next()  # the NOT
            return self._like(left, negated=True)
        if self._at_keyword("not") and self._peek_next_is_keyword("between"):
            self._next()  # the NOT
            return self._between(left, negated=True)
        if self._peek().kind is Kind.OP:
            op = self._next().text
            right = self._additive()
            return Compare(op, left, right)
        return left

    def _peek_next_is_keyword(self, word: str) -> bool:
        nxt = self.tokens[self.i + 1]
        return nxt.kind is Kind.KEYWORD and nxt.text.lower() == word

    def _in_list(self, value, negated: bool) -> In:
        self._eat_keyword("in")
        self._expect(Kind.LPAREN)
        items = [self._expr()]
        while self._peek().kind is Kind.COMMA:
            self._next()
            items.append(self._expr())
        self._expect(Kind.RPAREN)
        return In(value, tuple(items), negated)

    def _like(self, value, negated: bool) -> Like:
        self._eat_keyword("like")
        pattern = self._additive()
        return Like(value, pattern, negated)

    def _between(self, value, negated: bool) -> Between:
        self._eat_keyword("between")
        # _additive, not _expr through _or, so the AND below is the BETWEEN
        # separator and not a boolean conjunction that swallows the upper bound.
        low = self._additive()
        self._eat_keyword("and")
        high = self._additive()
        return Between(value, low, high, negated)

    def _additive(self):
        node = self._multiplicative()
        with ExitStack() as spine:
            while self._peek().kind in (Kind.PLUS, Kind.MINUS):
                spine.enter_context(self._descend())
                op = self._next().text
                node = BinOp(op, node, self._multiplicative())
        return node

    def _multiplicative(self):
        node = self._unary()
        with ExitStack() as spine:
            while self._peek().kind in (Kind.STAR, Kind.SLASH):
                spine.enter_context(self._descend())
                op = "*" if self._next().kind is Kind.STAR else "/"
                node = BinOp(op, node, self._unary())
        return node

    def _unary(self):
        if self._peek().kind is Kind.MINUS:
            self._next()
            with self._descend():
                return Neg(self._unary())
        return self._primary()

    def _primary(self):
        tok = self._peek()
        if tok.kind is Kind.LPAREN:
            self._next()
            with self._descend():
                node = self._or()
            self._expect(Kind.RPAREN)
            return node
        agg = self._try_aggregate()
        if agg is not None:
            return agg
        func = self._try_func()
        if func is not None:
            return func
        if tok.kind is Kind.IDENT:
            ref = self._column_ref()
            table, _, name = ref.partition(".")
            return Column(name, table) if "." in ref else Column(ref)
        if tok.kind is Kind.NUMBER:
            return Literal(float(self._next().text), is_string=False)
        if tok.kind is Kind.STRING:
            return Literal(self._next().text, is_string=True)
        raise ParseError(f"unexpected {tok.text!r} at column {tok.pos + 1}")


def parse(sql: str) -> Query:
    return _Parser(tokenize(sql)).parse()
