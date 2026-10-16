"""Phase 5 -- the conversion pipeline.

Takes a raw AI-generated sprite (Orange Star art on a flat chroma key, drawn at
roughly 1024px with blocky-but-imperfect pixel structure) and returns a finished
CoW sprite: small, quantized to one unit class's palette, and encoded into the
(red, green) coordinates CoW's colortable lookup expects.

The chain is

    chroma-key  ->  pixel-snap downscale  ->  quantize  ->  encode  ->  validate

and the order is deliberate (RESOLVED-1): quantizing *after* the downscale means
the modal vote runs on the generator's full color range, where near-duplicate
shades still carry a majority signal. Quantizing first would collapse those into
ties and hand the vote to whichever shade happened to win a rounding.

Both `tools/batch_convert.py` and the previewer import this module; neither owns
any of the logic.

The hard part is `detect_grid`. See its docstring.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

import cowpalette

from approot import ROOT  # project root from source, exe folder when frozen
CLASSES_JSON = ROOT / "pipeline" / "classes.json"

# The two flat keys the prompts author against (RESOLVED-5). Green is the
# working default; magenta exists for art that is itself green.
CHROMA_KEYS: dict[str, tuple[int, int, int]] = {
    "green": (0, 255, 0),
    "magenta": (255, 0, 255),
}


# --------------------------------------------------------------------------
# class palettes
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ClassPalette:
    """One unit class's quantization target, with its encode coordinates.

    `colors[i]` is the authored RGB the quantizer snaps to; `coords[i]` is the
    (red, green) that color encodes to. They are parallel by construction, so
    encoding is an index lookup rather than a second nearest-color search.
    """

    name: str
    colors: np.ndarray  # (n, 3) uint8 -- authored RGB
    coords: np.ndarray  # (n, 2) uint8 -- (red, green)
    shares: np.ndarray  # (n,) float  -- pixel share in the AW2 sample

    def __len__(self) -> int:
        return len(self.colors)

    @property
    def hex_colors(self) -> list[str]:
        return ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in self.colors]


_CLASS_CACHE: dict[str, ClassPalette] | None = None


def load_class_palettes(path: pathlib.Path | str | None = None) -> dict[str, ClassPalette]:
    """Read `pipeline/classes.json` into ClassPalette objects, memoized."""
    global _CLASS_CACHE
    if path is None and _CLASS_CACHE is not None:
        return _CLASS_CACHE

    p = pathlib.Path(path) if path is not None else CLASSES_JSON
    raw = json.loads(p.read_text(encoding="utf-8"))

    out: dict[str, ClassPalette] = {}
    for name, blob in raw["classes"].items():
        pal = blob["palette"]
        if not pal:
            raise ValueError(f"class {name!r} has an empty palette")
        colors = np.array([_hex(e["rgb"]) for e in pal], dtype=np.uint8)
        coords = np.array([(e["red"], e["green"]) for e in pal], dtype=np.uint8)
        shares = np.array([e.get("share", 0.0) for e in pal], dtype=float)
        out[name] = ClassPalette(name, colors, coords, shares)

    out[ALL_CLASSES] = _union_palette(raw["classes"])

    if path is None:
        _CLASS_CACHE = out
    return out


def class_names(path: pathlib.Path | str | None = None) -> list[str]:
    """`all` first -- it is the default -- then the real classes, sorted."""
    names = sorted(load_class_palettes(path))
    names.remove(ALL_CLASSES)
    return [ALL_CLASSES, *names]


# The default quantization target: every class's observed colors at once.
# Class is not a color restriction -- a tank can carry a driver's skin tone, a
# car's windows can use the ship palette's water blues, a masked soldier has no
# skin at all -- and a single-class palette snaps such pixels to whatever wrong
# color it does have, silently.
ALL_CLASSES = "all"


def _union_palette(classes: dict) -> ClassPalette:
    """Union of every class palette, deduplicated on authored RGB.

    Deduplicating on RGB is safe here, unlike in `table_class_palette`, because
    of a measured property of `classes.json`: every color that appears in more
    than one class encodes to coordinates that render identically in all 32
    teams. `test_convert.py` asserts that, so a rebuilt `classes.json` that
    breaks it fails a test rather than quietly picking one class's band.

    Ordered by pooled pixel count, so ties in the quantizer go to the color the
    AW2 art used most.
    """
    pooled: dict[str, list] = {}
    for blob in classes.values():
        for e in blob["palette"]:
            slot = pooled.setdefault(e["rgb"], [(e["red"], e["green"]), 0])
            slot[1] += e.get("pixels", 0)
    items = sorted(pooled.items(), key=lambda kv: -kv[1][1])
    total = sum(px for _, (_, px) in items) or 1
    return ClassPalette(
        ALL_CLASSES,
        np.array([_hex(rgb) for rgb, _ in items], dtype=np.uint8),
        np.array([xy for _, (xy, _) in items], dtype=np.uint8),
        np.array([px / total for _, (_, px) in items], dtype=float),
    )


def table_class_palette(unit_class: str | Sequence[str],
                        quadrants: Sequence[str] = ("ui", "tertiary", "secondary", "primary"),
                        team: str = "orange_star",
                        palettes: cowpalette.PaletteSet | None = None) -> ClassPalette:
    """Build a ClassPalette straight from a colortable's own class band.

    `classes.json` is the *authoring* target: colors sampled from real AW2 art,
    each snapped to its nearest table cell. This is the other thing you may want
    -- every color CoW's table actually defines for a class, taken verbatim.

    Two reasons it exists:

    * CoW's own shipped map units are encoded on the generic `unit` band (green
      132-134), not the per-class bands that `classes.json` targets. Converting
      art meant to sit beside them needs `unit`, and only this function offers
      it, since `classes.json` has no `unit` entry.
    * It is the exhaustive palette for a class rather than the observed one, so
      it is the right target when the AW2 sample was thin (`recon` has one unit)
      and the generator produces a shade the sample never contained.

    The cells a class band defines are identical across all 33 team tables
    (verified), so a palette built from any team encodes to coordinates that
    render in every team. `team` therefore only picks which RGB the quantizer
    aims at -- use the authoring team, `orange_star`.

    `unit_class` may be a sequence, which unions several bands into one
    palette. That is how real sprites are actually encoded: of the 55 shipped
    map units, 48 use the generic `unit` band alone and 7 mix it with one other
    (`infantry` or `smallvehicle`). Converting art to match such a sprite needs
    both bands, and a palette missing one snaps those pixels to the nearest
    color it does have -- visibly wrong, and the failure is silent.

    All four quadrants are included by default, and that is not a convenience
    -- real sprites draw from all of them. The APC's grey `#948c84` is only in
    `secondary`, so a primary-only palette snaps those 50 px to the nearest
    *red* it does have and the hull grows an orange patch. Narrow the quadrants
    only to deliberately constrain what the art may use.

    Colors duplicated across quadrants collapse to the first coordinate seen,
    in the order given: a repeated swatch is the same color reached two ways,
    and either coordinate renders identically in every team.
    """
    ps = palettes or cowpalette.load_palettes()
    tab = ps[team].table

    names = [unit_class] if isinstance(unit_class, str) else list(unit_class)
    rows = []
    for nm in names:
        try:
            rows.append(cowpalette.UNIT_CLASSES.index(nm))
        except ValueError:
            raise KeyError(f"unknown table class {nm!r}; "
                           f"have {list(cowpalette.UNIT_CLASSES)}") from None

    for q in quadrants:
        if q not in cowpalette.QUADRANT_ORIGINS:
            raise KeyError(f"unknown quadrant {q!r}")

    # Collect candidate swatch coordinates. Swatches are 3x3 on a lattice
    # anchored at the quadrant origin, and adjacent swatches touch -- so they
    # cannot be found by looking for gaps. Step the lattice and take each
    # swatch's centre. CoW's own shipped sprites address those centres
    # (x = 2, 5, 8, 130, ...), so matching them makes our output byte-identical
    # to stock art rather than merely equivalent.
    candidates: list[tuple[int, int]] = []
    for row_index in rows:
        for q in quadrants:
            ox, oy = cowpalette.QUADRANT_ORIGINS[q]
            y = oy + row_index * 3 + 1
            if y > 255:
                continue
            x = ox + 1
            while x < (128 if ox == 0 else 256):
                if tab[y, x, 3] > 0:
                    candidates.append((int(x), int(y)))
                x += 3

    if not candidates:
        raise KeyError(f"class(es) {names} have no swatches in {list(quadrants)}")

    # Deduplicate on the coordinate's *full cross-team signature*, not on one
    # team's RGB. This distinction is not academic: 18 of the 93 colors in the
    # unit bands are reachable by two coordinates that are identical in
    # orange_star and different in other teams. `#addee7` at (139,4) and
    # (145,4) is the same cyan when you author in orange_star and two different
    # colors in 18 of the 33 teams. Keying on orange_star RGB alone collapses
    # such a pair, the encoder picks whichever came first, and the sprite comes
    # out subtly wrong in half the game's armies -- invisible in the team you
    # authored in, which is what makes it worth guarding against here.
    #
    # Keying on the signature keeps both coordinates as separate palette
    # entries rather than silently discarding one.
    #
    # It does not, and cannot, *resolve* the ambiguity. The two coordinates are
    # the same color in the team you authored in, so orange_star art simply
    # does not contain the information needed to choose between them -- and for
    # the pair above, both sit in the same quadrant and the same band, so no
    # ordering of quadrants can pick a side either. Aliased entries tie during
    # quantization and argmin takes the first, which makes the choice
    # deterministic and reproducible but not necessarily the one a given stock
    # sprite made. Measured on the corpus this affects 5 naval sprites plus
    # `infantry+bh`; their output renders correctly in the 15 teams where the
    # pair agrees and picks the other member's color in the other 18.
    #
    # The fix, if it ever matters, is to author a disambiguating pixel: supply
    # art in a second team where the pair differs, or let the user pin a
    # coordinate for that color. Both need input we do not have today, so this
    # is recorded rather than guessed at.
    team_tables = [ps[t].table for t in ps.names() if t != cowpalette.MASK]

    seen: dict[tuple, tuple[int, int]] = {}
    for (x, y) in candidates:
        sig = tuple(int(v) for t in team_tables for v in t[y, x, :3])
        seen.setdefault(sig, (x, y))

    coords = np.array(list(seen.values()), dtype=np.uint8)
    colors = np.array([tab[y, x, :3] for (x, y) in seen.values()], dtype=np.uint8)
    shares = np.full(len(colors), 1.0 / len(colors))
    name = f"{'+'.join(names)}@{'+'.join(quadrants)}"
    return ClassPalette(name, colors, coords, shares)


def bands_of(sprite: Image.Image) -> list[str]:
    """Which class bands an already-encoded sprite addresses.

    Reads the green channel back out through the table's row layout. This is
    for *analysing* existing art -- the converter never infers a class for art
    it is about to encode, because for generated art the class is metadata the
    user supplied at generation time and the image cannot be trusted to reveal
    it.

    Its real use is the round-trip test: to check that converting a shipped
    sprite reproduces it, the conversion has to target the same bands the
    original used, and this is how the test finds out which those are without
    a hand-written table that could silently go stale.
    """
    rows: set[int] = set()
    for _, green in cowpalette.coord_histogram(sprite):
        origin = 129 if green > 128 else 0
        rows.add((green - origin) // 3)
    return [cowpalette.UNIT_CLASSES[i] for i in sorted(rows)
            if 0 <= i < len(cowpalette.UNIT_CLASSES)]


def _hex(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# --------------------------------------------------------------------------
# step 1 -- chroma key
# --------------------------------------------------------------------------


def chroma_key(image: Image.Image, key: str | tuple[int, int, int] = "green",
               tolerance: int = 90, fringe: int = 2) -> Image.Image:
    """Knock the flat backdrop out to alpha, then clean the fringe.

    A generator never returns the exact key color: JPEG-ish ringing and the
    model's own antialiasing leave a halo of desaturated key around every edge.
    Two passes handle that.

    1. Core key: pixels within `tolerance` (Euclidean RGB) of the key go
       transparent outright.
    2. Fringe: within `fringe` px of a keyed pixel, anything whose *dominant
       channel matches the key's dominant channel more strongly than the art
       ever does* is also dropped. Concretely we score how far a pixel leans
       toward the key hue and cut the leaners.

    The alpha produced here is binary. Soft alpha is a trap: it survives the
    resize as a weighted average and then quantize() has to threshold it
    anyway, at which point the halo has already polluted the modal vote.
    """
    rgb_key = np.array(CHROMA_KEYS[key] if isinstance(key, str) else key, dtype=np.int32)

    src = np.array(image.convert("RGBA"))
    rgb = src[:, :, :3].astype(np.int32)

    dist = np.sqrt(((rgb - rgb_key[None, None, :]) ** 2).sum(axis=2))
    keyed = dist <= tolerance

    # already-transparent input stays transparent
    keyed |= src[:, :, 3] < 128

    if fringe > 0 and keyed.any():
        near = _dilate(keyed, fringe) & ~keyed
        # "leaning toward the key": the key's strong channel dominates the
        # others by more than a solid art color would. Orange Star red art has
        # red >> green, so a green key's leaners are unambiguous.
        strong = int(np.argmax(rgb_key))
        others = [c for c in range(3) if c != strong]
        lean = rgb[:, :, strong] - np.maximum(rgb[:, :, others[0]], rgb[:, :, others[1]])
        if rgb_key[strong] == 0:  # key's strong channel is zero -> invert sense
            lean = -lean
        keyed |= near & (lean > 40)

    out = src.copy()
    out[keyed] = 0
    out[~keyed, 3] = 255
    return Image.fromarray(out, "RGBA")


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Square-kernel binary dilation. Small radii, so shifting beats scipy."""
    out = mask.copy()
    for _ in range(radius):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


