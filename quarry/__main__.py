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
        return "  ".join(str(v).ljust(widths[c]) for c, v in zip(cols, values))

    out = [line(cols), line(["-" * widths[c] for c in cols])]
    out += [line([r[c] for c in cols]) for r in rows]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print('usage: python -m quarry "SELECT ... FROM file.csv"', file=sys.stderr)
        return 2
    try:
        cols, rows = run(argv[0])
    except Exception as e:  # noqa: BLE001: the CLI turns any query error into a message
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(_format(cols, rows))
    print(f"\n({len(rows)} row{'' if len(rows) == 1 else 's'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
