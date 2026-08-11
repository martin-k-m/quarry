"""quarry, a small SQL query engine over CSV files."""

from __future__ import annotations

from .engine import QueryError, execute
from .lexer import LexError
from .parser import ParseError, Query, parse

__all__ = ["run", "parse", "execute", "Query", "LexError", "ParseError", "QueryError"]


def run(sql: str, base_dir: str = "."):
    """Parse and execute ``sql``, returning ``(columns, rows)``."""
    return execute(parse(sql), base_dir)