# --------------------------------------------------------------------------
# step 2 -- pixel-snap downscale (the hard part)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Grid:
    """A detected pixel grid: where the logical pixel boundaries actually fall.

    `pitch` is in source pixels per logical pixel and is a float, because a
    generator asked for 1024px of a 17-px-wide sprite gives 60.235..., not 60.
    `offset` is where the first boundary sits, in [0, pitch).
    """

    pitch_x: float
    pitch_y: float
    offset_x: float
    offset_y: float
    cols: int
    rows: int
    confidence: float

    @property
    def size(self) -> tuple[int, int]:
        return (self.cols, self.rows)

    def edges_x(self) -> np.ndarray:
        return self.offset_x + self.pitch_x * np.arange(self.cols + 1)

    def edges_y(self) -> np.ndarray:
        return self.offset_y + self.pitch_y * np.arange(self.rows + 1)


def detect_grid(image: Image.Image, min_pitch: float = 3.0,
                max_cells: int = 128, confidence_floor: float = 0.55) -> Grid:
    """Find the true pixel pitch and offset of an upscaled pixel-art image.

    The problem: the generator was shown a 6x nearest-neighbour reference and
    imitated its blockiness, but it does not honour an exact integer pitch, it
    does not start the grid at x=0, and its "flat" cells are not flat -- they
    carry gradient and noise. So neither a plain `resize(NEAREST)` nor a naive
    "divide by 6" recovers the original: both point-sample whatever pixel lands
    under the sampling point, which in a noisy cell is a lottery.

    The method, per axis independently:

    1. **Edge energy.** Sum the per-pixel color difference between adjacent
       columns, over rows, giving a 1D signal that spikes at every logical pixel
       boundary and is near-zero inside a flat cell. Working on differences
       rather than on the image makes the signal invariant to what the art
       actually is.

    2. **Score every candidate pitch** from `min_pitch` up to `extent / 2`, at
       0.02-px resolution near integers and 0.05 elsewhere. For a pitch p the
       score is the mean edge energy sampled at the predicted boundaries, minus
       the mean energy everywhere else -- a comb filter. The best offset for a
       given p is found by scanning offsets across [0, p); this is cheap because
       the energy signal is 1D.

    3. **Reject harmonics.** A comb at pitch p also fits pitch p/2, p/3 ... with
       every other tooth landing on a flat interior, so a bare argmax reliably
       returns a fraction of the truth and blows the sprite up by 2x or 3x. We
       therefore walk candidates from the largest pitch down and keep the first
       whose score is within 85% of the global best -- the coarsest grid that
       explains the edges, which is the right answer.

    4. **Confidence** is measured on the reduction itself, not on the comb: for
       each cell, what share of its interior pixels agree with the cell's modal
       color. A true pixel grid gives ~1.0; a misdetected one averages a
       gradient and gives much less. `confidence_floor` is what
       `convert_image` uses to route a file to needs-review/ instead of
       guessing.

    Raises `GridDetectionError` if the image has no opaque content.
    """
    a = np.array(image.convert("RGBA"))
    op = a[:, :, 3] > 0
    if not op.any():
        raise GridDetectionError("image is fully transparent after chroma-key")

    ys, xs = np.nonzero(op)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1

    crop = a[y0:y1, x0:x1]
    rgb = crop[:, :, :3].astype(np.float64)
    alpha = (crop[:, :, 3] > 0).astype(np.float64)
    # treat alpha as a fourth channel: the silhouette's own edges are the
    # strongest and most trustworthy boundary evidence in the whole image.
    chan = np.concatenate([rgb, alpha[:, :, None] * 255.0], axis=2)

    h, w = crop.shape[:2]
    energy_x = np.abs(np.diff(chan, axis=1)).sum(axis=(0, 2))  # length w-1
    energy_y = np.abs(np.diff(chan, axis=0)).sum(axis=(1, 2))  # length h-1

    px, ox_, alt_x = _best_pitch(energy_x, w, min_pitch, max_cells)
    py, oy_, alt_y = _best_pitch(energy_y, h, min_pitch, max_cells)

    pitch_x, offset_x = _pick_axis(crop, energy_x, w, px, ox_, alt_x, axis=1)
    pitch_y, offset_y = _pick_axis(crop, energy_y, h, py, oy_, alt_y, axis=0)

    # The comb reports the first boundary it found, which is somewhere in
    # [0, pitch) -- but the crop is the opaque bounding box, so content starts
    # at 0 and the cell containing it begins *before* that boundary. Walk the
    # origin back to the last boundary at or before 0, then count cells forward
    # until the crop is covered. Skipping this loses the sprite's first row and
    # column whenever the detected offset is near a full pitch.
    offset_x, cols = _frame_axis(offset_x, pitch_x, w)
    offset_y, rows = _frame_axis(offset_y, pitch_y, h)

    conf = _grid_confidence(crop, pitch_x, offset_x, cols, pitch_y, offset_y, rows)

    # Cell uniformity alone is not enough to trust a grid, because the one
    # failure mode it cannot see is the one most worth catching: a grid at half
    # the true pitch splits every real cell in two, and both halves are
    # perfectly uniform. Such a grid scores ~1.0 on uniformity while producing
    # a sprite at twice the right size -- high confidence, wrong answer, which
    # is exactly what the needs-review gate exists to prevent.
    #
    # So fold in the share of adjacent cell pairs that are near-duplicates. On
    # real pixel art some neighbours genuinely match, so this is a discount
    # rather than a veto, and it only bites when duplication is pervasive.
    dup = max(_duplicate_share(crop, pitch_x, offset_x, cols, axis=1),
              _duplicate_share(crop, pitch_y, offset_y, rows, axis=0))
    if dup > 0.66:
        conf *= 1.0 - (dup - 0.66) / 0.34 * 0.6

    # offsets are reported in the *original* image's coordinates
    return Grid(pitch_x, pitch_y, offset_x + x0, offset_y + y0, cols, rows, conf)


