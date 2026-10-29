"""Plain-text report formatting shared by the scripts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any


def header(title: str, **fields: Any) -> str:
    """Title line plus ``Label: value`` lines; underscores in keys become spaces."""
    items = {"Generated": f"{datetime.now():%Y-%m-%d %H:%M:%S}", **fields}
    width = max(len(k) for k in items) + 2
    lines = [title, "=" * len(title)]
    lines += [f"{(k.replace('_', ' ') + ':').ljust(width)}{v}" for k, v in items.items()]
    return "\n".join(lines) + "\n"


def table(rows: Iterable[Sequence[Any]], headers: Sequence[str]) -> str:
    """Fixed-width table. Returns ``(none)`` when there are no rows."""
    rows = [[str(c) for c in row] for row in rows]
    if not rows:
        return "(none)\n"
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*r) for r in rows]
    return "\n".join(line.rstrip() for line in lines) + "\n"


def section(title: str, body: str) -> str:
    return f"\n{title}\n{'-' * len(title)}\n{body}"
