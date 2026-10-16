"""Tests for the Phase 4 prompt templates (revision 4, the user's format).

Prompt *quality* is not testable from here -- it is a property of how the image
generator responds, and only the user can measure that. What is testable is
everything that would make a prompt wrong regardless of the model:

- the structure is the one the user specified: eight parts in fixed order,
  the subject leading, with the listed options for each choice
- assembly is total: every combination of addon x view x layout x grid x
  background x outlines x palette produces a clean prompt within budget, including for
  the longest subject the wildcard generator emits
- aspect ratio appears nowhere -- the generator sets it, not this program
- the parts the converter depends on survive: a flat keyable background by
  default, spaced same-size units on a sheet, and the 16-unit cap

Run: python tools/test_prompts.py
"""

from __future__ import annotations

import itertools
import re
import sys
import traceback

import convert
import prompts as P

_checks = 0
_failures: list[str] = []

LONGEST = ("undulating airborne anti-submarine warfare support vessel vehicle "
           "armed with a superheavy fuel-air-propelled selective-fire musket")


def check(cond: bool, label: str) -> bool:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
    return bool(cond)


def section(name: str) -> None:
    print(name)


def test_structure() -> None:
    section("template data")
    data = P.load()

    check(data["order"] == ["subject", "addon", "view", "layout",
                            "background", "style", "outlines", "palette"],
          f"part order is {data['order']}")
    check(set(P.option_keys("addon", data)) == {"bold", "none", "detailed"},
          f"addon options are {P.option_keys('addon', data)}")
    check(len(P.option_keys("view", data)) == 6,
          f"expected 6 views, got {P.option_keys('view', data)}")
    check(set(P.option_keys("layout", data)) == {"single", "sheet"},
          f"layout options are {P.option_keys('layout', data)}")
    check(P.options("addon", data)["none"]["text"] == "", "addon 'none' is not empty")

    # Every option is shown in a GUI combobox, so each needs a label, and
    # every default must be one of its own options.
    for part in P.CHOICE_PARTS:
        check(P.default(part, data) in data[part]["options"], f"{part}: default is not an option")
        for key, opt in P.options(part, data).items():
            check(bool(opt.get("label")), f"{part}/{key}: no label")
    check(data["layout"]["default_grid"] in data["layout"]["grids"], "default grid not an option")
    check(data["background"]["default_color"] in data["background"]["colors"],
          "default color not an option")

    # The user's chosen defaults.
    for part, want in (("addon", "none"), ("view", "elevated_side"), ("layout", "sheet"),
                       ("background", "chroma_green")):
        check(P.default(part, data) == want, f"{part} default is {P.default(part, data)!r}")
    check(data["layout"]["default_grid"] == "4x4", "default grid is not 4x4")
    check(data["outlines"]["default"] is True, "outlines are not on by default")
    # Edit mode is the recommended route: keep the reference's colors.
    check(P.default("palette", data) == "keep", "the palette part does not default to 'keep'")

    # Unit class plays no part in prompts any more; the reference image is the
    # user's pick from the folder, which must still hold the sheets to pick.
    check("classes" not in data, "prompts.json still carries per-class metadata")
    check("unit_class" not in P.build("x", data=data), "build() still takes a class")
    check(len(list(P.REFERENCE_DIR.glob("*.png"))) >= 12, "reference folder is short of images")

    print("    8 parts in order, defaults as chosen, no class in the prompt path")


def test_no_aspect() -> None:
    section("aspect ratio is the generator's job, not ours")
    data = P.load()
    raw = P.PROMPTS.read_text(encoding="utf-8").lower()
    check("aspect" not in raw.replace("aspect ratio is deliberately absent", ""),
          "prompts.json still carries an aspect setting")
    built = P.build("x", data=data)
    check("aspect" not in built, "build() still returns an aspect")
    ratio = re.compile(r"\b\d+:\d+\b")
    for combo in _combos(data):
        text = P.build("tank", data=data, **combo)["text"]
        if ratio.search(text) or "aspect" in text.lower():
            check(False, f"{combo}: prompt mentions an aspect ratio")
            break
    else:
        check(True, "no prompt mentions an aspect ratio")
    print("    no aspect in data, output or prompt text")