def _duplicate_share(crop: np.ndarray, pitch: float, offset: float, count: int,
                     axis: int) -> float:
    """How strongly the cells along `axis` look like each one split in two.

    Returns a value in [0, 1]. Near 1 means the grid is almost certainly at
    half the true pitch: every real cell became two near-identical cells, so
    the gaps between consecutive cells *alternate* -- tiny inside a real cell,
    large at a real boundary. Measured on a halved smuggler the gap sequence is
    1, 42, 1, 50, 1, 20, 2, 29, ... which is unmistakable.

    Alternation is the signal, not the raw share of small gaps. Counting small
    gaps alone gives about 0.5 on a halved grid -- real art also contains flat
    runs -- and that is not separable from a true grid over flat art. The
    alternating structure is.
    """
    extent = crop.shape[axis]
    slices = _cell_slices(pitch, offset, count, extent)
    if len(slices) < 6:
        return 0.0

    lines = []
    for (a, b) in slices:
        sub = crop[a:b] if axis == 0 else crop[:, a:b]
        if sub.size == 0:
            continue
        lines.append(sub.mean(axis=axis).astype(float))
    if len(lines) < 6:
        return 0.0

    gaps = np.array([float(np.abs(lines[i] - lines[i + 1]).mean())
                     for i in range(len(lines) - 1)])
    if gaps.size < 5 or gaps.max() <= 0:
        return 0.0

    # Compare the two interleaved phases of the gap sequence. A halved grid
    # puts all the small gaps in one phase; a true grid scatters them.
    best = 0.0
    for phase in (0, 1):
        inside, boundary = gaps[phase::2], gaps[1 - phase::2]
        if inside.size < 2 or boundary.size < 2:
            continue
        b = float(np.median(boundary))
        if b <= 0:
            continue
        ratio = float(np.median(inside)) / b
        # ratio near 0 -> strongly alternating -> near-certain doubling
        best = max(best, max(0.0, 1.0 - ratio / 0.25))
    return best


