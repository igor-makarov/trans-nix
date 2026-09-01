"""Shared filesystem primitives."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def path_lexists(path: Path) -> bool:
    return os.path.lexists(path)
