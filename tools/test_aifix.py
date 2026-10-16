"""Tests for the AI sprite fixer (tools/aifix.py).

Two kinds of evidence.

**A synthetic sheet with known answers.** Sixteen of CoW's own battle sprites,
decoded to the Orange Star RGB an artist would draw, laid out 4x4 at a
non-integer scale on an off-key backdrop, with one drop shadow bridging two
cells, a crumb in a gutter, and JPEG damage. Every unit must come back at its
true size and match the original almost pixel for pixel -- so a wrong cut, a
merged neighbour, a lost part or a wrong offset fails loudly.

**The real AI sheets.** The layouts, unit counts and validation of every sheet
the fixer was built against. These have no 1x originals, so they pin behaviour
rather than correctness; the synthetic sheet is what proves correctness.

Run: python tools/test_aifix.py
"""

from __future__ import annotations

import io
import pathlib
import sys
import traceback

import numpy as np
from PIL import Image

import aifix
import convert
import cowpalette
import synth

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTDATA = ROOT / "testdata"
SAMPLES = ROOT / "samples" / "cow battle animations"
EXP = ROOT / "testdata" / "ai sheets"

# CoW's battle sprites with a single frame and a ground-unit footprint.
SYNTH_UNITS = ["antitankcannon", "apc", "artillerycraft", "flak+os+air", "flare",
               "heavy_hovercraft", "heavy_tank", "light_tank", "missile", "motorbike",
               "neotank", "recon", "rocketthrower", "fighter", "waterplane", "infantry"]
SYNTH_SCALE = 4.6
SYNTH_KEY = (22, 238, 26)  # what a generator calls "pure green"

# (sheet, layout, native median width) -- the sheets the fixer was built
# against. Native is one output pixel per grain step (2.84 px on the Nano
# Banana sheets, 3.4 on cars.jpeg); sheet (1) has no grain and falls back to
# aifix.NATIVE_FALLBACK_WIDTH.
REAL_SHEETS = [
    (EXP / "Nanobanana Attempts (1).jpg", (5, 4), 96),
    (EXP / "Nanobanana Attempts (2).jpg", (4, 4), 79),
    (EXP / "Nanobanana Attempts (3).jpg", (4, 4), 90),
    (EXP / "cars2.jpg", (4, 4), 86),
    (EXP / "cars.jpeg", (6, 4), 62),
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


# --------------------------------------------------------------------------
# the synthetic sheet
# --------------------------------------------------------------------------


def _originals() -> list[Image.Image]:
    """The sixteen units as an artist would author them: Orange Star RGB,
    snapped to the converter's palette so every pixel has one right answer.

    CoW's decoded sprites hold a few colors outside the 63-color palette; left
    in, "the right color" for those pixels would be a matter of opinion and
    the lossless check could not demand an exact result.
    """
    pal = convert._resolve_class(convert.ALL_CLASSES)
    out = []
    for n in SYNTH_UNITS:
        name = n if "+" in n else f"{n}+os"
        with Image.open(SAMPLES / f"{name}+mask.png") as im:
            rgb = synth.decode_to_authored(im.convert("RGBA"))
        out.append(convert.quantize_to_class(rgb.crop(rgb.getbbox()), pal))
    return out


def _synthetic_sheet(originals: list[Image.Image], jpeg: bool = True
                     ) -> tuple[Image.Image, list[tuple[int, int]]]:
    """4x4 sheet of `originals` at SYNTH_SCALE. Returns (sheet, top-left per unit)."""
    cell_w, cell_h = 400, 360
    sheet = Image.new("RGBA", (cell_w * 4, cell_h * 4), SYNTH_KEY + (255,))
    rng = np.random.default_rng(7)
    spots = []
    for i, small in enumerate(originals):
        big = small.resize((round(small.width * SYNTH_SCALE),
                            round(small.height * SYNTH_SCALE)), Image.NEAREST)
        r, c = divmod(i, 4)
        x = c * cell_w + (cell_w - big.width) // 2 + int(rng.integers(-12, 13))
        y = r * cell_h + (cell_h - big.height) // 2 + int(rng.integers(-12, 13))
        sheet.alpha_composite(big, (x, y))
        spots.append((x, y))
    a = np.array(sheet)
    # A drop shadow running from unit 2 down into unit 6's cell -- the touching
    # case that defeats a component cutter.
    x0 = spots[1][0] + 40
    a[spots[1][1] + 200:spots[5][1] + 10, x0:x0 + 6, :3] = (24, 16, 16)
    # A JPEG crumb alone in the gutter between cells 1 and 2.
    a[100:104, cell_w - 2:cell_w + 2, :3] = (200, 80, 70)
    rgb = Image.fromarray(a[..., :3], "RGB")
    if jpeg:
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=88)
        rgb = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return rgb, spots