class GridDetectionError(RuntimeError):
    """Raised when an image carries no usable grid evidence at all."""


def _pick_axis(crop: np.ndarray, energy: np.ndarray, extent: int,
               pitch: float, offset: float,
               alt: tuple[float, float] | None, axis: int) -> tuple[float, float]:
    """Choose between a pitch and its doubled alternative, then refine.

    The comb filter cannot tell a real grid of pitch p from a blurred grid of
    pitch 2p: both put energy at every p-th boundary, the latter because the
    blur leaked it there. What *does* tell them apart is what happens when you
    actually reduce the image -- at the true pitch, cells are internally
    uniform; at half the true pitch, each real cell is split in two and the
    halves are still uniform, so uniformity alone does not decide it either.

    The deciding question is whether the finer grid carries any information the
    coarser one does not: if reducing at p and at 2p give the same picture,
    then p was splitting cells and 2p is the truth. So we reduce both ways and
    compare the finer result against itself downsampled -- if the odd columns
    duplicate the even ones, the finer grid is spurious.

    Both candidates are refined first, since the comparison is only meaningful
    between two accurately located grids.
    """
    pitch, offset = _refine(energy, extent, pitch, offset)
    if alt is None:
        return pitch, offset

    a_pitch, a_offset = _refine(energy, extent, alt[0], alt[1])

    fine = _reduce_axis(crop, pitch, offset, extent, axis)
    if fine is None or fine.shape[axis] < 4:
        return pitch, offset

    # If `pitch` is half the truth, every real cell became two near-identical
    # cells, and the gaps between consecutive cells alternate small/large.
    # `_duplicate_share` measures exactly that alternation; see its docstring
    # for why the alternation and not the count of small gaps is the signal.
    # 0.7 measured best on the corpus: 43 of 55 sprites recover exactly at
    # both damage=0 and damage=0.5, with 3 size errors. Lowering it to 0.6
    # costs 5 clean sprites (they get wrongly doubled); the value is a real
    # trade-off point, not an arbitrary constant.
    if _duplicate_share(crop, pitch, offset,
                        max(1, int(np.ceil((extent - offset) / pitch))),
                        axis) >= 0.7:
        return a_pitch, a_offset
    return pitch, offset


