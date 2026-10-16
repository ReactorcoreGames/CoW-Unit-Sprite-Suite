"""Round-trip tests for tools/cowpalette.py against real shipped sprites.

Ground truth is `samples/`, Commander Wars' own map-unit and battle-animation sprites.

The assertion that matters most is test_every_pixel_defined: under the correct
rule, every (red, green) coordinate in every shipped sprite lands on an OPAQUE
cell of every team table -- 100%, no exceptions. An earlier row-0-only reading
of the format scored 7.3% here, so this check is what separates the two.

Run:  python tools/test_cowpalette.py
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cowpalette as cp  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLES = ROOT / "reference" / "colortables"
MAP_UNITS = ROOT / "samples" / "cow map units"
BATTLEANIM = ROOT / "samples" / "cow battle animations"

TEAMS = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole", "neutral"]

_failures: list[str] = []
_checks = 0


def check(cond: bool, what: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(what)
        print(f"  FAIL  {what}")


def reference_recolor(path: pathlib.Path, team: str) -> np.ndarray:
    """Transcription of SpriteCreator::recolorImageWithTable, per pixel.

        qint32 red   = pixel.red();
        qint32 green = pixel.green();
        targetImage.setPixel(x, y, colorTable.pixel(red, green));

    Written from the C++ rather than from cowpalette, so agreement is evidence.
    """
    table = np.array(Image.open(TABLES / f"{team}.png").convert("RGBA"))
    src = np.array(Image.open(path).convert("RGBA"))
    out = np.zeros_like(src)
    h, w, _ = src.shape
    for y in range(h):
        for x in range(w):
            px = src[y, x]
            if px[3] == 0:
                continue
            out[y, x] = table[int(px[1]), int(px[0])]   # [green, red]
            out[y, x, 3] = px[3]
    return out


# ---------------------------------------------------------------- palettes


def test_palette_load() -> None:
    print("palette load")
    pals = cp.load_palettes()
    check(len(pals) == 33, f"33 colortables (got {len(pals)})")
    check("Color_Codes" not in pals, "Color_Codes legend excluded")
    check("mask" in pals, "mask table present")
    check(pals.team_names()[:5] == [
        "orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole",
    ], "classic armies lead the team list")
    check(pals.team_names()[-1] == "mask", "mask sorts last as a render target")

    for name in TEAMS:
        check(pals[name].table.shape == (256, 256, 4), f"{name}: 256x256x4")

    # The 2x2 quadrant layout, with a one-pixel gutter at row/col 128.
    check(cp.quadrant_of(1, 1) == "ui", "quadrant: ui")
    check(cp.quadrant_of(130, 1) == "tertiary", "quadrant: tertiary")
    check(cp.quadrant_of(1, 130) == "secondary", "quadrant: secondary")
    check(cp.quadrant_of(130, 130) == "primary", "quadrant: primary")
    check(cp.quadrant_of(128, 5) is None and cp.quadrant_of(5, 128) is None,
          "row/col 128 is a gutter")

    # Row/col 128 carries no swatch data. It is not wholly blank -- the pixel
    # label text in the upper quadrants overlaps it with black -- so the check
    # that matters is that no sprite ever addresses the gutter (below, in
    # test_every_pixel_defined), and that no *color* swatch lives there.
    t = pals["orange_star"].table
    gutter = np.concatenate([t[128, :, :3], t[:, 128, :3]])
    live = np.concatenate([t[128, :, 3], t[:, 128, 3]]) > 0
    check(not live.any() or (gutter[live] == 0).all(),
          "row/col 128 holds no color swatches (only label text, if anything)")

    # Swatches are 3x3, so a defined cell's neighbours match it.
    prim = pals["orange_star"].table[132, 129:132]
    check(len({tuple(c) for c in prim}) == 1, "a 3x3 swatch is uniform across its width")


# ---------------------------------------------------------------- the rule


def test_recolor_matches_reference() -> None:
    print("recolor vs. per-pixel transcription of recolorImageWithTable")
    srcs = sorted(MAP_UNITS.glob("*.png"))
    check(len(srcs) == 55, f"55 map-unit sprites (got {len(srcs)})")
    sample = srcs[:6] + sorted(BATTLEANIM.glob("*.png"))[:4]

    for path in sample:
        im = Image.open(path)
        for team in ("orange_star", "blue_moon", "black_hole"):
            fast = np.array(cp.recolor(im, team))
            slow = reference_recolor(path, team)
            check(np.array_equal(fast, slow), f"{path.name} / {team}: vectorized == reference")


def test_every_pixel_defined() -> None:
    """The discriminating check: 100% of sprite pixels hit a defined cell.

    This is what a row-0-only reading of the format cannot do -- it reaches only
    the handful of coordinates that happen to sit on row 0 (7.3% of pixels).
    """
    print("every sprite pixel lands on a defined table cell")
    pals = cp.load_palettes()

    for label, folder, want_px in (
        ("map units", MAP_UNITS, 92754),
        ("battleanim", BATTLEANIM, 229006),
    ):
        total = 0
        coords: set[tuple[int, int]] = set()
        undefined = {t: 0 for t in TEAMS}
        for path in sorted(folder.glob("*.png")):
            im = Image.open(path)
            hist = cp.coord_histogram(im)
            coords |= set(hist)
            total += sum(hist.values())
            for t in TEAMS:
                undefined[t] += int(cp.undefined_mask(im, t, pals).sum())
        check(total == want_px, f"{label}: {want_px:,} opaque px (got {total:,})")
        for t in TEAMS:
            check(undefined[t] == 0,
                  f"{label} / {t}: 0 undefined px (got {undefined[t]:,})")

        # Greens really are row selectors spread across the quadrants.
        greens = sorted({g for _, g in coords})
        check(any(g < 128 for g in greens) and any(g > 128 for g in greens),
              f"{label}: uses both upper and lower quadrants (greens {greens[:6]}...)")
        check(all(cp.quadrant_of(r, g) is not None for r, g in coords),
              f"{label}: no coordinate sits in the gutter")


def test_recolor_invariants() -> None:
    print("recolor invariants")
    pals = cp.load_palettes()
    im = Image.open(MAP_UNITS / "light_tank+mask.png")
    src = np.array(im.convert("RGBA"))

    for team in TEAMS:
        out = np.array(cp.recolor(im, team))
        check(out.shape == src.shape, f"{team}: shape preserved")
        check(np.array_equal(out[:, :, 3], src[:, :, 3]), f"{team}: alpha preserved")
        check((out[src[:, :, 3] == 0] == 0).all(), f"{team}: transparent px stay clear")

        # Every opaque pixel took exactly table[green, red].
        op = src[:, :, 3] > 0
        want = pals[team].table[src[:, :, 1][op].astype(int), src[:, :, 0][op].astype(int)]
        check(np.array_equal(out[op][:, :3], want[:, :3]),
              f"{team}: pixels took table[green, red]")

    a = np.array(cp.recolor(im, "orange_star"))
    b = np.array(cp.recolor(im, "blue_moon"))
    check(not np.array_equal(a, b), "different teams give different output")
    check(not np.array_equal(a, src), "recolor changes the sprite")

    # Recoloring is a pure function of the source, so it is repeatable and any
    # team can be produced from the original at any time.
    check(np.array_equal(np.array(cp.recolor(im, "orange_star")), a), "deterministic")


def test_recolor_is_plausible_art() -> None:
    """Sanity checks that the output looks like a real unit, not mush.

    The row-0 bug produced near-identical muddy sprites across teams; these
    assertions fail loudly in that case.
    """
    print("output is plausible team art")
    im = Image.open(MAP_UNITS / "light_tank+mask.png")
    n = im.height
    frame = im.crop((0, 0, n, n))

    renders = {t: np.array(cp.recolor(frame, t)) for t in
               ("orange_star", "blue_moon", "green_earth", "yellow_comet")}

    def dominant(a):
        op = a[:, :, 3] > 0
        px = a[op][:, :3].astype(int)
        return px.mean(axis=0)

    os_, bm, ge, yc = (dominant(renders[t]) for t in
                       ("orange_star", "blue_moon", "green_earth", "yellow_comet"))
    check(os_[0] > os_[2] + 25, f"orange_star reads red-dominant {os_.round()}")
    check(bm[2] > bm[0] + 25, f"blue_moon reads blue-dominant {bm.round()}")
    check(ge[1] > ge[0] + 15, f"green_earth reads green-dominant {ge.round()}")
    check(yc[0] > yc[2] + 30 and yc[1] > yc[2] + 30, f"yellow_comet reads yellow {yc.round()}")

    # Teams must differ substantially, not by a few tread pixels.
    diff = (renders["orange_star"][:, :, :3].astype(int)
            - renders["blue_moon"][:, :, :3].astype(int))
    op = renders["orange_star"][:, :, 3] > 0
    changed = (np.abs(diff).sum(axis=2) > 30)[op].mean()
    check(changed > 0.5, f"most opaque px differ between teams (got {changed:.0%})")

    # Neutral greys (treads) should survive as near-grey in every team.
    for t, a in renders.items():
        px = a[a[:, :, 3] > 0][:, :3].astype(int)
        spread = px.max(axis=1) - px.min(axis=1)
        check((spread < 12).any(), f"{t}: retains some near-neutral pixels")


def test_mask_round_trip() -> None:
    """Rendering to `mask` reproduces the sprite's own authoring colors."""
    print("mask round trip")
    for path in sorted(MAP_UNITS.glob("*.png"))[:10]:
        im = Image.open(path).convert("RGBA")
        back = np.array(cp.recolor(im, "mask"))
        src = np.array(im)
        op = src[:, :, 3] > 0
        check(np.array_equal(back[:, :, 3], src[:, :, 3]), f"{path.name}: alpha kept")
        # mask.png's cells hold the encoded source values, so red survives.
        check(np.array_equal(back[op][:, 0], src[op][:, 0]),
              f"{path.name}: red channel round-trips through mask")