_RENDER: dict[tuple[int, int, int], tuple] | None = None


def _render_key(rgb: tuple[int, int, int]) -> tuple:
    """What a palette color renders as in every team -- the suite's identity test.

    Two colors with the same key are interchangeable in CoW, e.g. (160,64,72)
    and (168,72,72); two with different keys are not, however close their RGB.
    """
    global _RENDER
    if _RENDER is None:
        pal = convert._resolve_class(convert.ALL_CLASSES)
        ps = cowpalette.load_palettes()
        teams = [t for t in ps.names() if t != cowpalette.MASK]
        _RENDER = {}
        for c, (r, g) in zip(pal.colors.tolist(), pal.coords.tolist()):
            _RENDER[tuple(c)] = tuple(tuple(int(v) for v in ps[t].table[g, r]) for t in teams)
    return _RENDER[rgb]


def _agreement(got: Image.Image, want: Image.Image) -> tuple[float, float]:
    """(silhouette, color) agreement at the best +-1 px alignment.

    Silhouette: share of the opaque union where both agree on opacity.
    Color: share of the pixels opaque in both that render the same in all 32
    teams. Kept apart because they fail for different reasons -- a wrong cut
    or offset breaks the first, JPEG color damage the second.
    """
    g = np.array(got.convert("RGBA"))
    w = np.array(want.convert("RGBA"))
    h = max(g.shape[0], w.shape[0]) + 2
    wd = max(g.shape[1], w.shape[1]) + 2
    W = np.zeros((h, wd, 4), np.uint8)
    W[1:1 + w.shape[0], 1:1 + w.shape[1]] = w
    best = (0.0, 0.0)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            G = np.zeros((h, wd, 4), np.uint8)
            G[dy:dy + g.shape[0], dx:dx + g.shape[1]] = g
            go, wo = G[..., 3] > 0, W[..., 3] > 0
            sil = float((go == wo)[go | wo].mean())
            both = go & wo
            col = 0.0
            if both.any():
                same = [_render_key(tuple(a)) == _render_key(tuple(b))
                        for a, b in zip(G[both][:, :3].tolist(), W[both][:, :3].tolist())]
                col = float(np.mean(same))
            if sil + col > sum(best):
                best = (sil, col)
    return best


# Units 2 and 6 carry the bridging shadow, so they are not pixel-comparable.
BRIDGED = {2, 6}

# Color on the JPEG sheet, as share of pixels that render right in all 32
# teams. Geometry is exact either way; JPEG costs color. Plain nearest-RGB
# scores 83% mean. aifix: 92.8% (worst unit 80%) with the quantizer alone,
# 92.0% (75%) adding the team lock, which repaints real gold trim, and 93.5%
# (81%) adding speck merging, which folds JPEG-shifted specks back.
JPEG_COLOR_MEAN = 0.92
JPEG_COLOR_FLOOR = 0.75
# The quantizer must beat nearest-RGB by at least this much on the JPEG sheet.
QUANTIZER_GAIN = 0.07