def _reduce_axis(crop: np.ndarray, pitch: float, offset: float, extent: int,
                 axis: int) -> np.ndarray | None:
    """Collapse `crop` along one axis to one line per cell, by cell mean.

    Only used to compare two candidate grids, so the mean is fine here -- it is
    the modal vote's job to be careful, and this only needs to tell "these two
    lines are the same picture" from "they are not".
    """
    slices = _cell_slices(pitch, offset, max(1, int(np.ceil((extent - offset) / pitch))),
                          extent)
    if not slices:
        return None
    parts = []
    for (a, b) in slices:
        sub = crop[a:b] if axis == 0 else crop[:, a:b]
        if sub.size == 0:
            continue
        parts.append(sub.mean(axis=axis))
    if not parts:
        return None
    return np.stack(parts, axis=axis)


def _candidate_pitches(min_pitch: float, max_pitch: float) -> np.ndarray:
    """Candidate pitches, sampled finely enough to land on non-integer truth."""
    if max_pitch <= min_pitch:
        return np.array([min_pitch])
    return np.arange(min_pitch, max_pitch + 1e-9, 0.02)


def _best_pitch(energy: np.ndarray, extent: int, min_pitch: float,
                max_cells: int) -> tuple[float, float, tuple[float, float] | None]:
    """Comb-filter `energy`; return (pitch, offset, doubled_alternative).

    The third element is the 2x candidate when it also scores well, or None.
    Choosing between them needs the image, not the energy signal, so that
    decision is detect_grid's -- see `_pick_axis`.

    `energy[i]` is the boundary evidence between source pixel i and i+1, so a
    boundary at coordinate c has its evidence at index c-1.
    """
    if energy.size < 2 or not np.any(energy > 0):
        return (1.0, 0.0, None)

    pitch_lo = max(min_pitch, extent / max_cells)
    pitch_hi = max(pitch_lo, extent / 2.0)
    cands = _candidate_pitches(pitch_lo, pitch_hi)

    total = float(energy.sum())
    scores = np.empty(len(cands))
    offsets = np.empty(len(cands))

    # cumulative sums turn "sum of energy in these 3-px windows" into a couple
    # of fancy-index lookups, which is what makes an exhaustive pitch x offset
    # sweep affordable at all.
    cum = np.concatenate([[0.0], np.cumsum(energy)])
    n = energy.size

    for i, p in enumerate(cands):
        # An offset resolution of ~1/5 px is plenty: the comb's teeth are
        # sampled with a 1-px tolerance window below, so finer offsets do not
        # change which indices get picked.
        offs = np.arange(0.0, p, max(0.2, p / 25.0))
        teeth = int(extent / p)
        if teeth < 1:
            scores[i], offsets[i] = -np.inf, 0.0
            continue
        # (offsets, teeth) boundary coordinates -> energy index c-1
        idx = np.round(offs[:, None] + p * np.arange(1, teeth + 1)[None, :]) - 1
        wlo = np.clip(idx - 1, 0, n - 1).astype(int)
        whi = np.clip(idx + 2, 0, n).astype(int)
        whi = np.maximum(whi, wlo + 1)
        on = (cum[whi] - cum[wlo]).sum(axis=1)
        claimed = (whi - wlo).sum(axis=1)
        # share of all edge energy captured, penalized by how much of the axis
        # the comb had to claim to capture it. Without the penalty a pitch of 1
        # captures everything and always wins.
        s = on / total - claimed / n
        k = int(np.argmax(s))
        scores[i], offsets[i] = float(s[k]), float(offs[k])

    best = float(scores.max())
    if best <= 0:
        return (1.0, 0.0, None)

    # Harmonic rejection, in both directions -- this is where a naive argmax
    # goes wrong, and it goes wrong two different ways.
    #
    # Too coarse: a comb at 2p keeps every other tooth of the true comb, so it
    # still lands on real boundaries and scores nearly as well. Measured on the
    # corpus, 2p scores 97% of the true pitch's score -- close enough that any
    # "take the coarsest good one" rule picks it and halves the sprite. 15 of
    # 55 sprites came out at exactly half size under that rule.
    #
    # Too fine: a comb at p/2 captures *all* the real boundaries plus a tooth
    # inside every cell. The claimed-width penalty above is what keeps it from
    # winning outright, but it can still sit inside the tolerance band.
    #
    # So: take the finest pitch scoring within tolerance of the best, then
    # explicitly test whether half of it is a real grid too and step down if
    # so. The step-down test is strict -- a genuine p/2 grid puts *as much*
    # energy on its extra teeth as on the ones it shares with p -- because
    # being wrong here doubles the sprite instead of halving it.
    keep = np.nonzero(scores >= best * 0.9)[0]
    pick = int(keep[0])
    pitch, offset = float(cands[pick]), float(offsets[pick])

    # `pitch` is the finest candidate that explains the edges. That is the
    # right starting point but not always the answer: blur smears every
    # boundary, so p/2 picks up enough midpoint energy to score within the
    # tolerance band, and taking it doubles the sprite. Report the doubled
    # alternative alongside, and let detect_grid choose between them by
    # measuring the reduction each one produces -- a decision the 1D energy
    # signal cannot make on its own, because it cannot see whether the extra
    # cells contain anything.
    alt = None
    d_i = int(np.argmin(np.abs(cands - pitch * 2)))
    if pitch * 2 <= pitch_hi and scores[d_i] >= best * 0.85:
        alt = (float(cands[d_i]), float(offsets[d_i]))

    return (pitch, offset, alt)