# ---------------------------------------------------------------- quantize


def test_quantize() -> None:
    print("quantizer")
    pal_cols = cp.palette_colors("orange_star", "primary")
    check(len(pal_cols) > 8, f"orange_star primary has a real palette ({len(pal_cols)} colors)")

    pal = pal_cols[:16]
    src = Image.new("RGBA", (len(pal), 1))
    for i, c in enumerate(pal):
        src.putpixel((i, 0), tuple(c) + (255,))
    out = np.array(cp.quantize(src, pal))
    check(all(tuple(out[0, i, :3]) == tuple(pal[i]) for i in range(len(pal))),
          "palette colors are fixed points")

    c0 = pal[0]
    near = Image.new("RGBA", (1, 1), (min(255, c0[0] + 3), c0[1], max(0, c0[2] - 2), 255))
    check(tuple(np.array(cp.quantize(near, pal))[0, 0, :3]) == tuple(c0),
          "near-miss snaps to the nearest color")

    noisy = Image.fromarray(
        np.random.RandomState(0).randint(0, 256, (16, 16, 4)).astype(np.uint8), "RGBA")
    q = np.array(cp.quantize(noisy, pal))
    used = {tuple(c) for c in q[q[:, :, 3] > 0][:, :3]}
    check(used <= {tuple(c) for c in pal}, "output uses only the palette")
    check(set(np.unique(q[:, :, 3])) <= {0, 255}, "alpha is binarized")

    soft = Image.new("RGBA", (1, 1), (240, 80, 56, 40))
    check(np.array(cp.quantize(soft, pal))[0, 0, 3] == 0, "sub-threshold alpha cut")

    flat = Image.new("RGBA", (8, 8), (200, 120, 100, 255))
    fq = np.array(cp.quantize(flat, pal))
    check(len({tuple(c) for c in fq.reshape(-1, 4)}) == 1, "no dither on a flat field")