def _check_units(prepared: aifix.Sheet, originals: list[Image.Image],
                 color_floor: float, color_mean: float = 0.0) -> float:
    """Check every unit against its original; returns the mean color agreement."""
    pal = convert._resolve_class(convert.ALL_CLASSES)
    sils, cols = [], []
    for u, orig in zip(prepared.units, originals):
        r = u.result
        if not check(r is not None and r.ok, f"{u.name} did not convert/validate"):
            continue
        if u.index in BRIDGED:
            # Found in its own cell, not merged with its neighbour: its width
            # is its own (a merge would take the wider unit's width).
            check(abs(r.quantized.width - orig.width) <= 1,
                  f"{u.name} is {r.quantized.width} wide, want {orig.width}: merged?")
            continue
        sw, sh = r.quantized.size
        check(abs(sw - orig.width) <= 1 and abs(sh - orig.height) <= 1,
              f"{u.name} came out {sw}x{sh}, want {orig.width}x{orig.height}")
        sil, col = _agreement(r.quantized, orig)
        sils.append(sil)
        cols.append(col)
        check(sil >= 0.98, f"{u.name} silhouette matches at {sil:.0%}, want >= 98%")
        check(col >= color_floor, f"{u.name} colors match at {col:.0%}, want >= {color_floor:.0%}")
    mean = float(np.mean(cols))
    check(mean >= color_mean, f"mean color agreement {mean:.1%}, want >= {color_mean:.0%}")
    print(f"  silhouette worst {min(sils):.1%}; color mean {mean:.1%}, worst {min(cols):.1%}")
    return mean


def _nearest_rgb_mean(prepared: aifix.Sheet, originals: list[Image.Image]) -> float:
    """The same color score for the pipeline's plain nearest-RGB quantizer."""
    pal = convert._resolve_class(convert.ALL_CLASSES)
    cols = []
    for u, orig in zip(prepared.units, originals):
        if u.index in BRIDGED or not u.result:
            continue
        q = convert.quantize_to_class(u.result.snapped, pal)
        cols.append(_agreement(q, orig)[1])
    return float(np.mean(cols))


def test_synthetic() -> None:
    originals = _originals()

    section("synthetic 4x4 sheet, scale 4.6, lossless")
    sheet, _ = _synthetic_sheet(originals, jpeg=False)
    prepared = aifix.prepare_sheet(sheet, name="syn")
    aifix.snap_sheet(prepared, SYNTH_SCALE)
    _check_units(prepared, originals, color_floor=0.95, color_mean=0.99)

    section("synthetic 4x4 sheet, scale 4.6, JPEG, off-key backdrop")
    sheet, _ = _synthetic_sheet(originals)

    key = aifix.sample_key(sheet)
    check(max(abs(a - b) for a, b in zip(key, SYNTH_KEY)) <= 3,
          f"backdrop sampled as {key}, want ~{SYNTH_KEY}")

    prepared = aifix.prepare_sheet(sheet, name="syn")
    check(prepared.layout == (4, 4), f"layout guessed {prepared.layout}, want (4, 4)")
    check(len(prepared.units) == 16, f"{len(prepared.units)} units, want 16")
    check(prepared.grain is not None and abs(prepared.grain - SYNTH_SCALE) < 0.1,
          f"grain {prepared.grain}, want ~{SYNTH_SCALE}")

    aifix.snap_sheet(prepared, SYNTH_SCALE)
    ours = _check_units(prepared, originals, JPEG_COLOR_FLOOR, JPEG_COLOR_MEAN)
    base = _nearest_rgb_mean(prepared, originals)
    check(ours - base >= QUANTIZER_GAIN,
          f"quantizer {ours:.1%} vs nearest-RGB {base:.1%}: gain under {QUANTIZER_GAIN:.0%}")
    print(f"  nearest-RGB on the same snaps: {base:.1%}")

    section("even out on art that was already right (the cost)")
    aifix.snap_sheet(prepared, SYNTH_SCALE, even=1.0)
    _check_units(prepared, originals, color_floor=0.0, color_mean=0.72)
    # Per unit: evening out unit 1 moves unit 1. The others may ripple a
    # little -- the usage prior is pooled over the sheet, so unit 1's new
    # colors can tip a near tie elsewhere -- but only a little.
    aifix.snap_sheet(prepared, SYNTH_SCALE, even={1: 1.0})
    plain = aifix.snap_sheet(aifix.prepare_sheet(sheet, name="syn"), SYNTH_SCALE)
    changed = lambda a, b: float(np.any(np.array(a.result.quantized) != np.array(b.result.quantized),
                                        axis=2).mean())
    own = changed(prepared.units[0], plain.units[0])
    ripple = max(changed(a, b) for a, b in zip(prepared.units[1:], plain.units[1:]))
    check(own >= 5 * max(ripple, 0.002),
          f"evening out unit 1 changed it {own:.1%}, others up to {ripple:.1%}")
    check(ripple <= 0.02, f"evening out unit 1 changed {ripple:.1%} of another unit")

    # The default size is native: the grain, which here is the true scale, so
    # every unit comes back at its original size (checked above at 4.6).
    check(abs(prepared.native_pitch - SYNTH_SCALE) < 0.1,
          f"native pitch {prepared.native_pitch:.2f}, want the grain ~{SYNTH_SCALE}")
    want = float(np.median([o.width for o in originals]))
    check(abs(prepared.native_width - want) <= 1.5,
          f"native width {prepared.native_width}, want the originals' median {want}")
    # a smaller size from the slider hits its target width
    aifix.snap_sheet(prepared, prepared.pitch_for_width(aifix.COW_BATTLE_WIDTH))
    widths = [u.size[0] for u in prepared.units if u.size]
    med = float(np.median(widths))
    check(abs(med - aifix.COW_BATTLE_WIDTH) <= 1.5,
          f"median width {med} at width {aifix.COW_BATTLE_WIDTH}, want ~{aifix.COW_BATTLE_WIDTH}")
    check(prepared.pitch >= prepared.min_pitch, "pitch went below the grain")
    # asking for more than native is clamped to the grain
    check(prepared.pitch_for_width(10 * want) == prepared.min_pitch,
          "a width above native went below the grain")


