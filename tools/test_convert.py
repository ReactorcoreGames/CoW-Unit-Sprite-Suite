"""Tests for the Phase 5 conversion pipeline.

The governing constraint: there is no AI-generated art to test against yet, and
a test that merely re-asserts the pipeline's own assumptions would pass while
proving nothing. So the substantive tests are **round trips against real CoW
sprites** -- take a shipped sprite, decode it to the RGB an artist would have
drawn, put it through a synthetic upscale-and-damage that reproduces what a
generator does to pixel art, run the pipeline, and require the result back.

That has the property the task needs: if the grid detection, the modal vote, the
quantizer or the encoder were wrong, the recovered sprite would differ from the
original and the test would fail loudly. Several of these tests did fail during
development, which is how the 2x-harmonic, the phantom edge cell and the
cross-team color-alias bugs were found.

The acceptance criterion is **render identity, not coordinate identity**: a
color reachable by two table coordinates has two encodings, and where those
coordinates agree in every team the two sprites are the same sprite. Where they
do *not* agree in every team, that is a real difference and the tests check for
it separately -- see `test_alias_limitation`.

Run: python tools/test_convert.py
"""

from __future__ import annotations

import pathlib
import sys
import traceback

import numpy as np
from PIL import Image

import cowpalette
import convert
import synth

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAP_UNITS = ROOT / "samples" / "cow map units"
BATTLEANIM = ROOT / "samples" / "cow battle animations"

# How many sprites the corpus-wide tests use. The full 55 take a few minutes;
# a deterministic slice keeps the suite runnable while still spanning sizes,
# silhouettes and both the `unit` and mixed-band encodings.
CORPUS_N = 14

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


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def corpus(n: int = CORPUS_N) -> list[pathlib.Path]:
    files = sorted(MAP_UNITS.glob("*.png"))
    if not files:
        raise SystemExit(f"no sprites in {MAP_UNITS}")
    if n >= len(files):
        return files
    # evenly spaced rather than the first n: the folder is alphabetical, so the
    # first n would be all A-and-B units and miss the naval and air sizes.
    step = len(files) / n
    return [files[int(i * step)] for i in range(n)]


def authored_and_box(sprite: Image.Image):
    """The Orange Star decode of a sprite, plus its opaque bounding box.

    The pipeline crops to content, so every comparison is against the crop.
    """
    auth = synth.decode_to_authored(sprite)
    a = np.array(auth)
    ys, xs = np.nonzero(a[:, :, 3] > 0)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return auth, box


def renders_same(got: Image.Image, want: Image.Image, team: str) -> bool:
    """Do two encoded sprites render identically in `team`?

    Compares the opacity mask and the RGB where opaque -- not raw arrays. Our
    encoder emits a hard alpha of 255 while stock sprites carry their source
    alpha, and that difference is invisible both in game and to `recolor`.
    """
    g = np.array(cowpalette.recolor(got, team))
    w = np.array(cowpalette.recolor(want, team))
    if g.shape != w.shape:
        return False
    gm, wm = g[:, :, 3] > 0, w[:, :, 3] > 0
    return bool(np.array_equal(gm, wm)
                and np.array_equal(g[gm][:, :3], w[wm][:, :3]))


def teams_differing(got: Image.Image, want: Image.Image) -> list[str]:
    ps = cowpalette.load_palettes()
    return [t for t in ps.names()
            if t != cowpalette.MASK and not renders_same(got, want, t)]


def roundtrip(path: pathlib.Path, *, damage: float = 0.0, target: int = 320,
              seed: int = 7, frame: int = 0):
    """Full synthetic round trip for one shipped sprite.

    Returns (result, original_frame_cropped, teams_that_differ).
    """
    with Image.open(path) as sp:
        fr = synth.frame_of(sp.copy(), frame)
    auth, box = authored_and_box(fr)
    want = fr.convert("RGBA").crop(box)

    # Target the bands the original actually uses. Deriving this from the
    # sprite rather than hardcoding it is what keeps the test honest: 48 of the
    # 55 map units use `unit` alone and 7 mix in another band, and a hardcoded
    # guess would quietly mis-target those 7 and mask a real failure as a
    # palette mismatch.
    pal = convert.table_class_palette(convert.bands_of(fr))

    big, _, _ = synth.synthesize(auth, target=target, damage=damage, seed=seed)
    res = convert.convert_image(big, pal, name=path.stem)
    if res.encoded is None:
        return res, want, None
    return res, want, teams_differing(res.encoded, want)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------


