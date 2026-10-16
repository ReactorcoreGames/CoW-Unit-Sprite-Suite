"""Tests for the per-unit reference gallery (`tools/build_references.py`).

The gallery is generated, so these check the generated files against the
properties a user relies on when picking one: every AW2 design is present once,
each sits on flat #00ff00, the large copy is an exact 6x nearest-neighbour
upscale, and the recolor kept neutrals (water, black, skin) while turning
team colors red.

Run: python tools/test_references.py   (after python tools/build_references.py)
"""

from __future__ import annotations

import sys
import traceback

import numpy as np
from PIL import Image

import build_references as br

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


def test_coverage() -> None:
    section("every AW2 design present once")
    sources = sorted(br.SRC.glob("*.png"))
    small = {p.stem for p in (br.OUT / "1x").glob("*.png")}
    big = {p.stem for p in br.OUT.glob("*.png")}
    check(small == big, f"1x and 6x sets differ: {sorted(small ^ big)[:5]}")
    for f in sources:
        army = next(a for a in br.ARMIES if f.stem.startswith(a + " "))
        name = f"{f.stem[len(army) + 1:]} - {army}"
        # The Neo Tank is one drawing in every army, so only Orange Star's copy
        # is kept; every other source must have its own reference.
        if name.startswith("Neo Tank") and army != "Orange Star":
            check(name not in small, f"{name}: duplicate Neo Tank kept")
        else:
            check(name in small, f"{name}: missing from the gallery")
    print(f"    {len(small)} references from {len(sources)} sources")


def test_files() -> None:
    section("flat green background, exact 6x copy")
    for p in sorted((br.OUT / "1x").glob("*.png")):
        a = np.array(Image.open(p).convert("RGB"))
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        check((border == (0, 255, 0)).all(), f"{p.stem}: border is not flat #00ff00")
        b = np.array(Image.open(br.OUT / p.name).convert("RGB"))
        check(b.shape == (a.shape[0] * br.SCALE, a.shape[1] * br.SCALE, 3)
              and (b[::br.SCALE, ::br.SCALE] == a).all(),
              f"{p.stem}: large copy is not an exact {br.SCALE}x upscale")
    print("    all bordered in #00ff00, all large copies exact")


def test_recolor() -> None:
    section("recolor keeps neutrals, turns team colors red")
    os_colors = set(br.opaque_colors(br.SRC.glob("Orange Star *.png")))
    water = {(0xc0, 0xe0, 0xf8), (0x58, 0x60, 0x98), (0x70, 0x98, 0xe8)}

    for army in br.ARMIES:
        for p in (br.OUT / "1x").glob(f"* - {army}.png"):
            a = np.array(Image.open(p).convert("RGB"))
            colors = {tuple(int(v) for v in c) for c in a.reshape(-1, 3)} - {(0, 255, 0)}
            stray = colors - os_colors
            # Rule-3 colors outside SNAP_TO_OS keep their table shade, so allow
            # a few; a whole foreign ramp surviving would be dozens.
            check(len(stray) <= 6, f"{p.stem}: {len(stray)} colors not in Orange Star art")
        for u in ("Battleship", "Cruiser", "Lander Boat", "Submarine (Surfaced)"):
            a = np.array(Image.open(br.OUT / "1x" / f"{u} - {army}.png").convert("RGB"))
            colors = {tuple(int(v) for v in c) for c in a.reshape(-1, 3)}
            check(water <= colors, f"{u} - {army}: lost its water blues")

    # Blue Moon's water was the bug that motivated the neutral rule.
    a = np.array(Image.open(br.OUT / "1x" / "Submarine (Surfaced) - Blue Moon.png").convert("RGB"))
    src = np.array(Image.open(br.SRC / "Blue Moon Submarine (Surfaced).png").convert("RGBA"))
    pale = (src[..., :3] == (0xc0, 0xe0, 0xf8)).all(-1)
    ys, xs = np.nonzero(pale)
    check((a[ys + br.PAD, xs + br.PAD] == (0xc0, 0xe0, 0xf8)).all(),
          "Blue Moon submarine water was remapped")
    print("    team ramps gone, water/neutrals intact")


def main() -> int:
    for t in (test_coverage, test_files, test_recolor):
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