# rows x cols layouts beyond the 4x4 above; 5x4 is covered by the real sheets
# (Nanobanana Attempts (1), the hand-made AW2 sheet)
EXTRA_LAYOUTS = [(1, 1), (2, 2), (3, 4), (4, 5)]


def _layout_sheet(originals: list[Image.Image], rows: int, cols: int, start: int
                  ) -> tuple[Image.Image, list[Image.Image]]:
    """A rows x cols JPEG sheet like the 4x4 one, units taken from `originals`
    in turn from `start`. Returns (sheet, the unit placed in each cell)."""
    cell_w, cell_h = 400, 360
    sheet = Image.new("RGBA", (cell_w * cols, cell_h * rows), SYNTH_KEY + (255,))
    rng = np.random.default_rng(rows * 10 + cols)
    placed = []
    for i in range(rows * cols):
        small = originals[(start + i) % len(originals)]
        big = small.resize((round(small.width * SYNTH_SCALE),
                            round(small.height * SYNTH_SCALE)), Image.NEAREST)
        r, c = divmod(i, cols)
        x = c * cell_w + (cell_w - big.width) // 2 + int(rng.integers(-12, 13))
        y = r * cell_h + (cell_h - big.height) // 2 + int(rng.integers(-12, 13))
        sheet.alpha_composite(big, (x, y))
        placed.append(small)
    buf = io.BytesIO()
    sheet.convert("RGB").save(buf, "JPEG", quality=88)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), placed


