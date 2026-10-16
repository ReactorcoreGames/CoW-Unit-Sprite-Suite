"""Tests for the Phase 3 wildcard generator.

What is actually checkable here is the data and the string algebra: the slot
ordering, the `__` substitution, and randomize's treatment of locks and
disables. Those are the parts that can be silently wrong -- a mis-sorted slot
list or a stray separator produces a plausible-looking string that is subtly
not what the originating tool would have produced.

The GUI itself is not tested. It needs a display, and the user has to run it.
What *is* tested is that the GUI holds no logic of its own to test: the same
shared-module check `test_convert.py` makes about the previewer.

Run: python tools/test_wildcards.py
"""

from __future__ import annotations

import pathlib
import random
import sys
import traceback

import wildcards as wc

ROOT = pathlib.Path(__file__).resolve().parent.parent

# From CLAUDE.md's slot table. Counts are asserted as a floor rather than an
# equality: the lists are the user's own working data and they grow. `a3` is
# the interesting one -- it was documented at 1 entry and now holds 3, which is
# exactly the "add alternatives and it randomizes for free" behaviour
# RESOLVED-6 designed for, so pinning it to 1 would fail for a good reason.
EXPECTED_KEYS = ["a1", "a2", "a3", "b1", "b2", "b3", "c1", "c2", "c3", "d1", "d2", "d3"]
MIN_ENTRIES = {
    "a1": 5521, "a2": 2982, "a3": 1,
    "b1": 7, "b2": 2160, "b3": 1218,
    "c1": 314, "c2": 623, "c3": 110,
    "d1": 9, "d2": 1101, "d3": 19,
}
# `d1`-`d3` ("driven by a" + person + type) were added by the user after the
# original nine -- the data-driven slot design absorbing a new group unchanged.
# `b2` was 2164 until the user removed four malformed entries (two bare `__`
# plus duplicates) that the exhaustive audit below surfaced. A floor can move
# *down* when the user deliberately cleans a list -- that is data maintenance,
# not a regression. Lower it when the audit reports zero anomalies, as it does
# now; do not lower it to make an unexplained shortfall go away.

_checks = 0
_failures: list[str] = []


def check(cond: bool, label: str) -> bool:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
    return bool(cond)


def section(name: str) -> None:
    print(name)


# ------------------------------------------------------------------ loading

def test_loading() -> None:
    section("loading and slot order")
    slots = wc.load_slots()

    check(len(slots) == len(EXPECTED_KEYS),
          f"expected {len(EXPECTED_KEYS)} slots, got {len(slots)}")
    check([s.key for s in slots] == EXPECTED_KEYS,
          f"slot order is {[s.key for s in slots]}, expected {EXPECTED_KEYS}")

    # The ordering must come from a plain basename sort -- that is the whole
    # reason the vehicle lists were renamed a1/a2/a3, and what keeps the order
    # when the config merges several folders. Assert the sort key rather than
    # just the result, so a coincidence cannot pass.
    names = [s.path.name for s in slots]
    check(names == sorted(names), "slots are not in plain basename order")
    folders = {s.path.parent.name for s in slots}
    check(folders == {"wildcards"}, f"expected slots from wildcards/, got {folders}")

    for s in slots:
        floor = MIN_ENTRIES[s.key]
        check(len(s.entries) >= floor,
              f"{s.key}: {len(s.entries)} entries, expected at least {floor}")

    # The single most load-bearing fact about this data.
    for s in slots:
        bad = [e for e in s.entries if not e.endswith(wc.PLACEHOLDER)]
        check(not bad, f"{s.key}: {len(bad)} entries lack a trailing '__' e.g. {bad[:2]}")

    # rstrip() must strip the CRLF and nothing else.
    for s in slots:
        check(not any(e != e.rstrip() for e in s.entries),
              f"{s.key}: entries carry trailing whitespace")
        check(not any("\r" in e or "\n" in e for e in s.entries),
              f"{s.key}: entries carry embedded newlines")
        check(all(e for e in s.entries), f"{s.key}: blank entry survived loading")

    print(f"    {len(slots)} slots, {sum(len(s.entries) for s in slots)} entries, all end in '__'")


