"""Tests for the Phase 5.5 sheet splitter.

The substantive tests are **round trips against real sheets with known 1x
originals**. `testdata/` holds two hand-arranged sheets -- the map units at 6x
and the chibis at 4x -- and the same two at 1x. Every piece
the splitter cuts must equal the matching crop of the 1x sheet pixel for pixel,
so a wrong scale, a wrong phase, a lost detached part or a merged neighbour
fails loudly. These are exactly the two sheets Phase 5 mangled when it ran
`detect_grid` over them whole.

Synthetic sheets then cover what those two cannot: units pasted off-grid, a
sprite whose GCD is a multiple of the true scale, JPEG damage, transparency.

Run: python tools/test_split.py
"""

from __future__ import annotations

import io
import pathlib
import sys
import tempfile
import traceback

import numpy as np
from PIL import Image

import convert
import split

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTDATA = ROOT / "testdata"
SHEETS = [
    # (upscaled sheet, 1x original, scale, unit count)
    (TESTDATA / "AW Map Units 6x Safety Green.png",
     TESTDATA / "AW Map Units 1x Safety Green.png", 6, 34),
    (TESTDATA / "AW2Style Units 4x green safety BG.png",
     TESTDATA / "AW2Style Units 1x safety green.png", 4, 20),
]

_checks = 0
_failures: list[str] = []


def check(cond: bool, label: str) -> bool:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
        print(f"  FAIL  {label}")
    return cond


def section(name: str) -> None:
    print(f"\n== {name}")


def _keyed(path: pathlib.Path) -> np.ndarray:
    a = np.array(Image.open(path).convert("RGBA"))
    a[np.all(a[..., :3] == (0, 255, 0), axis=2)] = 0
    return a


def _pieces_1x() -> list[Image.Image]:
    """Every map unit at 1x, cut from the 1x sheet -- the synthetic tests' parts."""
    res = split.split_sheet(Image.open(SHEETS[0][1]))
    return [p.result.snapped for p in res.pieces]


# --------------------------------------------------------------------------


def test_components() -> None:
    section("connected components")
    m = np.zeros((10, 12), bool)
    m[1:3, 1:3] = True          # a block
    m[3, 3] = True              # touches it diagonally -> same component
    m[6:8, 8:11] = True         # a separate block
    boxes = sorted(split.label_components(m))
    check(len(boxes) == 2, f"two 8-connected components, got {len(boxes)}")
    check(boxes[0] == (1, 1, 4, 4, 5), f"first box and area, got {boxes[0]}")
    check(boxes[1] == (8, 6, 11, 8, 6), f"second box and area, got {boxes[1]}")

    # a U shape joins through its base, found from two separate runs above
    u = np.zeros((5, 7), bool)
    u[0:4, 1] = u[0:4, 5] = True
    u[4, 1:6] = True
    check(len(split.label_components(u)) == 1, "U shape is one component")

    merged = split.merge_boxes([(0, 0, 4, 4, 10), (6, 0, 9, 4, 5), (30, 0, 34, 4, 9)], 2)
    check(len(merged) == 2, "boxes 2 px apart merge, a far box does not")
    check(sorted(merged)[0][4] == 15, "merged box sums the areas")


def test_reading_order() -> None:
    section("reading order")
    boxes = [(50, 2, 60, 12, 1), (0, 0, 10, 10, 1), (25, 30, 35, 40, 1), (0, 31, 10, 41, 1)]
    got = [b[:2] for b in split.reading_order(boxes)]
    check(got == [(0, 0), (50, 2), (0, 31), (25, 30)],
          f"row-major with ragged tops, got {got}")


def test_exact_scale() -> None:
    section("exact scale")
    piece = np.array(_pieces_1x()[3])
    for s in (1, 2, 3, 6, 7):
        big = np.array(Image.fromarray(piece).resize(
            (piece.shape[1] * s, piece.shape[0] * s), Image.NEAREST))
        ex = split.exact_scale(big)
        check(ex is not None and ex[:2] == (s, s), f"scale {s} found exactly, got {ex}")
        if ex:
            back = split._trim(split.exact_downscale(big, *ex))
            check(np.array_equal(back, piece), f"scale {s} downscales losslessly")

    rng = np.random.default_rng(0)
    big = np.array(Image.fromarray(piece).resize(
        (piece.shape[1] * 6, piece.shape[0] * 6), Image.NEAREST)).astype(np.int16)
    big[..., :3] += rng.integers(-6, 7, big[..., :3].shape).astype(np.int16)
    noisy = np.clip(big, 0, 255).astype(np.uint8)
    check(split.exact_scale(noisy) is None, "a noisy upscale has no exact grid")


