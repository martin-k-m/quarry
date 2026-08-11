"""Turn SQL text into a flat stream of tokens.

The lexer is deliberately dumb: it knows characters, not grammar. It recognises
keywords, identifiers, numbers, single-quoted strings and the handful of
operators the parser needs, and it reports the position of anything it cannot,
so a syntax error points at a column rather than at nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class Kind(Enum):
    KEYWORD = auto()
    IDENT = auto()
    NUMBER = auto()
    STRING = auto()
    OP = auto()      # = != < <= > >=
    STAR = auto()    # * for SELECT *, COUNT(*), and multiplication
    PLUS = auto()    # + in arithmetic
    MINUS = auto()   # - in arithmetic, and unary minus
    SLASH = auto()   # / in arithmetic
    COMMA = auto()
    DOT = auto()     # . between a table and a column, as in a.id
    LPAREN = auto()
    RPAREN = auto()
    EOF = auto()


KEYWORDS = {
    "select", "from", "where", "order", "by",
    "asc", "desc", "limit", "and", "or", "not",
    "group", "having",
    "distinct", "as", "join", "inner", "on",
    "left", "outer",
}


@dataclass(frozen=True)
class Token:
    kind: Kind
    text: str
    pos: int


class LexError(ValueError):
    """A character the lexer cannot begin a token with."""


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(sql)

    while i < n:
        c = sql[i]

        if c.isspace():
            i += 1
            continue

        if c == "'":
            # A single-quoted string literal. Two '' inside is one literal quote,
            # the SQL convention, so a value can contain the delimiter.
            j = i + 1
            chars: list[str] = []
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        chars.append("'")
                        j += 2
                        continue
                    break
                chars.append(sql[j])
                j += 1
            if j >= n:
                raise LexError(f"unterminated string starting at column {i + 1}")
            tokens.append(Token(Kind.STRING, "".join(chars), i))
            i = j + 1
            continue

        if c.isdigit() or (c == "." and i + 1 < n and sql[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (sql[j].isdigit() or (sql[j] == "." and not seen_dot)):
                seen_dot = seen_dot or sql[j] == "."
                j += 1
            tokens.append(Token(Kind.NUMBER, sql[i:j], i))
            i = j
            continue

        if c.isalpha() or c == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            kind = Kind.KEYWORD if word.lower() in KEYWORDS else Kind.IDENT
            tokens.append(Token(kind, word, i))
            i = j
            continue

        if c in "<>=!":
            two = sql[i:i + 2]
            if two in ("<=", ">=", "!=", "<>"):
                tokens.append(Token(Kind.OP, "!=" if two == "<>" else two, i))
                i += 2
                continue
            if c in "<>=":
                tokens.append(Token(Kind.OP, c, i))
                i += 1
                continue
            raise LexError(f"stray '!' at column {i + 1}")

        simple = {
            "*": Kind.STAR, ",": Kind.COMMA, ".": Kind.DOT,
            "(": Kind.LPAREN, ")": Kind.RPAREN,
            "+": Kind.PLUS, "-": Kind.MINUS, "/": Kind.SLASH,
        }
        if c in simple:
            tokens.append(Token(simple[c], c, i))
            i += 1
            continue

        raise LexError(f"unexpected character {c!r} at column {i + 1}")

    tokens.append(Token(Kind.EOF, "", n))
    return tokens