# ------------------------------------------------------------ substitution

def test_placeholder_substitution() -> None:
    section("'__' is a space placeholder, not a delimiter")

    # The reference implementation, transcribed from the originating tool:
    #   sequence.join('').replace(/__/g, ' ')
    def reference(entries):
        return "".join(entries).replace("__", " ").strip()

    fake = [
        wc.Slot(pathlib.Path("a1 x.txt"), ["big__"]),
        wc.Slot(pathlib.Path("a2 y.txt"), ["tank vehicle__"]),
        wc.Slot(pathlib.Path("a3 z.txt"), ["with a__"]),
        wc.Slot(pathlib.Path("b1 w.txt"), ["heavy__"]),
        wc.Slot(pathlib.Path("c3 v.txt"), ["gun__"]),
    ]
    check(wc.assemble(fake) == "big tank vehicle with a heavy gun",
          f"assemble gave {wc.assemble(fake)!r}")
    check(wc.assemble(fake) == reference([s.current for s in fake]),
          "assemble disagrees with the reference join-then-replace")

    # A disabled slot must drop out with no leftover or missing space. This is
    # the property that makes connector logic unnecessary.
    fake[2].enabled = False
    fake[3].enabled = False
    fake[4].enabled = False
    check(wc.assemble(fake) == "big tank vehicle",
          f"disabling slots gave {wc.assemble(fake)!r}")
    check("  " not in wc.assemble(fake), "double space after dropping slots")

    # Dropping a slot from the middle, not the tail.
    for s in fake:
        s.enabled = True
    fake[1].enabled = False
    check(wc.assemble(fake) == "big with a heavy gun",
          f"middle drop gave {wc.assemble(fake)!r}")

    # Everything off is empty, not a pile of spaces.
    for s in fake:
        s.enabled = False
    check(wc.assemble(fake) == "", f"all-disabled gave {wc.assemble(fake)!r}")

    # And against the real lists: no assembled string may contain '__', a
    # double space, or leading/trailing whitespace.
    slots = wc.load_slots()
    rng = random.Random(1234)
    dirty = 0
    for _ in range(300):
        wc.randomize(slots, rng)
        for s in slots:
            s.enabled = rng.random() > 0.3
        out = wc.assemble(slots)
        if "__" in out or "  " in out or out != out.strip():
            dirty += 1
    check(dirty == 0, f"{dirty}/300 random assemblies had stray '__' or spacing")

    # Exhaustive, not sampled. The 300 random assemblies above touch maybe 2%
    # of a 2164-entry list, which is how four real anomalies went unnoticed
    # until one turned up in a live run: two `a2` entries carry a literal
    # double space, and two `b2` entries are a bare `__` with no text. Every
    # entry is now walked, so a single bad line cannot hide in a long list.
    slots = wc.load_slots()
    anomalies = []
    for s in slots:
        for e in s.entries:
            body = e[:-len(wc.PLACEHOLDER)] if e.endswith(wc.PLACEHOLDER) else e
            txt = body.replace(wc.PLACEHOLDER, " ")
            if not body.strip():
                anomalies.append((s.key, "empty", e))
            elif "  " in txt or txt != txt.strip():
                anomalies.append((s.key, "spacing", e))

    # assemble() must produce a clean string even so -- that is the contract,
    # and it is what protects the prompt from a typo in hand-authored data.
    for s in slots:
        s.enabled = True
    for key, _kind, entry in anomalies:
        slot = [x for x in slots if x.key == key][0]
        slot.set_current(entry)
        out = wc.assemble(slots)
        check("  " not in out, f"{key}: anomalous entry {entry!r} leaked a double space")
        check(out == out.strip(), f"{key}: anomalous entry {entry!r} leaked edge whitespace")

    # Every single entry, as the only enabled slot, must assemble cleanly.
    for s in slots:
        s.enabled = False
    dirty_entries = []
    for s in slots:
        s.enabled = True
        for i in range(len(s.entries)):
            s.index = i
            out = wc.assemble(slots)
            if "  " in out or out != out.strip() or wc.PLACEHOLDER in out:
                dirty_entries.append((s.key, s.entries[i]))
        s.enabled = False
    check(not dirty_entries,
          f"{len(dirty_entries)} entries assemble unclean, e.g. {dirty_entries[:3]}")

    print(f"    300 random assemblies clean; disabled slots drop without a gap")
    print(f"    all {sum(len(s.entries) for s in slots)} entries assemble cleanly "
          f"({len(anomalies)} source anomalies absorbed)")
    if anomalies:
        print("    note: source lists contain hand-authoring anomalies --")
        for key, kind, entry in anomalies:
            print(f"      {key}: {kind} {entry!r}")