def test_real_sheets() -> None:
    section("real sheets against their 1x originals")
    for big, small, s, count in SHEETS:
        res = split.split_sheet(Image.open(big))
        ref = _keyed(small)
        check(res.strict_key, f"{big.name}: exact backdrop keyed strictly")
        check(len(res.pieces) == count, f"{big.name}: {count} units, got {len(res.pieces)}")
        check(not res.dropped, f"{big.name}: nothing dropped as a speck")
        bad = []
        for p in res.pieces:
            x0, y0, x1, y1 = (v // s for v in p.box)
            if p.needs_review or p.scale != (s, s) or \
                    not np.array_equal(np.array(p.result.snapped), ref[y0:y1, x0:x1]):
                bad.append(p.index)
        check(not bad, f"{big.name}: every piece equals its 1x crop, failed {bad}")
        check(all(p.result.validation["ok"] for p in res.pieces),
              f"{big.name}: every piece validates in all teams")

        # the 1x sheet itself splits identically
        res1 = split.split_sheet(Image.open(small))
        check([tuple(v * s for v in p.box) for p in res1.pieces] == [p.box for p in res.pieces],
              f"{small.name}: 1x sheet cuts at the same places")


def test_detached_parts() -> None:
    section("detached parts stay with their unit")
    # chibi helicopters: rotors float clear of the body
    res = split.split_sheet(Image.open(SHEETS[1][0]))
    raw = split.label_components(np.array(split.key_sheet(Image.open(SHEETS[1][0]))[0])[..., 3] > 0)
    check(len(raw) > len(res.pieces), "the sheet has more raw parts than units")

    # a 1-px gap merges at the default join; a wide one does not
    a = np.zeros((20, 40, 4), np.uint8)
    a[2:10, 2:10] = (200, 30, 30, 255)
    a[11:13, 4:8] = (0, 0, 0, 255)        # shadow 1 px below
    a[2:10, 25:33] = (200, 30, 30, 255)   # another unit 15 px away
    a[4, 5] = a[6, 28] = (255, 255, 255, 255)  # 1-px detail, as real art has
    res = split.split_sheet(Image.fromarray(a))
    check(len(res.pieces) == 2, f"shadow joins its unit, neighbour stays apart, got {len(res.pieces)}")
    res = split.split_sheet(Image.fromarray(a), join=0)
    check(len(res.pieces) == 3, "join=0 keeps the shadow separate")


def test_off_grid_paste() -> None:
    section("units pasted off-grid")
    parts = _pieces_1x()
    rng = np.random.default_rng(1)
    canvas = Image.new("RGBA", (900, 760), (0, 255, 0, 255))
    x, y = 7, 5
    for s in parts:
        big = s.resize((s.width * 5, s.height * 5), Image.NEAREST)
        if x + big.width > 890:
            x, y = 7 + int(rng.integers(0, 5)), y + 100 + int(rng.integers(0, 5))
        canvas.paste(big, (x, y), big)
        x += big.width + 30 + int(rng.integers(0, 5))
    res = split.split_sheet(canvas)
    check(len(res.pieces) == len(parts), f"{len(parts)} units found, got {len(res.pieces)}")
    same = sum(np.array_equal(np.array(p.result.snapped), np.array(t))
               for p, t in zip(res.pieces, parts))
    check(same == len(parts), f"every off-grid unit recovered exactly ({same}/{len(parts)})")


def test_gcd_multiple() -> None:
    section("a sprite whose GCD is a multiple of the scale")
    parts = _pieces_1x()
    chunky = np.zeros((8, 8, 4), np.uint8)
    chunky[...] = (200, 30, 30, 255)
    chunky[2:4, 2:6] = (0, 0, 0, 255)
    chunky = Image.fromarray(chunky).resize((16, 16), Image.NEAREST)  # every run is 2 px
    canvas = Image.new("RGBA", (300, 80), (0, 255, 0, 255))
    x = 6
    for s in (parts[0], chunky, parts[5]):
        big = s.resize((s.width * 3, s.height * 3), Image.NEAREST)
        canvas.paste(big, (x, 9), big)
        x += big.width + 30
    res = split.split_sheet(canvas)
    check([p.scale for p in res.pieces] == [(3.0, 3.0)] * 3,
          f"all three at the sheet's scale, got {[p.scale for p in res.pieces]}")
    check(res.pieces[1].result.snapped.size == (16, 16), "chunky sprite keeps its 16x16")

    # forced scale overrides everything
    res = split.split_sheet(canvas, scale=3)
    check(all(p.scale == (3, 3) for p in res.pieces), "--scale is honoured")


def test_damaged_sheet() -> None:
    section("JPEG-damaged sheet falls back to detection")
    buf = io.BytesIO()
    Image.open(SHEETS[0][0]).save(buf, "JPEG", quality=85)
    res = split.split_sheet(Image.open(buf))
    check(not res.strict_key, "damaged backdrop uses the tolerant key")
    check(len(res.pieces) == SHEETS[0][3], f"{SHEETS[0][3]} units found, got {len(res.pieces)}")
    check(all(p.method == "detected" for p in res.pieces), "every piece went to detection")
    ref = _keyed(SHEETS[0][1])
    right = 0
    for p in res.pieces:
        x0, y0, x1, y1 = (round(v / 6) for v in p.box)
        if p.result.snapped is not None and p.result.snapped.size == (x1 - x0, y1 - y0):
            right += 1
    check(right >= 30, f"at least 30 of 34 come out at the right size, got {right}")
    check(res.consensus and abs(res.consensus[0] - 6) < 0.15, f"consensus near 6, got {res.consensus}")


def test_outlier_pitch_flagged() -> None:
    section("a detected pitch that disagrees with the sheet goes to review")
    parts = _pieces_1x()
    canvas = Image.new("RGB", (900, 200), (0, 255, 0))
    x = 10
    for k, s in enumerate(parts[:5]):
        f = 8 if k == 2 else 6
        big = s.resize((s.width * f, s.height * f), Image.NEAREST)
        canvas.paste(big, (x, 20), big)
        x += big.width + 40
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    res = split.split_sheet(Image.open(buf))
    flagged = [p.index for p in res.pieces if p.needs_review]
    check(flagged == [3], f"only the 8x unit is flagged, got {flagged}")


def test_transparent_and_names() -> None:
    section("transparent sheets, names, outputs")
    res = split.split_sheet(Image.open(TESTDATA / "AW Map Units 1x Transparent.png"))
    check(len(res.pieces) == 34 and not any(p.needs_review for p in res.pieces),
          "transparent 1x sheet: 34 clean units")

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        sheet = d / "army.png"
        Image.open(SHEETS[0][0]).save(sheet)
        (d / "army.names.txt").write_text("# comment\nbattleship\n-\ncruiser\n\n",
                                          encoding="utf-8")
        nf = split.names_file_for(sheet)
        check(nf is not None, "names file found beside the sheet")
        names = split.read_names(nf)
        check(names == ["battleship", "", "cruiser"], f"names parsed, got {names}")
        res = split.split_sheet(Image.open(sheet), name="army", names=names)
        got = [p.name for p in res.pieces[:4]]
        check(got == ["battleship", "army_02", "cruiser", "army_04"], f"names applied, got {got}")

        out = d / "out"
        done, flagged = split.write_outputs(res, Image.open(sheet), out)
        check(done == 34 and flagged == 0, f"34 written, got {done}/{flagged}")
        check((out / "battleship+mask.png").is_file(), "encoded sprite written with +mask")
        check((out / "rgb" / "battleship.png").is_file(), "1x RGB written to rgb/")
        check((out / "rgb" / "army.index.png").is_file(), "index image written to rgb/")
        check(all("+mask" in p.name for p in out.glob("*.png")),
              "output root holds only masks, so the previewer can load it")
        enc = Image.open(out / "battleship+mask.png")
        check(convert.validate(enc)["ok"], "written sprite validates")


def test_shared() -> None:
    section("shared module")
    check(split.convert is convert, "split uses the shared conversion module")
    src = (ROOT / "tools" / "previewer.py").read_text(encoding="utf-8")
    check("import split" in src and "split.split_sheet" in src,
          "previewer calls the shared splitter")
    check("def label_components" not in src and "def exact_scale" not in src,
          "previewer does not reimplement the splitter")


# --------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_components,
        test_reading_order,
        test_exact_scale,
        test_real_sheets,
        test_detached_parts,
        test_off_grid_paste,
        test_gcd_multiple,
        test_damaged_sheet,
        test_outlier_pitch_flagged,
        test_transparent_and_names,
        test_shared,
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