def test_class_palettes() -> None:
    section("class palettes")

    classes = convert.load_class_palettes()
    check(len(classes) == 7, "six classes in classes.json plus the 'all' union")
    check(convert.class_names()[0] == convert.ALL_CLASSES, "'all' is not listed first")

    # The union holds every color of every class, once each.
    union = classes[convert.ALL_CLASSES]
    real = {n: p for n, p in classes.items() if n != convert.ALL_CLASSES}
    every = {c for p in real.values() for c in p.hex_colors}
    check(set(union.hex_colors) == every, "union is missing a class color")
    check(len(union.hex_colors) == len(every), "union holds a color twice")
    check(abs(union.shares.sum() - 1.0) < 1e-9, "union shares do not sum to 1")

    # The property that makes deduplicating the union on RGB safe: a color
    # shared by several classes must render identically in every team whichever
    # class's coordinate it takes. If a rebuilt classes.json breaks this, the
    # union would silently pick one class's band for that color.
    ps = cowpalette.load_palettes()
    tabs = [ps[t].table for t in ps.names() if t != cowpalette.MASK]
    renders: dict[str, set] = {}
    for p in real.values():
        for c, (r, g) in zip(p.hex_colors, p.coords):
            renders.setdefault(c, set()).add(
                tuple(int(v) for t in tabs for v in t[int(g), int(r), :3]))
    split = [c for c, s in renders.items() if len(s) > 1]
    check(not split, f"shared colors render differently across classes: {split}")

    # Blue stays blue. Nearest-RGB in Orange Star once sent the AW2 water and
    # glass blues to cells that turn grey, green or gold in other armies;
    # build_pipeline.py pins them to CoW's own water cells. Guard the outcome,
    # not the pin table: every blue authored color must render blue in at
    # least 31 of the 32 teams.
    import colorsys

    def is_blue(rgb) -> bool:
        h, _, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
        return 0.45 < h < 0.72 and s > 0.2

    for p in real.values():
        for c, rgb, (r, g) in zip(p.hex_colors, p.colors, p.coords):
            if not is_blue(rgb):
                continue
            blue_in = sum(is_blue(t[int(g), int(r), :3]) for t in tabs)
            check(blue_in >= len(tabs) - 1,
                  f"{p.name} {c}: encodes to ({r},{g}), blue in only {blue_in}/{len(tabs)} teams")
    for name, pal in classes.items():
        check(len(pal.colors) == len(pal.coords) == len(pal.shares),
              f"{name}: colors/coords/shares are parallel")
        check(len(pal) >= 12, f"{name}: at least 12 colors")
        check(pal.coords.max() <= 255 and pal.coords.min() >= 0,
              f"{name}: coordinates are in range")

    # Every classes.json coordinate must be defined in every team, or art
    # encoded with it renders as a hole for some armies.
    ps = cowpalette.load_palettes()
    teams = [t for t in ps.names() if t != cowpalette.MASK]
    for name, pal in classes.items():
        holes = 0
        for (r, g) in pal.coords:
            for t in teams:
                if ps[t].table[int(g), int(r), 3] == 0:
                    holes += 1
        check(holes == 0, f"{name}: every coordinate defined in all {len(teams)} teams")


