"""Wildcard list core (Phase 3) -- loading, slot order, and string assembly.

Deliberately GUI-free. Everything the wildcard generator actually *decides* --
which files are slots, what order they go in, how entries join into a subject
string, and what randomize does with locks and disables -- lives here so it can
be tested headlessly. `tools/wildcard_gui.py` is a thin ttkbootstrap shell over
this module and reimplements none of it; `tools/test_wildcards.py` asserts that.

The `__` convention (CLAUDE.md, RESOLVED-6) is the one thing worth restating,
because it looks like a delimiter and is not. It is a **space placeholder**.
The originating tool joins the picked entries with the empty string and only
then substitutes:

    sequence.join('').replace(/__/g, ' ')

That is why a disabled slot drops out cleanly with no special case -- the
preceding entry's own trailing `__` still supplies the separating space. It is
also why `a3 with a.txt` needs no connector logic: it is an ordinary slot
holding `with a__`, and disabling it is how you ask for an unarmed vehicle.
"""

from __future__ import annotations

import json
import pathlib
import random


# Project root from source, the exe's own folder when frozen -- see approot.py.
# One-dir builds read the lists beside the exe, where the user can edit them.
from approot import ROOT  # noqa: E402

# Slots are sorted by plain basename (a1, a2, a3, b1...), so the folder a list
# lives in never affects its position -- `list_dirs` in the config can merge
# several folders.
#
# Nothing here is a fixed set of nine slots: `load_slots` globs `*.txt`, so
# dropping a new list into the folder adds a slot and deleting one removes it,
# with no code change. That is deliberate -- these lists are user data.
LIST_DIRS = (ROOT / "wildcards",)

CONFIG_NAME = "wildcards.config.json"

PLACEHOLDER = "__"


def list_dirs() -> tuple[pathlib.Path, ...]:
    """Where to look for wildcard lists: the config's `list_dirs`, else `wildcards/`."""
    cfg = load_config()
    if cfg.get("list_dirs"):
        return tuple(ROOT / d for d in cfg["list_dirs"])
    return LIST_DIRS


def config_path() -> pathlib.Path:
    return ROOT / CONFIG_NAME