def _frame_axis(offset: float, pitch: float, extent: int) -> tuple[float, int]:
    """Slide the grid origin to at-or-before 0, and count cells to cover `extent`.

    Returns an offset in (-pitch, 0] and the cell count.

    The threshold is the subtle part. Stepping back whenever the offset is
    non-zero adds a leading cell that is almost entirely outside the crop --
    which is how `light_tank` came out 27 cells wide instead of 26, with a
    column of near-nothing on the left. Stepping back only when at least a
    third of a cell precedes the first boundary keeps the real leading cell
    when there is one and drops the phantom when there is not. The same third
    governs the trailing edge, so the two ends behave alike.
    """
    keep = pitch / 3.0
    o = offset % pitch
    if o > keep:
        o -= pitch  # a real cell starts before coordinate 0; include it
    n = max(1, int(np.ceil((extent - o - keep) / pitch)))
    return float(o), int(n)


def _refine(energy: np.ndarray, extent: int, pitch: float, offset: float,
            window: float = 0.3) -> tuple[float, float]:
    """Least-squares fit of the boundary line, given a rough pitch and offset.

    The comb sweep is quantized -- 0.02 px in pitch, ~p/25 in offset -- and that
    is not good enough on its own. A 0.7% pitch error is invisible at one
    boundary and catastrophic at the thirtieth: over 30 cells it accumulates to
    a fifth of a cell, which is enough to shift the sampling window of the last
    few columns into their neighbours and lose a column of the sprite entirely.

    So: for each predicted boundary k, find the true energy peak within
    `window * pitch` of it, then regress the found positions on k. The slope is
    the refined pitch and the intercept the refined offset. Boundaries with no
    real peak nearby (a flat run inside the sprite genuinely has no edge there)
    contribute nothing, which is what makes this robust on sparse art.
    """
    n = energy.size
    if n < 2 or pitch <= 1:
        return pitch, offset

    half = max(1, int(round(window * pitch)))
    ks, pos = [], []
    k = 0
    while True:
        c = offset + pitch * k
        if c > extent:
            break
        i = int(round(c)) - 1  # energy index for a boundary at coordinate c
        lo, hi = max(0, i - half), min(n, i + half + 1)
        if hi > lo:
            seg = energy[lo:hi]
            if seg.max() > 0:
                # energy-weighted centroid of the peak, not the argmax: the
                # argmax quantizes back to whole pixels and throws away exactly
                # the subpixel information this pass exists to recover.
                w = seg - seg.min()
                if w.sum() > 0:
                    centre = float((np.arange(lo, hi) * w).sum() / w.sum())
                    ks.append(k)
                    pos.append(centre + 1.0)  # index -> boundary coordinate
        k += 1

    if len(ks) < 3:
        return pitch, offset

    kk = np.array(ks, dtype=float)
    pp = np.array(pos, dtype=float)
    # one robust pass: drop boundaries that disagree with the fit by more than
    # a third of a cell. Those are peaks belonging to art detail, not grid.
    for _ in range(2):
        slope, intercept = np.polyfit(kk, pp, 1)
        resid = np.abs(pp - (slope * kk + intercept))
        keep = resid <= max(1.0, pitch / 3.0)
        if keep.sum() < 3 or keep.all():
            break
        kk, pp = kk[keep], pp[keep]

    slope, intercept = np.polyfit(kk, pp, 1)
    if not (pitch * 0.8 <= slope <= pitch * 1.2):
        return pitch, offset  # the fit ran away; keep the comb's answer
    return float(slope), float(intercept % slope)


def _cell_slices(pitch: float, offset: float, count: int, extent: int,
                 inset: float = 0.25) -> list[tuple[int, int]]:
    """Per-cell [start, stop) source ranges, inset away from the boundaries.

    The inset is the point of the whole exercise: a generator's cell edges are
    smeared over a pixel or two, so the outer quarter of each cell is
    contaminated by its neighbours. Voting only on the middle half gives the
    cell's true color a clean majority.
    """
    out = []
    for i in range(count):
        a = offset + pitch * i
        b = a + pitch
        pad = pitch * inset
        s = int(np.floor(a + pad))
        e = int(np.ceil(b - pad))
        s = max(0, min(s, extent - 1))
        e = max(s + 1, min(e, extent))
        out.append((s, e))
    return out