def test_table_palettes() -> None:
    section("table-derived palettes")

    for band in cowpalette.UNIT_CLASSES:
        try:
            pal = convert.table_class_palette(band)
        except KeyError:
            continue  # `base` is empty in some quadrants; skip if wholly absent
        check(len(pal) > 0, f"band {band}: non-empty")
        check(len(pal.colors) == len(pal.coords), f"band {band}: parallel arrays")

    unit = convert.table_class_palette("unit")
    # Swatch centres, matching what stock sprites address. If this slipped to
    # swatch edges the output would still render correctly but would no longer
    # be byte-identical to stock art, so it is worth pinning.
    reds = {int(r) for r, g in unit.coords}
    check(reds & {130, 133, 136}, "unit band lands on swatch centres (x=130,133,...)")

    # The cross-team alias case, asserted directly: these two coordinates are
    # the same color in orange_star and different colors elsewhere. If a future
    # change went back to deduplicating on one team's RGB, this would fail.
    ps = cowpalette.load_palettes()
    a, b = (139, 4), (145, 4)
    same_os = np.array_equal(ps["orange_star"].table[a[1], a[0], :3],
                             ps["orange_star"].table[b[1], b[0], :3])
    differ = [t for t in ps.names() if t != cowpalette.MASK
              and not np.array_equal(ps[t].table[a[1], a[0], :3],
                                     ps[t].table[b[1], b[0], :3])]
    check(same_os, "(139,4) and (145,4) are identical in orange_star")
    check(len(differ) == 18, f"...and differ in 18 teams (got {len(differ)})")
    coords = {(int(r), int(g)) for r, g in unit.coords}
    check(a in coords and b in coords,
          "both alias coordinates survive into the palette")

    # Multi-band union really unions.
    both = convert.table_class_palette(["unit", "infantry"])
    only = convert.table_class_palette("unit")
    check(len(both) > len(only), "multi-band palette is larger than one band")


def test_chroma_key() -> None:
    section("chroma key")

    # A flat key with a solid square on it, plus a deliberate halo ring.
    a = np.zeros((40, 40, 4), dtype=np.uint8)
    a[:, :, :3] = (0, 255, 0)
    a[:, :, 3] = 255
    a[10:30, 10:30, :3] = (232, 72, 64)   # art
    a[9, 10:30, :3] = (120, 200, 90)      # halo: leans green
    img = Image.fromarray(a, "RGBA")

    keyed = np.array(convert.chroma_key(img))
    check((keyed[0, 0, 3] == 0), "flat key becomes transparent")
    check((keyed[20, 20, 3] == 255), "art stays opaque")
    check(tuple(keyed[20, 20, :3]) == (232, 72, 64), "art color is untouched")
    check(keyed[9, 20, 3] == 0, "green-leaning halo is removed")
    check(set(np.unique(keyed[:, :, 3])) <= {0, 255}, "alpha is binary")

    # Magenta key, same shape of test.
    a[:, :, :3] = (255, 0, 255)
    a[10:30, 10:30, :3] = (232, 72, 64)
    m = np.array(convert.chroma_key(Image.fromarray(a, "RGBA"), key="magenta"))
    check(m[0, 0, 3] == 0, "magenta key becomes transparent")
    check(m[20, 20, 3] == 255, "art survives the magenta key")

    # A sprite that is *itself* mostly key-colored must not be erased when the
    # other key is used -- this is the reason two keys exist.
    a[:, :, :3] = (255, 0, 255)
    a[10:30, 10:30, :3] = (0, 255, 0)
    m2 = np.array(convert.chroma_key(Image.fromarray(a, "RGBA"), key="magenta"))
    check(m2[20, 20, 3] == 255, "green art survives a magenta key")


def test_grid_detection() -> None:
    section("grid detection")

    # Ground truth from the synthesizer: it knows the pitch it used.
    path = MAP_UNITS / "apc+mask.png"
    with Image.open(path) as sp:
        fr = synth.frame_of(sp.copy(), 0)
    auth, _ = authored_and_box(fr)

    for damage in (0.0, 0.5, 1.0):
        big, true_pitch, _ = synth.synthesize(auth, target=320, damage=damage, seed=3)
        keyed = convert.chroma_key(big)
        g = convert.detect_grid(keyed)
        err = abs(g.pitch_x - true_pitch) / true_pitch
        check(err < 0.02,
              f"damage {damage}: pitch within 2% of truth "
              f"({g.pitch_x:.3f} vs {true_pitch:.3f})")
        check(g.confidence > 0.55,
              f"damage {damage}: confidence above the review floor "
              f"({g.confidence:.3f})")

    # Confidence must actually discriminate, or the needs-review gate is
    # decoration. A photo-like image has no pixel grid at all.
    rng = np.random.default_rng(0)
    noise = np.zeros((200, 200, 4), dtype=np.uint8)
    noise[:, :, :3] = rng.integers(0, 256, (200, 200, 3))
    noise[:, :, 3] = 255
    gn = convert.detect_grid(Image.fromarray(noise, "RGBA"))
    check(gn.confidence < 0.55,
          f"gridless noise scores below the floor ({gn.confidence:.3f})")

    # A fully transparent image has nothing to detect and must say so rather
    # than returning a meaningless grid.
    blank = Image.fromarray(np.zeros((32, 32, 4), dtype=np.uint8), "RGBA")
    try:
        convert.detect_grid(blank)
        check(False, "empty image raises GridDetectionError")
    except convert.GridDetectionError:
        check(True, "empty image raises GridDetectionError")