def test_documented_examples() -> None:
    section("documented example outputs")

    # CLAUDE.md quotes three sample outputs. They were produced against an
    # earlier snapshot of the lists -- "hydroelectric" and "dart" are no longer
    # present, because this is the user's live working data. So the check is
    # structural rather than literal: build each documented shape from entries
    # that do exist and require the same grammar.
    slots = wc.load_slots()
    by = {s.key: s for s in slots}

    def pick(key, entry):
        ok = by[key].set_current(entry)
        check(ok, f"{key}: entry {entry!r} not found in the list")

    # all slots -- vehicle, connector, both weapon groups, and the driver
    pick("a1", "young__")
    pick("a2", "spaceship vehicle__")
    pick("a3", "with a__")
    pick("b1", "ultralight__")
    pick("b2", "steam-powered__")
    pick("b3", "gossamer__")
    pick("c1", "beam__")
    pick("c2", "auto-fire__")
    pick("c3", "pistol__")
    pick("d1", "driven by a__")
    pick("d2", "evil general__")
    pick("d3", "person__")
    for s in slots:
        s.enabled = True
    out = wc.assemble(slots)
    check(out == "young spaceship vehicle with a ultralight steam-powered gossamer "
                 "beam auto-fire pistol driven by a evil general person",
          f"all-slots gave {out!r}")

    # unarmed civilian -- the RESOLVED-6 recipe, a1+a2 only
    for s in slots:
        s.enabled = s.key in ("a1", "a2")
    out = wc.assemble(slots)
    check(out == "young spaceship vehicle", f"unarmed gave {out!r}")
    check(out.endswith("vehicle"), "every a2 entry ends in 'vehicle' -- see CLAUDE.md")

    # simple gun -- vehicle, connector, one weight, one gun noun
    for s in slots:
        s.enabled = s.key in ("a1", "a2", "a3", "b1", "c3")
    pick("b1", "light__")
    out = wc.assemble(slots)
    check(out == "young spaceship vehicle with a light pistol", f"simple gun gave {out!r}")

    print("    three documented shapes reproduce (entries re-sourced; lists have moved on)")


# --------------------------------------------------------------- randomize