def test_layouts() -> None:
    originals = _originals()
    for rows, cols in EXTRA_LAYOUTS:
        section(f"synthetic {rows}x{cols} sheet")
        sheet, placed = _layout_sheet(originals, rows, cols, start=rows + cols)
        prepared = aifix.prepare_sheet(sheet, name=f"syn{rows}x{cols}")
        check(prepared.layout == (rows, cols),
              f"{rows}x{cols}: layout guessed {prepared.layout}")
        check(len(prepared.units) == rows * cols,
              f"{rows}x{cols}: {len(prepared.units)} units, want {rows * cols}")
        check(prepared.grain is not None and abs(prepared.grain - SYNTH_SCALE) < 0.1,
              f"{rows}x{cols}: grain {prepared.grain}, want ~{SYNTH_SCALE}")
        # At the true scale, so a size error means a bad cut. (The grain reads
        # 4.54 here; native would put the 75 px units 1-2 px over.)
        aifix.snap_sheet(prepared, SYNTH_SCALE)
        off = []
        for u, orig in zip(prepared.units, placed):
            r = u.result
            if not check(r is not None and r.ok, f"{u.name} did not convert/validate"):
                continue
            sw, sh = r.quantized.size
            if abs(sw - orig.width) > 1 or abs(sh - orig.height) > 1:
                off.append(f"{u.name} {sw}x{sh} vs {orig.width}x{orig.height}")
        check(not off, f"{rows}x{cols}: wrong sizes: {off}")
        g = f"{prepared.grain:.2f}" if prepared.grain else "none"
        print(f"  layout {prepared.layout}, grain {g}, {rows * cols} units at their true size"
              if not off else f"  {len(off)} off")

    # The override: the same 3x4 sheet read as 3x2 must give six cells, and
    # a guess is never forced on the caller.
    sheet, _ = _layout_sheet(originals, 3, 4, start=7)
    forced = aifix.prepare_sheet(sheet, layout=(3, 2))
    check(forced.layout == (3, 2) and len(forced.units) == 6,
          f"layout override gave {forced.layout} with {len(forced.units)} units")


def test_write_outputs() -> None:
    section("export: keep/reject, army PNGs, Nx upscale")
    import tempfile
    originals = _originals()
    sheet, placed = _layout_sheet(originals, 2, 2, start=0)
    prepared = aifix.snap_sheet(aifix.prepare_sheet(sheet, name="exp"), SYNTH_SCALE)
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        n = aifix.write_outputs(prepared, out, keep={1, 3, 4},
                                teams=["blue_moon", "green_earth"], scale=3)
        check(n == 3, f"wrote {n} units, want the 3 kept")
        masks = sorted(p.name for p in out.glob("*+mask.png"))
        check(masks == ["exp_01+mask.png", "exp_03+mask.png", "exp_04+mask.png"],
              f"mask files {masks}")
        armies = sorted(p.name for p in (out / "armies 3x").glob("*.png"))
        check(len(armies) == 6 and "exp_01+blue_moon.png" in armies,
              f"army files {armies}")
        u = prepared.units[0]
        with Image.open(out / "armies 3x" / "exp_01+green_earth.png") as im:
            check(im.size == (u.size[0] * 3, u.size[1] * 3),
                  f"3x export is {im.size}, unit is {u.size}")
            # exactly a 3x nearest-neighbour blow-up of CoW's own rendering
            want = cowpalette.recolor(u.result.encoded, "green_earth")
            check(np.array_equal(np.array(im.convert("RGBA"))[::3, ::3], np.array(want)),
                  "3x export is not a nearest-neighbour blow-up of the recolor")
        with Image.open(out / "exp_01+mask.png") as im:
            check(im.size == u.size, "the +mask.png is not 1x")
        # 1x goes to armies/
        aifix.write_outputs(prepared, out, teams=["red_fire"])
        check((out / "armies" / "exp_02+red_fire.png").is_file(), "1x armies/ not written")
        # Rejecting a unit later sets its earlier export aside, so the previewer
        # (which lists the top level) stops showing it -- and nothing is deleted.
        aifix.write_outputs(prepared, out, keep={1, 2, 3}, teams=["blue_moon"], scale=3)
        check(not (out / "exp_04+mask.png").exists(), "a rejected unit's +mask.png stayed")
        for f in ("exp_04+mask.png", "rgb/exp_04.png", "armies 3x/exp_04+blue_moon.png",
                  "armies/exp_04+red_fire.png"):
            check((out / "rejected" / f).is_file(), f"rejected/{f} was not set aside")
        check((out / "exp_03+mask.png").is_file(), "a kept unit was set aside")


def _code_only(path: pathlib.Path) -> str:
    """Source without comments and strings, so checks read code, not prose."""
    import tokenize
    toks = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    return " ".join(t.string for t in toks if t.type not in (tokenize.COMMENT, tokenize.STRING))