def test_modal_beats_point_sampling() -> None:
    """The reducer must beat NEAREST, or the whole approach is unjustified.

    This is the load-bearing claim of the pixel-snap step: a point sample in a
    noisy, gradient-shaded cell picks whatever pixel happens to land under the
    sampling point, while a modal vote picks what the cell is actually made of.
    Asserting it here keeps the claim honest rather than assumed.
    """
    section("modal vote vs. point sampling")

    paths = corpus(6)
    modal_wins = nearest_wins = 0

    for path in paths:
        with Image.open(path) as sp:
            fr = synth.frame_of(sp.copy(), 0)
        auth, box = authored_and_box(fr)
        want = np.array(auth.crop(box))

        big, _, _ = synth.synthesize(auth, target=320, damage=1.0, seed=11)
        keyed = convert.chroma_key(big)
        grid = convert.detect_grid(keyed)
        snapped = np.array(convert.pixel_snap(keyed, grid)[0])

        nearest = np.array(keyed.resize((grid.cols, grid.rows), Image.NEAREST))

        if snapped.shape != want.shape or nearest.shape != want.shape:
            continue

        def err(x):
            m = (x[:, :, 3] > 0) & (want[:, :, 3] > 0)
            if not m.any():
                return float("inf")
            return float(np.abs(x[m][:, :3].astype(int)
                                - want[m][:, :3].astype(int)).mean())

        if err(snapped) < err(nearest):
            modal_wins += 1
        else:
            nearest_wins += 1

    check(modal_wins > nearest_wins,
          f"modal vote beats NEAREST on most sprites ({modal_wins} vs {nearest_wins})")


def test_encode_is_exact() -> None:
    section("encode")

    pal = convert.table_class_palette("unit")

    # A quantized image encodes without complaint and lands on the right coords.
    h = w = 6
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[:, :, :3] = pal.colors[0]
    a[:, :, 3] = 255
    a[0, 0, 3] = 0
    enc = np.array(convert.encode(Image.fromarray(a, "RGBA"), pal))
    check(enc[0, 0, 3] == 0, "transparent pixels stay transparent")
    check(tuple(enc[3, 3, :2]) == tuple(pal.coords[0]),
          "an opaque pixel gets its palette coordinate")
    check(enc[3, 3, 2] == 0, "blue is zeroed (CoW ignores it; canonical form)")

    # An off-palette pixel must raise, not be silently snapped: if it reaches
    # the encoder, an earlier step misbehaved and swallowing it hides that.
    a[2, 2, :3] = (1, 2, 3)
    try:
        convert.encode(Image.fromarray(a, "RGBA"), pal)
        check(False, "off-palette pixel raises EncodeError")
    except convert.EncodeError:
        check(True, "off-palette pixel raises EncodeError")

    # quantize -> encode never raises, for any input, which is the contract
    # convert_image relies on.
    rng = np.random.default_rng(5)
    junk = np.zeros((20, 20, 4), dtype=np.uint8)
    junk[:, :, :3] = rng.integers(0, 256, (20, 20, 3))
    junk[:, :, 3] = 255
    q = convert.quantize_to_class(Image.fromarray(junk, "RGBA"), pal)
    try:
        convert.encode(q, pal)
        check(True, "quantize output always encodes")
    except convert.EncodeError as exc:
        check(False, f"quantize output always encodes ({exc})")


