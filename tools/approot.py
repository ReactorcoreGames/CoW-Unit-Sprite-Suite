"""The folder the suite's data lives under, whether run from source or frozen.

Run from source, that is the project root (the parent of `tools/`). Under
PyInstaller, `__file__` points inside the bundled `_internal/` directory, so
deriving data paths from it would make a one-dir build read tables and lists
the user cannot see or edit. When frozen we anchor to the executable's own
folder instead -- the folder the user is looking at in Explorer, and the whole
point of shipping one-dir.

Every module that reads data by path (`cowpalette`, `convert`, `previewer`,
`prompts`, `wildcards`) takes `ROOT` from here, so the release layout only has
to mirror the project layout: `reference/colortables/`, `pipeline/`,
`wildcards/` beside the exe.
"""

from __future__ import annotations

import pathlib
import sys


def root() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parent.parent


ROOT = root()
