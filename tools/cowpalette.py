"""Shared palette core for the CoW Unit Sprite Suite.

THE RULE, straight from SpriteCreator::recolorImageWithTable (the runtime
recolor path in reference/spritecreator.cpp):

    qint32 red   = pixel.red();
    qint32 green = pixel.green();
    targetImage.setPixel(x, y, colorTable.pixel(red, green));

That is a **2D lookup into the whole 256x256 colortable**: the source pixel's
red channel is the table's X coordinate and its green channel is the table's Y
coordinate. Blue is unused. Alpha 0 stays transparent; everything else takes the
table's color outright. There is no fallthrough and no row-0 special case.

Verified: every (red, green) pair occurring in the shipped sprite sets lands on
an opaque cell of every team table -- 100% of 92,754 map-unit pixels and 100% of
229,006 battle-animation pixels. See tools/test_cowpalette.py.

Table layout (documented in pixel text inside the PNGs themselves, and in
reference/colortables/Color_Codes.png):

    256x256, four quadrants, each a palette for a different material role:

        +----------------+----------------+
        | UI      (0,0)  | TERTIARY       |   row/col 128 is an empty gutter
        |                |        (129,0) |
        +----------------+----------------+
        | SECONDARY      | PRIMARY        |
        |        (0,129) |      (129,129) |
        +----------------+----------------+

    Within a quadrant, swatches are 3x3 pixels, arranged in rows by unit class:
    BASE, UNIT, INFANTRY, SMALLVEHICLE, RECON, AIRVEHICLE, SHIP, LARGEVEHICLE.
    In mask.png an unused slot is drawn as a black X.

None of that layout needs to be modelled to recolor correctly -- the 2D lookup
handles it. It is described here because it explains what the numbers mean, and
because the converter (Phase 5) will need to *choose* coordinates when encoding
new art.

Both the previewer (Phase 2) and the conversion pipeline (Phase 5) import this
module. It depends only on numpy and Pillow -- no GUI imports here.
"""

from __future__ import annotations

import pathlib
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

from approot import ROOT  # project root from source, exe folder when frozen

# Vendored copy of the game's colortables, used as-is.
TABLE_DIR = ROOT / "reference" / "colortables"

# Not a team: a legend image shipped alongside the tables.
_NOT_A_TABLE = frozenset({"Color_Codes"})

# The authoring/source table. Its cells hold the index-encoded values that
# sprites are drawn in, so it is a valid render target but not a team color.
MASK = "mask"

QUADRANT_ORIGINS = {
    "ui": (0, 0),
    "tertiary": (129, 0),
    "secondary": (0, 129),
    "primary": (129, 129),
}

UNIT_CLASSES = (
    "base", "unit", "infantry", "smallvehicle",
    "recon", "airvehicle", "ship", "largevehicle",
)


# --------------------------------------------------------------------------
# palette loading
# --------------------------------------------------------------------------


class Palette:
    """One 256x256 colortable, held as-is.

    `table` is (256, 256, 4) uint8 RGBA indexed [y, x] -- so a lookup for a
    source pixel is `table[green, red]`.
    """

    __slots__ = ("name", "table", "path")

    def __init__(self, name: str, path: pathlib.Path):
        self.name = name
        self.path = path
        self.table = np.array(Image.open(path).convert("RGBA"))
        if self.table.shape[:2] != (256, 256):
            raise ValueError(f"{path.name}: expected 256x256, got {self.table.shape[:2]}")

    def lookup(self, red: int, green: int) -> tuple[int, int, int, int]:
        """The table color a source pixel with these channels maps to."""
        return tuple(int(v) for v in self.table[green, red])

    def is_live(self, red: int, green: int) -> bool:
        """True if that coordinate is defined (opaque) in this table."""
        return bool(self.table[green, red, 3])

    def swatch_rows(self, quadrant: str) -> dict[str, tuple[int, int, int]]:
        """Best-effort read of one quadrant's 3x3 swatch rows, for inspection.

        Returns {unit_class: (y, x_start, x_end)} for rows that have content.
        Not used by recolor -- the 2D lookup needs no layout knowledge.
        """
        ox, oy = QUADRANT_ORIGINS[quadrant]
        out: dict[str, tuple[int, int, int]] = {}
        for i, cls in enumerate(UNIT_CLASSES):
            y = oy + i * 3
            if y + 2 > 255:
                break
            band = self.table[y, ox:ox + 127 if ox == 0 else 256]
            xs = np.nonzero(band[:, 3])[0]
            if len(xs):
                out[cls] = (y, ox + int(xs.min()), ox + int(xs.max()))
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Palette {self.name}>"