def test_randomize_honours_locks() -> None:
    section("randomize honours locks and disables")
    slots = wc.load_slots()
    rng = random.Random(7)

    # A locked slot never moves, however many rolls.
    wc.randomize(slots, rng)
    locked_keys = {"a2", "b2", "c1"}
    for s in slots:
        s.locked = s.key in locked_keys
    held = {s.key: s.current for s in slots if s.locked}
    free_start = {s.key: s.index for s in slots if not s.locked}

    for _ in range(50):
        wc.randomize(slots, rng)
        for s in slots:
            if s.locked:
                check(s.current == held[s.key], f"{s.key}: locked slot moved")

    # ...and the unlocked ones actually do move, or the test above is vacuous.
    moved = sum(1 for s in slots if not s.locked and s.index != free_start[s.key])
    check(moved >= 4, f"only {moved} of 6 unlocked slots ever moved")

    # A lock on a disabled slot is still a lock.
    for s in slots:
        s.locked = False
        s.enabled = True
    target = slots[0]
    target.locked = True
    target.enabled = False
    before = target.current
    for _ in range(20):
        wc.randomize(slots, rng)
    check(target.current == before, "a locked+disabled slot moved")
    check(target.enabled is False, "randomize changed an enable flag")

    # Randomize must never touch enable flags -- those are the user's choice.
    for s in slots:
        s.enabled = s.key in ("a1", "a2")
        s.locked = False
    wc.randomize(slots, rng)
    check([s.enabled for s in slots] == [s.key in ("a1", "a2") for s in slots],
          "randomize altered the enabled set")

    # An unlocked disabled slot IS re-picked -- documented behaviour, so that
    # re-enabling a slot gives a fresh entry rather than a stale one.
    s = slots[4]
    s.enabled = False
    s.locked = False
    seen = set()
    for _ in range(30):
        wc.randomize(slots, rng)
        seen.add(s.index)
    check(len(seen) > 1, "a disabled unlocked slot was never re-picked")

    # Determinism: the same seed gives the same picks.
    a, b = wc.load_slots(), wc.load_slots()
    wc.randomize(a, random.Random(99))
    wc.randomize(b, random.Random(99))
    check([x.index for x in a] == [x.index for x in b], "randomize is not seed-deterministic")

    print("    locks held over 50 rolls; enables untouched; seeded runs match")


def test_slot_mechanics() -> None:
    section("slot navigation and filtering")
    slots = wc.load_slots()
    by = {s.key: s for s in slots}

    s = by["b1"]
    n = len(s.entries)
    s.index = 0
    s.step(-1)
    check(s.index == n - 1, "step(-1) did not wrap to the end")
    s.step(1)
    check(s.index == 0, "step(+1) did not wrap to the start")
    s.step(n)
    check(s.index == 0, "stepping a full lap did not return to the start")

    # Filtering returns indices into `entries`, so a filtered pick is a real pick.
    a2 = by["a2"]
    hits = a2.matches("tank")
    check(len(hits) > 0, "no a2 entries match 'tank'")
    for i, e in hits:
        check(a2.entries[i] == e, "matches() index does not address its own entry")
        check("tank" in e.lower(), "matches() returned a non-matching entry")

    check(a2.matches("TANK") == hits, "filter is not case-insensitive")
    check(len(a2.matches("")) == len(a2.entries), "empty filter did not match everything")
    check(a2.matches("zzzzqqq") == [], "nonsense filter matched something")

    check(a2.set_current(a2.entries[17]) is True, "set_current rejected a real entry")
    check(a2.index == 17, "set_current pointed at the wrong index")
    check(a2.set_current("not an entry__") is False, "set_current accepted a fake entry")
    check(a2.index == 17, "a rejected set_current still moved the index")

    # key/label split the filename at the first space.
    check(by["a3"].key == "a3", "key is not the filename prefix")
    check(by["a3"].label == "with a", f"label is {by['a3'].label!r}")

    print("    wrap, filter indices, and set_current all behave")