def _combos(data: dict):
    for addon, view, layout, bg, outlines, palette in itertools.product(
            P.option_keys("addon", data), P.option_keys("view", data),
            P.option_keys("layout", data), P.option_keys("background", data),
            (True, False), P.option_keys("palette", data)):
        grid_opts = P.grids(data) if layout == "sheet" else [None]
        color_opts = P.colors(data) if P.needs_color(bg, data) else [None]
        for grid in grid_opts:
            for color in color_opts:
                yield dict(addon=addon, view=view, layout=layout, grid=grid,
                           background=bg, color=color, outlines=outlines,
                           palette=palette)


def test_build_is_total() -> None:
    section("assembly over every combination")
    data = P.load()
    n = 0
    worst = 0
    for combo in _combos(data):
        n += 1
        b = P.build(LONGEST, data=data, **combo)
        t = b["text"]
        worst = max(worst, b["chars"])
        ok = (t.startswith("Undulating airborne") and LONGEST[1:] in t
              and "  " not in t and "{" not in t and "}" not in t
              and "\n" not in t and b["chars"] <= b["budget"]
              and t.endswith("."))
        if not ok:
            check(False, f"{combo}: bad prompt ({b['chars']} chars): {t[:120]!r}")
    check(True, "all combinations assemble")
    print(f"    {n} combinations assemble cleanly; worst case {worst} chars")


def test_parts() -> None:
    section("each part lands where the user specified")
    data = P.load()
    subject = "bulldozer vehicle"

    t = P.build(subject, addon="bold", view="side", layout="single", palette="none",
                data=data)["text"]
    check(t.startswith("Bulldozer vehicle as a game sprite in the style of Advance Wars. "),
          f"subject clause reads {t[:70]!r}")
    # Order: each part's text appears after the previous one's.
    pos = [t.index(s) for s in ("Bulldozer", "A bold simple shape", "Direct side-view.",
                                "One subject seen in full", "Flat pure chroma green",
                                "Chunky low-resolution", "Thick dark outlines.")]
    check(pos == sorted(pos), "parts are out of order")
    check(t.endswith("Thick dark outlines."), "outlines are not the last part")

    # Addon none and outlines off drop out entirely, without stray spaces.
    bare = P.build(subject, addon="none", view="side", outlines=False, palette="none",
                   data=data)["text"]
    check("readable when scaled" not in bare and "Thick dark" not in bare,
          "none/off parts still present")
    check("Advance Wars. Direct side-view." in bare, "addon 'none' left a gap")
    check(bare.endswith("picked out in flat tone."), "outlines-off did not drop the suffix")

    sheet = P.build(subject, layout="sheet", grid="4x4", data=data)
    check("A grid of sixteen of them, 4 by 4," in sheet["text"],
          f"sheet text reads {sheet['text']!r}")
    check(sheet["grid"] == "4x4", "grid not reported")
    single = P.build(subject, layout="single", grid="4x4", data=data)
    check(single["grid"] is None and "grid of" not in single["text"],
          "a single-subject prompt picked up a grid")

    env = P.build(subject, background="environment", color="teal", data=data)
    check("In a teal themed environment." in env["text"], "color not filled in")
    check(env["color"] == "teal", "color not reported")
    grad = P.build(subject, background="gradient", data=data)
    check(f"Bold black-{data['background']['default_color']} gradient background." in grad["text"],
          "gradient did not take the default color")
    check(P.build(subject, color="red", data=data)["color"] is None,
          "a chroma background reported a color it did not use")

    # Internal capitals survive; whitespace from the wildcard lists normalizes.
    check(P.build("T-90 MBT hull", data=data)["text"].startswith("T-90 MBT hull as"),
          "internal capitals mangled")
    check(P.build("  flying   farm  vehicle ", data=data)["text"].startswith("Flying farm vehicle as"),
          "subject whitespace not normalized")

    print("    subject leads, parts in order, empty parts drop out, slots filled")


