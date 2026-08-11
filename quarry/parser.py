"""Turn a token stream into a query tree.

A hand-written recursive-descent parser for the subset:

    SELECT <* | col, ...> FROM <table> [WHERE <expr>]
    [ORDER BY <col> [ASC|DESC]] [LIMIT <n>]

The expression grammar has the usual precedence, lowest first: OR, AND, NOT,
then a single comparison, then a primary (a column, a literal, or a
parenthesised expression). Recursive descent mirrors that grammar one function
per rule, which is why the shape of the code is the shape of the language.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import Kind, Token, tokenize


# ── Expression nodes ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Column:
    name: str


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


# ── The statement ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Query:
    columns: list[str]              # ["*"] or explicit names
    table: str
    where: object | None
    order_by: tuple[str, bool] | None   # (column, descending)
    limit: int | None


class ParseError(ValueError):
    """A token that does not fit the grammar."""


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

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
        columns = self._columns()
        self._eat_keyword("from")
        table = self._table_name()

        where = None
        if self._at_keyword("where"):
            self._next()
            where = self._or()

        order_by = None
        if self._at_keyword("order"):
            self._next()
            self._eat_keyword("by")
            col = self._expect(Kind.IDENT).text
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
        return Query(columns, table, where, order_by, limit)

    def _columns(self) -> list[str]:
        if self._peek().kind is Kind.STAR:
            self._next()
            return ["*"]
        names = [self._expect(Kind.IDENT).text]
        while self._peek().kind is Kind.COMMA:
            self._next()
            names.append(self._expect(Kind.IDENT).text)
        return names

    def _table_name(self) -> str:
        tok = self._peek()
        if tok.kind in (Kind.IDENT, Kind.STRING):
            return self._next().text
        raise ParseError(f"expected a table name after FROM at column {tok.pos + 1}")

    # -- expressions, lowest precedence first --
    def _or(self):
        node = self._and()
        while self._at_keyword("or"):
            self._next()
            node = Or(node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._at_keyword("and"):
            self._next()
            node = And(node, self._not())
        return node

    def _not(self):
        if self._at_keyword("not"):
            self._next()
            return Not(self._not())
        return self._comparison()

    def _comparison(self):
        left = self._primary()
        if self._peek().kind is Kind.OP:
            op = self._next().text
            right = self._primary()
            return Compare(op, left, right)
        return left

    def _primary(self):
        tok = self._peek()
        if tok.kind is Kind.LPAREN:
            self._next()
            node = self._or()
            self._expect(Kind.RPAREN)
            return node
        if tok.kind is Kind.IDENT:
            return Column(self._next().text)
        if tok.kind is Kind.NUMBER:
            return Literal(float(self._next().text), is_string=False)
        if tok.kind is Kind.STRING:
            return Literal(self._next().text, is_string=True)
        raise ParseError(f"unexpected {tok.text!r} at column {tok.pos + 1}")


def parse(sql: str) -> Query:
    return _Parser(tokenize(sql)).parse()
