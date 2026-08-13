"""Command line: ``python -m quarry "SELECT ... FROM file.csv"``."""

from __future__ import annotations

import sys

from . import run


def _format(cols: list[str], rows: list[dict[str, str]]) -> str:
    widths = {c: len(c) for c in cols}
    for r in rows:
        for c in cols:
            widths[c] = max(widths[c], len(str(r[c])))

    def line(values) -> str:
        return "  ".join(str(v).ljust(widths[c]) for c, v in zip(cols, values, strict=True))

    out = [line(cols), line(["-" * widths[c] for c in cols])]
    out += [line([r[c] for c in cols]) for r in rows]
    return "\n".join(out)


def _run_and_print(sql: str) -> None:
    cols, rows = run(sql)
    print(_format(cols, rows))
    print(f"\n({len(rows)} row{'' if len(rows) == 1 else 's'})")


def _repl() -> int:
    # An interactive prompt. One query per line, run against the working
    # directory. A query error prints a message and the session continues; a
    # blank line is ignored; Ctrl-D (EOF) or `.exit` ends it.
    try:
        import readline  # noqa: F401  optional line editing, not required
    except ImportError:
        pass

    print("quarry. Type a SQL query, .exit to quit, Ctrl-D for EOF.")
    while True:
        try:
            line = input("quarry> ")
        except EOFError:
            print()
            return 0
        query = line.strip()
        if not query:
            continue
        if query == ".exit":
            return 0
        try:
            _run_and_print(query)
        except Exception as e:  # noqa: BLE001: keep the session alive on any query error
            print(f"error: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return _repl()
    try:
        _run_and_print(argv[0])
    except Exception as e:  # noqa: BLE001: the CLI turns any query error into a message
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