def test_palette() -> None:
    section("optional color request")
    data = P.load()

    keep = data["palette"]["options"]["keep"]["text"]
    check(keep == "Keep the same color palette and style as original image.",
          f"keep clause changed: {keep!r}")
    default = P.build("tank", data=data)["text"]
    check(default.endswith("Thick dark outlines. " + keep),
          f"the default prompt does not end with the keep clause: {default[-90:]!r}")
    desc = P.build("tank", palette="described", data=data)["text"]
    check(desc.endswith(data["palette"]["options"]["described"]["text"]),
          "described palette is not the last part")
    check("Thick dark outlines. Warm coral-red" in desc, "palette does not follow outlines")
    none = P.build("tank", palette="none", data=data)["text"]
    check(none.endswith("Thick dark outlines.") and none + " " + keep == default,
          "palette 'none' changed the rest of the prompt")
    # The hex option backfired -- the model painted the codes on as car paint
    # (cars.jpeg) -- and was removed. No option may list hex codes again.
    for key, opt in P.options("palette", data).items():
        check(not re.search(r"#[0-9a-fA-F]{6}|\{hexes\}", opt["text"]),
              f"palette option {key} lists hex codes")
    print("    keep clause last by default; described and none intact; no hex codes")


def test_converter_contract() -> None:
    section("the converter still gets what it needs")
    data = P.load()

    # The default background must be one convert.py can actually key.
    d = P.build("x", data=data)
    check(d["key"] == "green", "default background is not chroma green")
    for key, opt in P.options("background", data).items():
        if "key" in opt:
            check(opt["key"] in convert.CHROMA_KEYS,
                  f"background {key} names key {opt['key']!r} that convert.py lacks")
            check("flat" in opt["text"].lower(), f"keyable background {key} is not asked flat")
        else:
            check(P.build("x", background=key, data=data)["key"] is None,
                  f"themed background {key} claims to be keyable")

    # The splitter needs separated, equally sized units, and the count capped.
    sheet = data["layout"]["options"]["sheet"]["text"].lower()
    for phrase in ("evenly spaced", "wide flat gaps", "same size", "same angle"):
        check(phrase in sheet, f"sheet layout lost {phrase!r}")
    check(max(data["layout"]["grids"].values()) <= 16, "more than 16 units offered")

    # The reference is the image being edited; the only words spent on it are
    # the keep clause's "original image".
    for phrase in ("attached", "reference image", "the reference"):
        check(phrase not in d["text"].lower(), f"prompt refers to {phrase!r}")

    print("    green default keys, sheet spacing intact, 16-unit cap, no reference talk")


def test_errors() -> None:
    section("error handling")
    for kwargs, label in ((dict(view="nope"), "view"), (dict(addon="nope"), "addon"),
                          (dict(layout="nope"), "layout"), (dict(background="nope"), "background"),
                          (dict(layout="sheet", grid="9x9"), "grid"),
                          (dict(background="abstract", color="plaid"), "color"),
                          (dict(palette="nope"), "palette")):
        try:
            P.build("x", **kwargs)
            check(False, f"unknown {label} did not raise")
        except KeyError:
            check(True, f"unknown {label} raises")
    try:
        P.build("   ")
        check(False, "empty subject did not raise")
    except ValueError:
        check(True, "empty subject raises")
    print("    unknown option of every kind and empty subject all raise")


def main() -> int:
    tests = [
        test_structure,
        test_no_aspect,
        test_build_is_total,
        test_parts,
        test_palette,
        test_converter_contract,
        test_errors,
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