def test_gui_holds_no_logic() -> None:
    section("GUI is a shell over aifix.py")
    path = ROOT / "tools" / "aifix_gui.py"
    src = path.read_text(encoding="utf-8")
    code = _code_only(path)
    names = set(code.split())
    check("import aifix" in src, "aifix_gui.py does not import the core")
    for fn in ("prepare_sheet", "snap_sheet", "write_outputs"):
        check(f"aifix.{fn}(" in src, f"aifix_gui.py does not call aifix.{fn}")
    # The steps a hurried edit would most plausibly inline into the GUI, by
    # name: none may appear in its code at all, called or defined.
    for step in ("key_sheet", "find_cuts", "guess_layout", "isolate", "measure_grain",
                 "snap_unit", "pixel_snap", "quantize_pixels", "quantize_to_class",
                 "even_out", "merge_specks", "encode", "validate", "upscale", "save"):
        check(step not in names, f"aifix_gui.py does its own {step}")
    # Re-snapping takes seconds: the core runs off the UI thread.
    check("threading.Thread(" in src, "aifix_gui.py runs the core on the UI thread")
    # Esc leaves a field and never quits, as in the other two programs.
    check('self.bind("<Escape>", lambda e: self.focus_set())' in src,
          "Esc does not hand focus back to the window")
    check("quit" not in names and src.count(".destroy()") == src.count("card.frame.destroy()"),
          "the GUI has a quit/destroy action besides rebuilding result cards")
    # its own theme: not the previewer's pydata, not the generator's gruvbox
    check('"everforest-dark"' in src and "themename=DARK_THEME" in src,
          "GUI does not open in the dark everforest theme")
    check('gk.setup(self, "icon_aifix"' in src, "GUI does not use its own icon")
    check("gk.credit_label" in src and "gk.guide_button" in src and "gk.theme_button" in src,
          "GUI is missing the shared chrome")

    # The core stays GUI-free and imports headlessly.
    core = set(_code_only(ROOT / "tools" / "aifix.py").split())
    check("tkinter" not in core and "ttkbootstrap" not in core, "aifix.py imports a GUI toolkit")
    import subprocess
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import aifix;"
         "assert 'tkinter' not in sys.modules and 'ttkbootstrap' not in sys.modules;"
         "print('ok')" % str(ROOT / "tools")],
        capture_output=True, text=True)
    check(probe.returncode == 0 and probe.stdout.strip() == "ok",
          f"importing aifix.py headlessly failed: {probe.stderr.strip()[:200]}")
    # Data only through approot, like every other module (aifix reads none itself).
    check("__file__" not in core, "aifix.py derives a path from __file__")
    check("from approot import ROOT" in src, "aifix_gui.py does not take ROOT from approot")


def _body_luma(img: Image.Image) -> float | None:
    a = np.array(img.convert("RGBA"))
    px = a[a[..., 3] > 0][:, :3].astype(float)
    y = aifix._ycc(px)
    m = aifix._body_mask(y)
    return float(np.median(y[m, 0])) if m.sum() >= 20 else None


def test_even_out_on_drift() -> None:
    section("even out on a drifted sheet (cars2: red and peach SUVs)")
    path = EXP / "cars2.jpg"
    if not path.is_file():
        print(f"  skip  {path.name} (not present)")
        return
    with Image.open(path) as im:
        prepared = aifix.prepare_sheet(im.convert("RGB"))
    pitch = prepared.native_pitch
    spread = {}
    for k in (0.0, 1.0):
        aifix.snap_sheet(prepared, pitch, even=k)
        lum = [v for v in (_body_luma(u.result.quantized) for u in prepared.units) if v]
        spread[k] = float(np.std(lum))
        check(all(u.result.ok for u in prepared.units), f"even={k}: a unit failed to validate")
    print(f"  body luma spread across units: off {spread[0.0]:.1f}, on {spread[1.0]:.1f}")
    check(spread[1.0] <= 0.6 * spread[0.0],
          f"evening out left the units' tones {spread[1.0]:.1f} apart (was {spread[0.0]:.1f})")


