"""Small output formatting helpers."""

from __future__ import annotations

import sys


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