def test_modding_surface() -> None:
    section("modding: data-driven slots and config")
    import json
    import shutil
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cow_wc_"))
    try:
        # Slot count follows the files, not the code. This is the property that
        # lets someone add or remove a list in Explorer.
        for name in ["a1 one.txt", "a2 two.txt", "a3 join.txt", "b1 gun.txt"]:
            (tmp / name).write_text("alpha__\nbeta__\n", encoding="utf-8")
        slots = wc.load_slots((tmp,))
        check(len(slots) == 4, f"expected 4 slots from 4 files, got {len(slots)}")

        (tmp / "a4 extra.txt").write_text("gamma__\n", encoding="utf-8")
        slots = wc.load_slots((tmp,))
        check(len(slots) == 5, "adding a .txt did not add a slot")
        check([s.key for s in slots] == ["a1", "a2", "a3", "a4", "b1"],
              f"new slot sorted wrong: {[s.key for s in slots]}")

        (tmp / "a4 extra.txt").unlink()
        check(len(wc.load_slots((tmp,))) == 4, "removing a .txt did not remove a slot")

        # Comments let a modder park an entry without deleting it.
        (tmp / "b1 gun.txt").write_text(
            "# a note\nalpha__\n#beta__\ngamma__\n", encoding="utf-8")
        gun = [s for s in wc.load_slots((tmp,)) if s.key == "b1"][0]
        check(gun.entries == ["alpha__", "gamma__"],
              f"'#' comments not skipped: {gun.entries}")

        # civilian_keys: explicit list wins, then connector_slot, then default.
        slots = wc.load_slots((tmp,))
        check(wc.civilian_keys(slots, {}) == {"a1", "a2"},
              "default civilian rule is not 'a group minus a3'")
        check(wc.civilian_keys(slots, {"connector_slot": "a2"}) == {"a1", "a3"},
              "connector_slot override ignored")
        check(wc.civilian_keys(slots, {"civilian_slots": ["a1", "b1"]}) == {"a1", "b1"},
              "explicit civilian_slots ignored")
        check(wc.civilian_keys(slots, {"civilian_slots": ["a1", "nope"]}) == {"a1"},
              "civilian_slots did not drop an unknown key")
        check(wc.civilian_keys(slots, {"civilian_slots": []}) == set(),
              "an empty civilian_slots list should disable every slot")

        # default_keys: the startup selection. Built-in a2+a3+c3 on the real
        # lists, config override wins, and a selection matching nothing falls
        # back to every slot rather than an empty subject.
        real = wc.load_slots()
        check(wc.default_keys(real, {}) == {"a2", "a3", "c3"},
              f"default startup slots are {sorted(wc.default_keys(real, {}))}")
        check(wc.default_keys(real, {"default_slots": ["a1", "nope"]}) == {"a1"},
              "default_slots override ignored or unknown key kept")
        check(wc.default_keys(real, {"default_slots": ["nope"]}) == {s.key for s in real},
              "default_slots matching nothing did not fall back to every slot")

        # A malformed config raises ConfigError rather than crashing or being
        # silently ignored -- the GUI catches it and says so in the status bar.
        cfgdir = tmp / "cfg"
        cfgdir.mkdir()
        real_root = wc.ROOT
        try:
            wc.ROOT = cfgdir
            check(wc.load_config() == {}, "absent config is not an empty dict")
            (cfgdir / wc.CONFIG_NAME).write_text("{ not json", encoding="utf-8")
            try:
                wc.load_config()
                check(False, "malformed config did not raise ConfigError")
            except wc.ConfigError:
                check(True, "malformed config raises")
            (cfgdir / wc.CONFIG_NAME).write_text("[1,2]", encoding="utf-8")
            try:
                wc.load_config()
                check(False, "non-object config did not raise ConfigError")
            except wc.ConfigError:
                check(True, "non-object config raises")
            (cfgdir / wc.CONFIG_NAME).write_text(
                json.dumps({"connector_slot": "a2"}), encoding="utf-8")
            check(wc.load_config()["connector_slot"] == "a2", "valid config not read")
        finally:
            wc.ROOT = real_root
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # The frozen-build path must not derive data paths from __file__: under
    # PyInstaller that points into the bundled _internal/ dir, so a one-dir
    # build would read data the user cannot see. One shared root (approot.py)
    # serves every module that reads data by path; none may roll its own.
    for name in ("wildcards", "cowpalette", "convert", "previewer", "prompts"):
        code = _code_only(ROOT / "tools" / f"{name}.py")
        check("from approot import ROOT" in code, f"{name}.py does not take ROOT from approot")
        check("ROOT = " not in code, f"{name}.py derives its own ROOT")

    # And prove the anchor: fake a frozen interpreter whose exe lives in a temp
    # folder, and the root must be that folder.
    import subprocess
    fake = pathlib.Path(tempfile.mkdtemp())
    try:
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); sys.frozen = True;"
             "sys.executable = %r; import approot; print(approot.ROOT)"
             % (str(ROOT / "tools"), str(fake / "CoW.exe"))],
            capture_output=True, text=True,
        )
        check(probe.returncode == 0
              and pathlib.Path(probe.stdout.strip()) == fake.resolve(),
              f"frozen root does not anchor to the executable: {probe.stdout.strip()!r}")
    finally:
        shutil.rmtree(fake, ignore_errors=True)

    print("    slots follow the files; config overrides resolve; bad config raises")