def test_isolate() -> None:
    section("isolate")
    a = np.zeros((60, 60, 4), np.uint8)
    a[10:40, 10:40] = (200, 80, 70, 255)     # the unit
    a[10:12, 43:45] = (90, 90, 90, 255)      # antenna tip, 3 px off: small but near
    a[55:58, 55:58] = (200, 80, 70, 255)     # speck far away
    out = aifix.isolate(a, join=4, min_area=20)
    check(out[10, 43, 3] == 255, "a small part next to the unit was dropped")
    check(out[56, 56, 3] == 0, "a far speck was kept")
    check(out[20, 20, 3] == 255, "the unit itself was cleared")

    # a chain of small crumbs must not walk the reach out to a far part
    b = np.zeros((40, 100, 4), np.uint8)
    b[10:30, 5:25] = (200, 80, 70, 255)
    for x in range(28, 70, 3):                # 1-px crumbs, each 2 px from the last
        b[20, x] = (200, 80, 70, 255)
    b[10:30, 80:95] = (200, 80, 70, 255)     # a neighbour's corner
    out = aifix.isolate(b, join=3, min_area=20)
    check(out[20, 85, 3] == 0, "crumbs chained isolation out to a neighbour")


def test_key() -> None:
    section("key and despill")
    a = np.zeros((40, 40, 3), np.uint8)
    a[:] = SYNTH_KEY
    a[10:30, 10:30] = (220, 90, 70)
    a[10:30, 10] = (150, 190, 60)             # green-tinted edge column
    out = aifix.key_sheet(Image.fromarray(a, "RGB"))
    check(out[0, 0, 3] == 0, "backdrop not keyed out")
    check(out[20, 20, 3] == 255 and tuple(out[20, 20, :3]) == (220, 90, 70),
          "interior art changed by the key")
    edge = out[20, 10]
    check(edge[3] == 0 or edge[1] <= max(edge[0], edge[2]),
          f"edge pixel kept its green spill: {tuple(edge)}")


def _leak(sheet: aifix.Sheet) -> float:
    """Share of body-colored pixels on palette cells that do not follow the team."""
    pal = convert._resolve_class(convert.ALL_CLASSES)
    team = aifix.team_cells(pal)
    index = {tuple(c): i for i, c in enumerate(pal.colors.tolist())}
    tot = leak = 0
    for u in sheet.units:
        a = np.array(u.result.quantized)
        px = a[a[..., 3] > 0][:, :3]
        body = px[aifix._body_mask(aifix._ycc(px))]
        ii = np.array([index[tuple(c)] for c in body.tolist()], dtype=int)
        tot += ii.size
        leak += int((~team[ii]).sum())
    return leak / max(1, tot)


def test_team_lock() -> None:
    section("team lock: no orange patches in other armies")
    path = EXP / "Nanobanana Attempts (3).jpg"
    if not path.is_file():
        print(f"  skip  {path.name} (not present)")
        return
    with Image.open(path) as im:
        prepared = aifix.prepare_sheet(im.convert("RGB"))
    pitch = prepared.native_pitch
    off = _leak(aifix.snap_sheet(prepared, pitch, team_lock=False))
    on = _leak(aifix.snap_sheet(prepared, pitch))
    print(f"  body on non-team cells: lock off {off:.1%}, on {on:.1%} "
          f"(CoW's own sprites: 3.6%)")
    check(on <= 0.01, f"team lock left {on:.1%} of the body off the team cells")
    check(off > 0.05, "the leak this lock exists for no longer shows -- recheck it")

    # exact palette art keeps its non-team accents (the lossless guarantee)
    pal = convert._resolve_class(convert.ALL_CLASSES)
    gold = pal.colors[[tuple(c) for c in pal.colors.tolist()].index((240, 176, 88))]
    picks = aifix.quantize_pixels(np.array([gold] * 5), pal)
    check(all(tuple(pal.colors[i]) == (240, 176, 88) for i in picks),
          "the team lock repainted an exact gold accent")