class PaletteSet:
    """Every colortable in reference/colortables/."""

    def __init__(self, folder: pathlib.Path | str = TABLE_DIR):
        folder = pathlib.Path(folder)
        if not folder.is_dir():
            raise FileNotFoundError(f"{folder} missing -- expected the colortable PNGs there")
        self.folder = folder
        self._tables: dict[str, Palette] = {}
        self._paths = {
            p.stem: p for p in sorted(folder.glob("*.png"))
            if p.stem not in _NOT_A_TABLE
        }
        if not self._paths:
            raise FileNotFoundError(f"no colortable PNGs in {folder}")

    def __getitem__(self, name: str) -> Palette:
        if name not in self._tables:
            if name not in self._paths:
                raise KeyError(f"no colortable named {name!r}; have {', '.join(self.names())}")
            self._tables[name] = Palette(name, self._paths[name])
        return self._tables[name]

    def __contains__(self, name: object) -> bool:
        return name in self._paths

    def __len__(self) -> int:
        return len(self._paths)

    def names(self) -> list[str]:
        """Every table, `mask` included."""
        return sorted(self._paths)

    def team_names(self) -> list[str]:
        """Render targets, classic AW armies first, then the rest alphabetically.

        `mask` is included last: rendering to it round-trips a sprite back to its
        authoring colors, which is useful for checking a conversion.
        """
        canonical = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole"]
        head = [n for n in canonical if n in self._paths]
        tail = sorted(n for n in self._paths if n not in head and n != MASK)
        return head + tail + ([MASK] if MASK in self._paths else [])


_DEFAULT_SET: PaletteSet | None = None


def load_palettes(folder: pathlib.Path | str | None = None) -> PaletteSet:
    """Load the palette set, memoized when using the default folder."""
    global _DEFAULT_SET
    if folder is not None:
        return PaletteSet(folder)
    if _DEFAULT_SET is None:
        _DEFAULT_SET = PaletteSet()
    return _DEFAULT_SET


# --------------------------------------------------------------------------
# recolor
# --------------------------------------------------------------------------


def recolor(image: Image.Image, team: str | Palette,
            palettes: PaletteSet | None = None) -> Image.Image:
    """Render a sprite into `team`'s colors: out = table[src.green, src.red].

    Vectorized form of SpriteCreator::recolorImageWithTable. Transparent source
    pixels stay transparent; the source alpha is preserved rather than being
    forced to the table's alpha, so antialiased edges survive.
    """
    pal = team if isinstance(team, Palette) else (palettes or load_palettes())[team]
    src = np.array(image.convert("RGBA"))
    op = src[:, :, 3] > 0

    out = np.zeros_like(src)
    r = src[:, :, 0].astype(np.intp)
    g = src[:, :, 1].astype(np.intp)
    out[op] = pal.table[g[op], r[op]]
    out[op, 3] = src[op, 3]
    return Image.fromarray(out, "RGBA")


def undefined_mask(image: Image.Image, team: str | Palette,
                   palettes: PaletteSet | None = None) -> np.ndarray:
    """Boolean (h, w): opaque source pixels whose coordinate is empty in `team`.

    A correctly encoded sprite has none. Anything here would render as a hole,
    so this is the check the converter must pass.
    """
    pal = team if isinstance(team, Palette) else (palettes or load_palettes())[team]
    src = np.array(image.convert("RGBA"))
    op = src[:, :, 3] > 0
    r = src[:, :, 0].astype(np.intp)
    g = src[:, :, 1].astype(np.intp)
    bad = np.zeros(src.shape[:2], dtype=bool)
    bad[op] = pal.table[g[op], r[op], 3] == 0
    return bad


def coord_histogram(image: Image.Image) -> dict[tuple[int, int], int]:
    """Count opaque pixels per (red, green) table coordinate."""
    src = np.array(image.convert("RGBA"))
    op = src[:, :, 3] > 0
    if not op.any():
        return {}
    pairs = np.stack([src[:, :, 0][op], src[:, :, 1][op]], axis=1)
    vals, counts = np.unique(pairs, axis=0, return_counts=True)
    return {(int(v[0]), int(v[1])): int(c) for v, c in zip(vals, counts)}


def quadrant_of(red: int, green: int) -> str | None:
    """Which quadrant a coordinate falls in; None for the row/col 128 gutter."""
    if red == 128 or green == 128:
        return None
    return {(False, False): "ui", (True, False): "tertiary",
            (False, True): "secondary", (True, True): "primary"}[(red > 128, green > 128)]


# --------------------------------------------------------------------------
# quantizer
# --------------------------------------------------------------------------


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _as_rgb_array(palette: Iterable) -> np.ndarray:
    out = []
    for c in palette:
        out.append(_hex_to_rgb(c) if isinstance(c, str) else tuple(int(v) for v in c)[:3])
    if not out:
        raise ValueError("target palette is empty")
    return np.array(out, dtype=np.int32)


def palette_colors(team: str, quadrant: str | None = None,
                   palettes: PaletteSet | None = None) -> list[tuple[int, int, int]]:
    """Distinct opaque colors in a team table, optionally one quadrant only.

    This is the target the quantizer aims at when reducing generated art.
    """
    pal = (palettes or load_palettes())[team]
    t = pal.table
    if quadrant is None:
        cells = t.reshape(-1, 4)
    else:
        ox, oy = QUADRANT_ORIGINS[quadrant]
        cells = t[oy:oy + 127, ox:ox + 127].reshape(-1, 4)
    cells = cells[cells[:, 3] > 0][:, :3]
    if not len(cells):
        return []
    return [tuple(int(v) for v in c) for c in np.unique(cells, axis=0)]