def _grid_confidence(crop: np.ndarray, px: float, ox: float, cols: int,
                     py: float, oy: float, rows: int) -> float:
    """Mean per-cell agreement with the cell's modal color, weighted by size.

    This is deliberately measured on the same reduction the pipeline performs
    -- same inset, same color bucketing -- so a confidence figure means "the
    modal vote was decisive", not "the comb filter liked itself".

    Bucketing is not a detail here. Measuring agreement on raw RGBA scores a
    correctly detected grid on noisy input at ~0.07, because gaussian noise
    makes every single pixel a unique color and the "mode" wins with a count of
    one. That would route every real generator image to needs-review. The vote
    is taken on buckets, so the confidence in that vote must be too.
    """
    h, w = crop.shape[:2]
    xs = _cell_slices(px, ox, cols, w)
    ys = _cell_slices(py, oy, rows, h)

    packed = _bucket_key(crop)
    agree, weight = 0.0, 0.0
    for (ya, yb) in ys:
        for (xa, xb) in xs:
            cell = packed[ya:yb, xa:xb].ravel()
            if cell.size == 0:
                continue
            _, counts = np.unique(cell, return_counts=True)
            agree += counts.max()
            weight += cell.size
    return float(agree / weight) if weight else 0.0


BUCKET_SHIFT = 5  # 8 levels per channel


def _bucket_key(a: np.ndarray) -> np.ndarray:
    """RGBA -> one integer per pixel at the vote's color resolution.

    Transparent pixels collapse to a single shared key regardless of their RGB,
    since a fully transparent pixel's color is meaningless and letting it vary
    would fracture the vote in every edge cell.
    """
    v = a.astype(np.uint32)
    op = v[..., 3] > 0
    k = ((v[..., 0] >> BUCKET_SHIFT) << 10) | \
        ((v[..., 1] >> BUCKET_SHIFT) << 5) | (v[..., 2] >> BUCKET_SHIFT)
    return np.where(op, k + 1, 0)


def pixel_snap(image: Image.Image, grid: Grid | None = None,
               alpha_threshold: float = 0.5) -> tuple[Image.Image, Grid]:
    """Reduce an upscaled image to one pixel per detected cell, by modal vote.

    The vote is over *quantized-to-8-levels* color rather than raw RGB. Raw RGB
    has no mode worth the name in a generator's output -- a 60x60 cell can hold
    3000 distinct near-identical shades and every one of them "wins" with a
    count of 1, which makes the mode an arbitrary pick. Bucketing to 32-value
    steps first collapses the noise into a real majority; the winning bucket's
    *mean* color is then emitted, so no precision is thrown away.

    Alpha is decided separately and first: a cell is opaque iff more than
    `alpha_threshold` of its interior is opaque. Transparent pixels are excluded
    from the color vote entirely, which stops a half-covered edge cell from
    averaging in the void.
    """
    if grid is None:
        grid = detect_grid(image)

    a = np.array(image.convert("RGBA"))
    h, w = a.shape[:2]

    xs = _cell_slices(grid.pitch_x, grid.offset_x, grid.cols, w)
    ys = _cell_slices(grid.pitch_y, grid.offset_y, grid.rows, h)

    out = np.zeros((grid.rows, grid.cols, 4), dtype=np.uint8)

    for j, (ya, yb) in enumerate(ys):
        for i, (xa, xb) in enumerate(xs):
            cell = a[ya:yb, xa:xb].reshape(-1, 4)
            if cell.size == 0:
                continue
            op = cell[:, 3] > 0
            if op.mean() <= alpha_threshold:
                continue  # stays transparent
            lit = cell[op][:, :3].astype(np.int32)

            bucket = (lit >> BUCKET_SHIFT)
            key = (bucket[:, 0] << 10) | (bucket[:, 1] << 5) | bucket[:, 2]
            vals, counts = np.unique(key, return_counts=True)
            win = vals[counts.argmax()]
            members = lit[key == win]
            out[j, i, :3] = np.round(members.mean(axis=0)).astype(np.uint8)
            out[j, i, 3] = 255

    return Image.fromarray(out, "RGBA"), grid


# --------------------------------------------------------------------------
# steps 3 and 4 -- quantize and encode
# --------------------------------------------------------------------------


def quantize_to_class(image: Image.Image, unit_class: str | ClassPalette,
                      alpha_threshold: int = 128) -> Image.Image:
    """Snap to the class's authored palette. The class is given, never inferred."""
    pal = _resolve_class(unit_class)
    return cowpalette.quantize(image, pal.colors, dither=False,
                               alpha_threshold=alpha_threshold)


def encode(image: Image.Image, unit_class: str | ClassPalette) -> Image.Image:
    """Rewrite a class-quantized image into CoW's (red, green) coordinates.

    Every opaque pixel must already be exactly one of the class's colors --
    which is what `quantize_to_class` guarantees -- so this is a lookup, not a
    second search. Blue is set to 0: CoW ignores it, and zeroing makes the
    encoded form canonical so two runs compare byte-for-byte.

    Raises `EncodeError` on any opaque pixel that is not in the palette, rather
    than silently snapping it; an off-palette pixel here means an earlier step
    misbehaved and the caller should hear about it.
    """
    pal = _resolve_class(unit_class)

    a = np.array(image.convert("RGBA"))
    op = a[:, :, 3] > 0
    out = np.zeros_like(a)
    if not op.any():
        return Image.fromarray(out, "RGBA")

    lit = a[op][:, :3].astype(np.int32)
    keys = (lit[:, 0] << 16) | (lit[:, 1] << 8) | lit[:, 2]

    pc = pal.colors.astype(np.int32)
    pkeys = (pc[:, 0] << 16) | (pc[:, 1] << 8) | pc[:, 2]
    order = np.argsort(pkeys)
    pos = np.searchsorted(pkeys[order], keys)
    pos = np.clip(pos, 0, len(order) - 1)
    idx = order[pos]
    bad = pkeys[idx] != keys
    if bad.any():
        n = int(bad.sum())
        sample = lit[bad][0]
        raise EncodeError(
            f"{n} opaque pixel(s) are not in the {pal.name} palette, "
            f"e.g. #{sample[0]:02x}{sample[1]:02x}{sample[2]:02x}")

    enc = np.zeros((len(keys), 4), dtype=np.uint8)
    enc[:, 0] = pal.coords[idx, 0]
    enc[:, 1] = pal.coords[idx, 1]
    enc[:, 2] = 0
    enc[:, 3] = 255
    out[op] = enc
    return Image.fromarray(out, "RGBA")