def test_merge_specks() -> None:
    section("speck merging")
    pal = convert._resolve_class(convert.ALL_CLASSES)
    body, twin, rivet = (240, 72, 72), (232, 72, 64), (248, 248, 248)
    a = np.zeros((12, 12, 4), np.uint8)
    a[1:11, 1:11] = body + (255,)
    a[3, 3] = twin + (255,)                  # a speck one ramp step off
    a[7, 7] = rivet + (255,)                 # a real highlight: high contrast
    a[5:7, 9:11] = (0, 0, 0, 255)            # a dark 4-px detail
    out = aifix.merge_specks(a)
    check(tuple(out[3, 3, :3]) == body, "a near-twin speck was not merged")
    check(tuple(out[7, 7, :3]) == rivet, "a contrasting highlight was merged away")
    check(tuple(out[5, 9, :3]) == (0, 0, 0), "a 4-px dark detail was merged")
    check(np.array_equal(out[..., 3], a[..., 3]), "merging changed the silhouette")
    check(np.array_equal(aifix.merge_specks(a, smooth=0), a), "smooth=0 still merged")

    # CoW's own battle sprites: merging must leave them nearly untouched
    changed = total = 0
    for name in SYNTH_UNITS:
        nm = name if "+" in name else f"{name}+os"
        with Image.open(SAMPLES / f"{nm}+mask.png") as im:
            q = np.array(convert.quantize_to_class(synth.decode_to_authored(im.convert("RGBA")), pal))
        m = aifix.merge_specks(q)
        changed += int(np.any(m != q, axis=2).sum())
        total += int((q[..., 3] > 0).sum())
    share = changed / total
    print(f"  CoW's own sprites changed by merging: {share:.2%}")
    check(share <= 0.005, f"merging changed {share:.2%} of CoW's own sprites")


def test_grain_on_real_pixel_art() -> None:
    section("grain on hand-made pixel art")
    sheet = TESTDATA / "AW2Style Units 4x green safety BG.png"
    with Image.open(sheet) as im:
        prepared = aifix.prepare_sheet(im.convert("RGB"))
    check(prepared.layout == (5, 4), f"layout {prepared.layout}, want (5, 4)")
    check(prepared.grain is not None and abs(prepared.grain - 4.0) < 0.05,
          f"grain {prepared.grain} on an exact 4x sheet")


def test_real_sheets() -> None:
    section("real AI sheets")
    for path, layout, native in REAL_SHEETS:
        if not path.is_file():
            print(f"  skip  {path.name} (not present)")
            continue
        with Image.open(path) as im:
            sheet = aifix.fix_sheet(im.convert("RGB"), name=path.stem)
        n = layout[0] * layout[1]
        check(sheet.layout == layout, f"{path.name}: layout {sheet.layout}, want {layout}")
        ok = sum(1 for u in sheet.units if u.result and u.result.ok)
        check(ok == n, f"{path.name}: {ok}/{n} units converted and validated")
        # the default is native: snapped at the grain, never finer
        if sheet.grain:
            check(sheet.pitch == sheet.grain,
                  f"{path.name}: default pitch {sheet.pitch:.2f}, not the grain {sheet.grain:.2f}")
        widths = [u.size[0] for u in sheet.units if u.size]
        med = float(np.median(widths)) if widths else 0
        check(abs(med - native) <= 2, f"{path.name}: native median width {med}, want ~{native}")
        check(abs(sheet.native_width - med) <= 2,
              f"{path.name}: native_width says {sheet.native_width}, the units are {med}")
        g = f"{sheet.grain:.2f}" if sheet.grain else "none"
        print(f"  {path.name}: {sheet.layout[0]}x{sheet.layout[1]}, grain {g}, "
              f"pitch {sheet.pitch:.2f}, median width {med:g}, {ok}/{n} ok")
    # Sheet (1) has no grain at all; the others do.
    p1 = REAL_SHEETS[0][0]
    if p1.is_file():
        with Image.open(p1) as im:
            check(aifix.prepare_sheet(im.convert("RGB")).grain is None,
                  "Nanobanana Attempts (1) reported a grain it does not have")


def main() -> int:
    for t in (test_isolate, test_key, test_synthetic, test_even_out_on_drift, test_team_lock,
              test_merge_specks, test_layouts, test_write_outputs, test_gui_holds_no_logic,
              test_grain_on_real_pixel_art,
              test_real_sheets):
        try:
            t()
        except Exception:
            _failures.append(f"{t.__name__} raised")
            traceback.print_exc()
    print(f"\n{_checks} checks, {len(_failures)} failed")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