# ---------------------------------------------------------------- backgrounds


def test_backgrounds() -> None:
    print("backgrounds (RESOLVED-5)")
    check(len(cp.BACKGROUNDS) == 6, "6 backgrounds in the Q/E cycle")
    check(set(cp.BACKGROUNDS) == set(cp.GRADIENTS) | set(cp.FLATS),
          "cycle covers 4 gradients + 2 flat keys")

    for name in cp.BACKGROUNDS:
        bg = np.array(cp.make_background(name, (7, 64)))
        check(bg.shape == (64, 7, 4), f"{name}: correct size")
        check((bg[:, :, 3] == 255).all(), f"{name}: opaque")
        check(all(len(set(map(tuple, bg[y]))) == 1 for y in (0, 31, 63)),
              f"{name}: rows uniform (vertical gradient)")

    for name, (top, mid, bot) in cp.GRADIENTS.items():
        bg = np.array(cp.make_background(name, (4, 101)))
        check(tuple(bg[0, 0, :3]) == cp._hex_to_rgb(top), f"{name}: top stop exact")
        check(tuple(bg[50, 0, :3]) == cp._hex_to_rgb(mid), f"{name}: middle stop exact")
        check(tuple(bg[-1, 0, :3]) == cp._hex_to_rgb(bot), f"{name}: bottom stop exact")

    check(tuple(np.array(cp.make_background("key green", (2, 2)))[0, 0]) == (0, 255, 0, 255),
          "key green is #00ff00")

    im = cp.recolor(Image.open(MAP_UNITS / "apc+mask.png"), "orange_star")
    a, c = np.array(im), np.array(cp.composite_on(im, "key magenta"))
    check(c.shape == a.shape, "composite keeps size")
    check((c[:, :, 3] == 255).all(), "composite is fully opaque")
    solid = a[:, :, 3] == 255
    check(np.array_equal(c[solid][:, :3], a[solid][:, :3]), "opaque sprite px survive")
    hole = a[:, :, 3] == 0
    check((c[hole][:, :3] == (255, 0, 255)).all(), "transparent px show the background")