def quantize(image: Image.Image, palette: Iterable, dither: bool = False,
             alpha_threshold: int = 128) -> Image.Image:
    """Snap every opaque pixel to its nearest color in `palette`.

    `palette` takes hex strings or RGB tuples. Distance is squared Euclidean in
    RGB -- predictable, which matters more than perceptual accuracy when the
    output has to round-trip exactly.

    Default is no dither: dithering scatters isolated pixels through flat
    regions, which is wrong for sprite work. `dither=True` uses Floyd-Steinberg.

    Pixels below `alpha_threshold` are forced fully transparent, the rest fully
    opaque -- AI output has soft edges and CoW sprites do not.
    """
    pal = _as_rgb_array(palette)
    src = np.array(image.convert("RGBA"))
    opaque = src[:, :, 3] >= alpha_threshold

    if dither:
        pal_img = Image.new("P", (1, 1))
        flat = [int(v) for c in pal for v in c][:768]
        flat += [0] * (768 - len(flat))
        pal_img.putpalette(flat)
        conv = Image.fromarray(src[:, :, :3], "RGB").quantize(
            palette=pal_img, dither=Image.FLOYDSTEINBERG)
        out_rgb = np.array(conv.convert("RGB"))
    else:
        flat = src[:, :, :3].reshape(-1, 3).astype(np.int32)
        # int32 throughout: a summed squared distance reaches ~195k, which
        # overflows int16 and silently picks the *wrong* nearest color.
        d = ((flat[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
        out_rgb = pal[d.argmin(axis=1)].astype(np.uint8).reshape(src.shape[0], src.shape[1], 3)

    out = np.zeros_like(src)
    out[:, :, :3] = out_rgb
    out[opaque, 3] = 255
    out[~opaque] = 0
    return Image.fromarray(out, "RGBA")


# --------------------------------------------------------------------------
# backgrounds (RESOLVED-5)
# --------------------------------------------------------------------------

GRADIENTS: dict[str, tuple[str, str, str]] = {
    "dawn": ("#2b3a67", "#8a6fa8", "#f0a07c"),
    "day": ("#4a90d9", "#8fc3ec", "#d6ecfa"),
    "dusk": ("#1f2b4d", "#7c4a72", "#e8865f"),
    "night": ("#070b1a", "#141d3a", "#2b3558"),
}

FLATS: dict[str, str] = {
    "key green": "#00ff00",
    "key magenta": "#ff00ff",
}

BACKGROUNDS: tuple[str, ...] = ("dawn", "day", "dusk", "night", "key green", "key magenta")


def make_background(name: str, size: tuple[int, int]) -> Image.Image:
    """An RGBA backdrop of `size`. Gradients run top -> middle -> bottom."""
    w, h = int(size[0]), int(size[1])
    if w < 1 or h < 1:
        raise ValueError(f"bad background size {size!r}")

    if name in FLATS:
        return Image.new("RGBA", (w, h), _hex_to_rgb(FLATS[name]) + (255,))
    if name not in GRADIENTS:
        raise KeyError(f"unknown background {name!r}; have {', '.join(BACKGROUNDS)}")

    top, mid, bot = (np.array(_hex_to_rgb(c), dtype=float) for c in GRADIENTS[name])
    ys = np.linspace(0.0, 1.0, h)[:, None]
    upper = top + (mid - top) * (ys / 0.5)
    lower = mid + (bot - mid) * ((ys - 0.5) / 0.5)
    col = np.where(ys < 0.5, upper, lower)

    out = np.empty((h, w, 4), dtype=np.uint8)
    out[:, :, :3] = np.repeat(col[:, None, :], w, axis=1).round().astype(np.uint8)
    out[:, :, 3] = 255
    return Image.fromarray(out, "RGBA")


def composite_on(image: Image.Image, background: str) -> Image.Image:
    """Flatten `image` onto a named background at the image's own size."""
    im = image.convert("RGBA")
    return Image.alpha_composite(make_background(background, im.size), im)


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------


def split_frames(image: Image.Image, count: int = 1) -> list[Image.Image]:
    """Split a horizontal strip into `count` equal frames.

    Never inferred -- map units happen to be width/height frames, but
    battleanim frame counts are not derivable from dimensions. Caller supplies.
    """
    count = max(1, int(count))
    if count == 1:
        return [image.copy()]
    w, h = image.size
    if w % count:
        raise ValueError(f"width {w} is not divisible by {count} frames")
    fw = w // count
    return [image.crop((i * fw, 0, (i + 1) * fw, h)) for i in range(count)]


def export_name(sprite_name: str, palette_name: str) -> str:
    """RESOLVED-4: `sprite_name+palette_name.png`.

    A shipped sprite is `apc+mask.png`; the `+mask` suffix marks the
    index-encoded form, so it is dropped before appending the team name.
    """
    stem = pathlib.Path(sprite_name).stem
    if stem.endswith("+mask"):
        stem = stem[: -len("+mask")]
    return f"{stem}+{palette_name}.png"