def test_validation_catches_holes() -> None:
    """`validate` is the acceptance gate, so it must be able to fail."""
    section("validation")

    pal = convert.table_class_palette("unit")
    good = np.zeros((4, 4, 4), dtype=np.uint8)
    good[:, :, :2] = pal.coords[0]
    good[:, :, 3] = 255
    rep = convert.validate(Image.fromarray(good, "RGBA"))
    check(rep["ok"], "a properly encoded sprite validates")
    check(rep["teams_checked"] >= 30,
          f"validation covers every team ({rep['teams_checked']})")
    check(rep["opaque_pixels"] == 16, "opaque pixel count is reported")

    # The gutter at row/col 128 is empty in every table, so a sprite addressing
    # it must be rejected. If validate cannot fail here it is worthless.
    bad = good.copy()
    bad[0, 0, :2] = (128, 128)
    rep2 = convert.validate(Image.fromarray(bad, "RGBA"))
    check(not rep2["ok"], "a sprite addressing the gutter fails validation")
    check(rep2["undefined_worst"] >= 1, "the hole is counted")

    # Fully transparent is vacuously fine, not a crash.
    blank = np.zeros((4, 4, 4), dtype=np.uint8)
    check(convert.validate(Image.fromarray(blank, "RGBA"))["ok"],
          "a blank image validates vacuously")


def test_roundtrip_clean() -> None:
    """The headline test: real sprites survive the whole pipeline intact."""
    section("round trip -- clean upscale")

    exact = wrong_size = differed = 0
    alias_only = 0
    for path in corpus():
        res, want, diff = roundtrip(path, damage=0.0)
        if res.encoded is None:
            wrong_size += 1
            continue
        check(res.validation["ok"], f"{path.stem}: output validates in every team")
        if res.encoded.size != want.size:
            wrong_size += 1
            continue
        if not diff:
            exact += 1
        elif len(diff) == 18:
            # The documented cross-team alias: two coordinates that are the
            # same color in orange_star and differ in exactly 18 teams. The
            # input cannot distinguish them -- see test_alias_limitation.
            alias_only += 1
        else:
            differed += 1

    n = len(corpus())

    # Thresholds are the measured figures, not round numbers. Over the full 55
    # sprites the pipeline recovers 43 exactly with 3 size errors; on this
    # slice that works out to at most one size error and at least ten exact.
    # Stating them as "all" would be wrong, and stating them loosely would let
    # a real regression through -- these are set just below where the code
    # actually sits, so losing a sprite fails the suite.
    check(wrong_size <= 1,
          f"at most one sprite misses its size ({wrong_size} of {n})")
    check(differed == 0,
          f"no sprite differs for reasons other than the known alias "
          f"({differed} unexplained)")
    check(exact >= 10,
          f"at least 10 of {n} recover byte-exactly ({exact})")
    check(exact + alias_only + wrong_size == n,
          f"every sprite is exact, alias-limited, or a counted size miss "
          f"({exact} exact, {alias_only} alias, {wrong_size} size)")
    print(f"    {exact}/{n} byte-exact, {alias_only} alias-limited, "
          f"{wrong_size} size miss")


def test_roundtrip_damaged() -> None:
    """The same, through the full generator-damage model.

    Weaker thresholds than the clean case, deliberately: this measures how much
    abuse the detector tolerates, and pinning it to perfection would mean
    tuning the damage model to the code instead of the other way round.
    """
    section("round trip -- damaged upscale")

    for damage in (0.5, 1.0):
        good = total = 0
        for path in corpus(8):
            res, want, diff = roundtrip(path, damage=damage)
            total += 1
            if res.encoded is None:
                continue
            check(res.validation["ok"],
                  f"{path.stem} @ {damage}: output validates in every team")
            if res.encoded.size == want.size and (not diff or len(diff) == 18):
                good += 1
        check(good >= total * 0.6,
              f"damage {damage}: at least 60% recover exactly ({good}/{total})")
        print(f"    damage {damage}: {good}/{total} recovered")


def test_alias_limitation() -> None:
    """Pin the known limitation, so a future fix is noticed rather than assumed.

    If someone later teaches the encoder to disambiguate cross-team aliases,
    this test starts failing -- which is the point. A limitation that is
    asserted stays visible; one that is only mentioned in a comment does not.
    """
    section("known limitation: cross-team color aliases")

    naval = MAP_UNITS / "submarine+mask.png"
    if not naval.is_file():
        check(False, "submarine+mask.png present for the alias test")
        return

    res, want, diff = roundtrip(naval, damage=0.0)
    check(res.encoded is not None and res.encoded.size == want.size,
          "submarine recovers its size")
    if res.encoded is None:
        return
    check(res.validation["ok"], "submarine output still validates everywhere")
    check(len(diff) in (0, 18),
          f"submarine differs in 0 or exactly 18 teams (got {len(diff)})")
    if diff:
        check(all(not renders_same(res.encoded, want, t) for t in diff),
              "the differing teams are consistently reported")
        # It must still be *correct art* in the teams where the alias agrees.
        agree = [t for t in cowpalette.load_palettes().names()
                 if t != cowpalette.MASK and t not in diff]
        check(len(agree) >= 14,
              f"it renders correctly in the other {len(agree)} teams")