def _code_only(path: pathlib.Path) -> str:
    """Source with comments and string literals removed.

    The naive substring checks below were failing on prose: `wildcards.py`
    mentions ttkbootstrap in its module docstring to say the *GUI* is the
    ttkbootstrap shell, and `wildcard_gui.py` mentions "quit" only in a comment
    saying Esc deliberately does not quit. Both are correct code being flagged
    by a test reading English. Tokenizing first means these checks look at what
    the module actually does.
    """
    import io
    import tokenize

    out = []
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_gui_holds_no_logic() -> None:
    section("GUI is a shell over the shared module")
    src = (ROOT / "tools" / "wildcard_gui.py").read_text(encoding="utf-8")
    gui_code = _code_only(ROOT / "tools" / "wildcard_gui.py")

    check("import wildcards" in src, "wildcard_gui.py does not import the shared module")

    # The GUI must not reimplement the substitution or the loader. These are
    # the two things a hurried edit would most plausibly inline.
    check('replace("__"' not in src and "replace('__'" not in src,
          "wildcard_gui.py performs its own '__' substitution")
    check(".splitlines()" not in src and "read_text" not in src,
          "wildcard_gui.py loads list files itself")
    check("wc.assemble" in src, "wildcard_gui.py does not call wc.assemble")
    check("wc.randomize" in src, "wildcard_gui.py does not call wc.randomize")
    check("wc.load_slots" in src, "wildcard_gui.py does not call wc.load_slots")
    check("wc.civilian_keys" in src, "wildcard_gui.py does not call wc.civilian_keys")
    check('startswith("a")' not in gui_code,
          "wildcard_gui.py still hardcodes the civilian slot rule")

    # And the core must stay GUI-free, so it is importable headlessly. Checked
    # on code rather than prose -- and then proven by actually importing it in
    # a subprocess with no display, which is the claim that matters.
    core_code = _code_only(ROOT / "tools" / "wildcards.py")
    check("ttkbootstrap" not in core_code and "tkinter" not in core_code,
          "wildcards.py imports a GUI toolkit")

    import subprocess
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import wildcards;"
         "assert 'tkinter' not in sys.modules and 'ttkbootstrap' not in sys.modules;"
         "print(len(wildcards.load_slots()))" % str(ROOT / "tools")],
        capture_output=True, text=True,
    )
    check(probe.returncode == 0 and probe.stdout.strip() == str(len(EXPECTED_KEYS)),
          f"importing wildcards.py headlessly failed: {probe.stderr.strip()[:200]}")

    # Matches previewer.py: Esc leaves a field rather than quitting.
    check("self.focus_set()" in src, "Esc does not hand focus back to the window")
    check("quit" not in gui_code.lower() and "destroy" not in gui_code.lower(),
          "the GUI binds a quit/destroy action -- Esc must never close the window")
    # gruvbox, light by default, and unlike the previewer's dark pydata.
    check('"gruvbox-light"' in src and "themename=LIGHT_THEME" in src,
          "GUI does not open in the light gruvbox theme")

    print("    no duplicated logic; core imports headlessly")


def main() -> int:
    tests = [
        test_loading,
        test_placeholder_substitution,
        test_documented_examples,
        test_randomize_honours_locks,
        test_slot_mechanics,
        test_modding_surface,
        test_gui_holds_no_logic,
    ]
    for t in tests:
        try:
            t()
        except Exception:
            _failures.append(f"{t.__name__} raised")
            traceback.print_exc()

    print()
    if _failures:
        print(f"{len(_failures)} of {_checks} checks FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"all {_checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