def load_config() -> dict:
    """Read `wildcards.config.json` beside the data, or `{}` if absent.

    Absent is the normal case and must stay a supported one: every consumer
    falls back to a sensible default, so a user who never opens the file gets
    exactly the behaviour they had before it existed. A malformed file is
    reported by the caller rather than crashing the program -- a typo in JSON
    should not stop someone from generating a prompt.
    """
    p = config_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{p.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{p.name}: top level must be an object")
    return data


class ConfigError(Exception):
    """A config file exists but could not be read."""


class Slot:
    """One wildcard list: its entries, the current pick, and its two flags.

    `enabled` excludes the slot from the assembled string entirely. `locked`
    keeps the current pick through a randomize. They are independent: a locked
    disabled slot stays disabled and keeps its pick for when it is re-enabled.
    """

    def __init__(self, path: pathlib.Path, entries: list[str]):
        self.path = path
        self.entries = entries
        self.index = 0
        self.enabled = True
        self.locked = False

    # The filename prefix up to the first space -- "a1", "b2", "c3". Used as the
    # short label in the GUI and as the stable key in tests.
    @property
    def key(self) -> str:
        return self.path.stem.split(" ", 1)[0]

    @property
    def label(self) -> str:
        """Human-readable name: the filename minus its slot prefix."""
        parts = self.path.stem.split(" ", 1)
        return parts[1] if len(parts) > 1 else self.path.stem

    @property
    def current(self) -> str:
        return self.entries[self.index] if self.entries else ""

    def step(self, delta: int) -> None:
        """Move the pick, wrapping. Matches the previewer's cycle behaviour."""
        if self.entries:
            self.index = (self.index + delta) % len(self.entries)

    def set_current(self, entry: str) -> bool:
        """Point the slot at `entry` if the list holds it. True when it moved."""
        try:
            self.index = self.entries.index(entry)
        except ValueError:
            return False
        return True

    def matches(self, needle: str) -> list[tuple[int, str]]:
        """(index, entry) pairs containing `needle`, case-insensitively.

        Returns indices into `entries`, not positions in the filtered view, so
        a selection in a filtered list sets the real pick without a second
        lookup. An empty needle matches everything -- the lists run to 5521
        entries, so the GUI leans on this rather than on prev/next alone.
        """
        if not needle:
            return list(enumerate(self.entries))
        low = needle.lower()
        return [(i, e) for i, e in enumerate(self.entries) if low in e.lower()]

    def randomize(self, rng: random.Random) -> None:
        """Re-pick, unless locked. Disabled slots are still re-picked.

        Deliberate: a disabled slot contributes nothing to the string, so its
        pick is invisible, and rolling it anyway means re-enabling a slot after
        a few randomizes gives a fresh entry rather than a stale one. `locked`
        is the flag that means "hold this", and it is honoured regardless of
        whether the slot is enabled.
        """
        if not self.locked and self.entries:
            self.index = rng.randrange(len(self.entries))


def civilian_keys(slots, cfg: dict | None = None) -> set[str]:
    """Which slot keys the "Unarmed civilian" button leaves enabled.

    Resolution order, each falling through to the next:

    1. `civilian_slots` in the config -- an explicit list of slot keys, which
       is the modding hook. Someone who adds `a4 propulsion.txt` decides for
       themselves whether it is part of a civilian vehicle.
    2. `connector_slot` in the config -- keep the `a` group but drop the named
       connector. Covers the common case of renaming the connector list.
    3. The built-in rule: the `a` group minus `a3`.

    The fallback is what the button did before this was configurable, so an
    absent config changes nothing. It is only a *default* though, and the
    reason it needs overriding is concrete: it assumes the vehicle description
    is the `a` group and the connector is `a3`. Both are conventions of the
    lists as shipped, not facts about the format.
    """
    cfg = load_config() if cfg is None else cfg
    keys = {s.key for s in slots}

    explicit = cfg.get("civilian_slots")
    if explicit is not None:
        return {k for k in explicit if k in keys}

    connector = cfg.get("connector_slot", "a3")
    return {k for k in keys if k.startswith("a") and k != connector}


DEFAULT_SLOTS = ("a2", "a3", "c3")


def default_keys(slots, cfg: dict | None = None) -> set[str]:
    """Which slot keys are switched on when the program starts.

    `default_slots` in the config, else `DEFAULT_SLOTS` -- the user's chosen
    starting point, a vehicle with a connector and a plain gun noun ("tractor
    vehicle with a pistol"). Unknown keys are ignored. If none of the keys
    exist (the lists were renamed), every slot starts on rather than none, so
    the program never opens on an empty subject.
    """
    cfg = load_config() if cfg is None else cfg
    keys = {s.key for s in slots}
    chosen = {k for k in cfg.get("default_slots", DEFAULT_SLOTS) if k in keys}
    return chosen or keys


def load_slots(dirs=None) -> list[Slot]:
    """Load every `.txt` in `dirs`, ordered by plain basename sort.

    Entries are `rstrip()`ed -- the files are CRLF, and the trailing `__` must
    survive, so this strips whitespace only. Blank lines are dropped; a list
    file ending without a newline still yields its last entry.

    Lines beginning with `#` are skipped, so a modder can comment a list or
    park an entry without deleting it. No shipped entry starts with `#`, and
    none could usefully -- it would be a literal `#` in the prompt.
    """
    dirs = list_dirs() if dirs is None else dirs
    paths: list[pathlib.Path] = []
    for d in dirs:
        d = pathlib.Path(d)
        if d.is_dir():
            paths.extend(p for p in d.glob("*.txt") if p.is_file())

    slots: list[Slot] = []
    for path in sorted(paths, key=lambda p: p.name):
        text = path.read_text(encoding="utf-8", errors="replace")
        entries = [line.rstrip() for line in text.splitlines()]
        entries = [e for e in entries if e and not e.startswith("#")]
        if entries:
            slots.append(Slot(path, entries))
    return slots


def assemble(slots) -> str:
    """Join the enabled slots' picks and expand `__` into spaces.

    Concatenation with no separator, exactly as the originating tool does it --
    the spacing is carried entirely by the placeholders inside the entries.

    Runs of whitespace are then collapsed. The originating JS tool does not do
    this, and for clean data it changes nothing -- but the lists are hand-authored
    and a handful of entries carry a literal double space or are a bare `__`
    with no text at all. Those produce a visible "with a  duralumin" gap in the
    prompt. Collapsing here fixes every such case at once, and means a future
    typo in the lists degrades to correct output rather than a subtly malformed
    prompt. `tools/test_wildcards.py` audits the lists exhaustively and reports
    the anomalies, so they stay visible rather than being silently papered over.
    """
    joined = "".join(s.current for s in slots if s.enabled)
    return " ".join(joined.replace(PLACEHOLDER, " ").split())


def randomize(slots, rng: random.Random | None = None) -> None:
    """Re-pick every unlocked slot. Locks and disables are per-slot policy."""
    rng = rng or random.Random()
    for s in slots:
        s.randomize(rng)