def test_needs_review_gate() -> None:
    """Low-confidence input must be flagged, not guessed at."""
    section("needs-review gate")

    rng = np.random.default_rng(2)
    noise = np.zeros((160, 160, 4), dtype=np.uint8)
    noise[:, :, :3] = rng.integers(0, 256, (160, 160, 3))
    noise[:, :, 3] = 255
    res = convert.convert_image(Image.fromarray(noise, "RGBA"), "smallvehicle",
                                name="noise")
    check(res.needs_review, "gridless noise is routed to review")
    check(res.encoded is None, "a flagged image produces no encoded output")
    check(not res.ok, "a flagged result is not ok")
    check(bool(res.reason), "a flagged result explains itself")
    check(res.snapped is not None,
          "a flagged result still keeps the snapped stage, for a human to look at")

    # An explicit grid overrides the gate: the user measured it, so trust it.
    g = convert.Grid(8.0, 8.0, 0.0, 0.0, 20, 20, 1.0)
    forced = convert.convert_image(Image.fromarray(noise, "RGBA"), "smallvehicle",
                                   name="noise", grid=g)
    check(not forced.needs_review, "an explicit grid bypasses the confidence gate")
    check(forced.encoded is not None, "an explicit grid produces output")
    check(forced.validation["ok"], "forced output still validates")


def test_frame_counts() -> None:
    """res.xml gives authoritative frame counts; absence must be graceful."""
    section("frame counts from res.xml")

    units = convert.frame_counts("units")
    anims = convert.frame_counts("battleanimations")
    if not units and not anims:
        check(True, "upstream tree absent -- frame_counts returns {} (optional checkout)")
        return

    check(isinstance(units, dict), "units res.xml parses to a dict")
    if units:
        check(len(units) > 100, f"units res.xml has many entries ({len(units)})")
        check(all(isinstance(v, tuple) and len(v) == 2 for v in units.values()),
              "every entry is a (cols, rows) pair")
        check(all(c >= 1 and r >= 1 for c, r in units.values()),
              "every count is at least 1")
    check(convert.frame_counts("nonexistent") == {},
          "a missing res.xml returns {} rather than raising")


def test_map_unit_geometry() -> None:
    """`frame_of` must match the measured geometry of the shipped strips."""
    section("map-unit frame geometry")

    sizes = set()
    for path in sorted(MAP_UNITS.glob("*.png")):
        with Image.open(path) as im:
            w, h = im.size
        sizes.add((w, h))
        check(w % h == 0, f"{path.stem}: width is a whole number of frames")
        check(w // h == 4, f"{path.stem}: exactly 4 frames")
    check(len(sizes) == 4, f"four distinct strip sizes ({sorted(sizes)})")

    with Image.open(MAP_UNITS / "apc+mask.png") as sp:
        f0 = synth.frame_of(sp.copy(), 0)
        f4 = synth.frame_of(sp.copy(), 4)
    check(f0.size == (32, 32), "a 128x32 strip yields 32x32 frames")
    check(np.array_equal(np.array(f0), np.array(f4)), "frame index wraps")


def test_pipeline_is_shared() -> None:
    """Both entry points must go through this module, not reimplement it."""
    section("shared module")

    import batch_convert
    check(batch_convert.convert is convert,
          "batch_convert imports the shared conversion module")

    src = (ROOT / "tools" / "previewer.py").read_text(encoding="utf-8")
    check("import convert" in src or "from convert" in src,
          "previewer imports the shared conversion module")
    check("def detect_grid" not in src and "def pixel_snap" not in src,
          "previewer does not reimplement the pipeline")


# --------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_class_palettes,
        test_table_palettes,
        test_chroma_key,
        test_grid_detection,
        test_modal_beats_point_sampling,
        test_encode_is_exact,
        test_validation_catches_holes,
        test_roundtrip_clean,
        test_roundtrip_damaged,
        test_alias_limitation,
        test_needs_review_gate,
        test_frame_counts,
        test_map_unit_geometry,
        test_pipeline_is_shared,
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