# ---------------------------------------------------------------- frames


def test_frames_and_naming() -> None:
    print("frames and export naming")
    im = Image.open(MAP_UNITS / "apc+mask.png")
    w, h = im.size
    check((w, h) == (128, 32), f"apc is a 128x32 strip (got {w}x{h})")
    check(len(cp.split_frames(im, 1)) == 1, "count 1 returns the whole image")

    frames = cp.split_frames(im, 4)
    check(len(frames) == 4, "split into 4")
    check(all(f.size == (h, h) for f in frames), f"frames are square {h}x{h}")
    check(np.array_equal(np.array(frames[0].convert("RGBA")),
                         np.array(im.convert("RGBA"))[:, :h]), "frame 0 is the left cell")

    for q in sorted(MAP_UNITS.glob("*.png")):
        qw, qh = Image.open(q).size
        check(qw == qh * 4, f"{q.name}: 4 square frames ({qw}x{qh})")

    try:
        cp.split_frames(im, 5)
        check(False, "non-divisible frame count raises")
    except ValueError:
        check(True, "non-divisible frame count raises")

    check(cp.export_name("apc+mask.png", "orange_star") == "apc+orange_star.png",
          "+mask dropped, team appended")
    check(cp.export_name("infantry+ac+mask.png", "blue_moon") == "infantry+ac+blue_moon.png",
          "variant suffixes kept")
    check(cp.export_name("my_sprite.png", "red") == "my_sprite+red.png",
          "plain names get the team appended")


def main() -> int:
    for fn in (test_palette_load, test_recolor_matches_reference, test_every_pixel_defined,
               test_recolor_invariants, test_recolor_is_plausible_art, test_mask_round_trip,
               test_quantize, test_backgrounds, test_frames_and_naming):
        fn()
    print()
    if _failures:
        print(f"{len(_failures)} of {_checks} checks FAILED:")
        for f in _failures:
            print("  -", f)
        return 1
    print(f"all {_checks} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