class EncodeError(RuntimeError):
    """An opaque pixel was not one of the target class's palette colors."""


def _resolve_class(unit_class: str | ClassPalette) -> ClassPalette:
    if isinstance(unit_class, ClassPalette):
        return unit_class
    table = load_class_palettes()
    if unit_class not in table:
        raise KeyError(f"unknown unit class {unit_class!r}; "
                       f"have {sorted(table)}")
    return table[unit_class]


# --------------------------------------------------------------------------
# step 5 -- validate
# --------------------------------------------------------------------------


def validate(encoded: Image.Image, teams: Sequence[str] | None = None) -> dict:
    """Prove an encoded sprite renders in every team with no holes.

    A hole is an opaque pixel whose (red, green) addresses an empty cell in some
    team's table: CoW would draw nothing there. This is the acceptance test for
    the whole pipeline, so it runs across *all* teams by default rather than
    spot-checking one.
    """
    ps = cowpalette.load_palettes()
    names = list(teams) if teams is not None else [
        t for t in ps.names() if t != cowpalette.MASK]

    worst = 0
    per_team: dict[str, int] = {}
    for t in names:
        n = int(cowpalette.undefined_mask(encoded, t, ps).sum())
        per_team[t] = n
        worst = max(worst, n)

    a = np.array(encoded.convert("RGBA"))
    return {
        "ok": worst == 0,
        "teams_checked": len(names),
        "opaque_pixels": int((a[:, :, 3] > 0).sum()),
        "undefined_worst": worst,
        "undefined_by_team": per_team,
    }


# --------------------------------------------------------------------------
# the whole chain
# --------------------------------------------------------------------------


@dataclasses.dataclass
class ConversionResult:
    """Everything one image produced, including the intermediates.

    The intermediates are kept because the failure modes of this pipeline are
    visual: when a sprite comes out wrong, the useful question is always "at
    which step", and holding the stages costs nothing at these sizes.
    """

    name: str
    unit_class: str
    grid: Grid | None = None
    keyed: Image.Image | None = None
    snapped: Image.Image | None = None
    quantized: Image.Image | None = None
    encoded: Image.Image | None = None
    validation: dict | None = None
    needs_review: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return (not self.needs_review and self.encoded is not None
                and bool(self.validation and self.validation["ok"]))


def convert_image(image: Image.Image, unit_class: str, *, name: str = "sprite",
                  key: str = "green", tolerance: int = 90, fringe: int = 2,
                  confidence_floor: float = 0.55,
                  grid: Grid | None = None,
                  validate_teams: Sequence[str] | None = None) -> ConversionResult:
    """Run the full chain on one image.

    Low-confidence grid detection sets `needs_review` and stops before the
    encode: a wrong grid produces a plausible-looking but silently wrong sprite,
    which is worse than no sprite. Pass an explicit `grid` to override detection
    when the user knows the pitch.
    """
    res = ConversionResult(name=name, unit_class=unit_class)

    res.keyed = chroma_key(image, key=key, tolerance=tolerance, fringe=fringe)

    try:
        g = grid if grid is not None else detect_grid(res.keyed)
    except GridDetectionError as exc:
        res.needs_review = True
        res.reason = str(exc)
        return res
    res.grid = g

    if grid is None and g.confidence < confidence_floor:
        res.needs_review = True
        res.reason = (f"grid confidence {g.confidence:.2f} < {confidence_floor:.2f} "
                      f"(pitch {g.pitch_x:.2f}x{g.pitch_y:.2f} -> {g.cols}x{g.rows})")
        # still snap, so a human can look at what it would have done
        res.snapped, _ = pixel_snap(res.keyed, g)
        return res

    res.snapped, _ = pixel_snap(res.keyed, g)
    res.quantized = quantize_to_class(res.snapped, unit_class)
    res.encoded = encode(res.quantized, unit_class)
    res.validation = validate(res.encoded, validate_teams)

    if not res.validation["ok"]:
        res.needs_review = True
        res.reason = (f"{res.validation['undefined_worst']} pixel(s) undefined "
                      f"in at least one team table")
    return res


def convert_file(path: pathlib.Path | str, unit_class: str, **kw) -> ConversionResult:
    p = pathlib.Path(path)
    kw.setdefault("name", p.stem)
    with Image.open(p) as im:
        return convert_image(im.copy(), unit_class, **kw)


# --------------------------------------------------------------------------
# frame geometry, from CoW's own res.xml
# --------------------------------------------------------------------------

RES_XML = (ROOT / "upstream" / "resources" / "images")


def frame_counts(which: str = "units") -> dict[str, tuple[int, int]]:
    """`{sprite id: (cols, rows)}` from CoW's res.xml. Read-only reference.

    Frame counts for battle animations are not derivable from image dimensions
    -- 640x96 could be 4, 5, 8 or 10 frames -- but upstream records them. We
    read the numbers; we do not copy the code.

    `which` is "units" or "battleanimations". Returns {} if the upstream tree
    is not present, since it is a 277 MB optional checkout.
    """
    import xml.etree.ElementTree as ET

    p = RES_XML / which / "res.xml"
    if not p.is_file():
        return {}

    out: dict[str, tuple[int, int]] = {}
    for el in ET.parse(p).getroot().iter():
        ident = el.get("id") or el.get("file")
        if not ident:
            continue
        cols, rows = el.get("cols"), el.get("rows")
        if cols is None and rows is None:
            continue
        key = pathlib.PurePosixPath(ident).stem
        out[key] = (int(cols or 1), int(rows or 1))
    return out
