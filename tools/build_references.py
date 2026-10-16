"""Build the per-unit reference gallery from every army in `pipeline/source/`.

AW2 draws each unit differently per army -- the Yellow Comet fighter is a
biplane, the Black Hole soldier wears a diving helmet -- so the four non-red
armies are 76 genuinely new designs, not recolors (measured: only the Neo Tank
is the same drawing in every army). This script recolors each one to Orange
Star red, puts it on flat #00ff00, and writes a native-size and a 6x copy, so a
user steering the generator toward a look has a large gallery to pick from.

Outputs, under pipeline/reference/units/:
  <Unit> - <Army>.png       6x nearest-neighbour upscale  <- attach this one
  1x/<Unit> - <Army>.png    native size

Plus square category collages in pipeline/reference/: sheet_<infantry|air|ground|sea>.png
(6x) and sheet_<...>_1x.png. The older per-class sheets are left alone.

How the recolor works
---------------------
Each color in, say, a Blue Moon sprite is resolved by the first rule that
applies:

1. **AW2 neutrals stay.** A color drawn by all five armies -- blacks, whites,
   skin, tread greys, and the three water blues -- is not a team color, so it
   is kept. This rule exists because of water: Blue Moon's pale water blue is
   also a highlight shade in Blue Moon's table, and rule 3 alone turned every
   Blue Moon ship's water pink.
2. **Same-drawing calibration.** The Neo Tank is the identical drawing in every
   army, so it gives an exact pixel-for-pixel army -> Orange Star mapping for
   every color it contains. Those pairs win over the table.
3. **CoW's colortables.** The same cell holds Blue Moon's blue in
   blue_moon.png and the matching red in orange_star.png. Every cell holding
   the color votes for its Orange Star counterpart, weighted by how many pixels
   CoW's own stock sprites draw from that cell -- so real unit art outweighs
   whatever swatch happens to share the color. A color absent from the table
   borrows its nearest table color's vote within MAX_SNAP, else it is kept.

Rule 3 results are snapped to the real AW2 Orange Star palette when one is
within SNAP_TO_OS, so the gallery uses the same reds as the original sprites.

Run: python tools/build_references.py
"""

from __future__ import annotations

import collections
import pathlib
import sys

import numpy as np
from PIL import Image

PROJ = pathlib.Path(__file__).resolve().parent.parent
SRC = PROJ / "pipeline" / "source"
STOCK = PROJ / "samples" / "cow battle animations"
OUT = PROJ / "pipeline" / "reference" / "units"
sys.path.insert(0, str(PROJ / "tools"))
import cowpalette as cp  # noqa: E402

GREEN = (0, 255, 0, 255)
SCALE = 6
PAD = 2  # px of green around each sprite at 1x
MAX_SNAP = 24
SNAP_TO_OS = 20

ARMIES = {
    "Orange Star": "orange_star",
    "Blue Moon": "blue_moon",
    "Green Earth": "green_earth",
    "Yellow Comet": "yellow_comet",
    "Grey Blackhole": "black_hole",
}

RGB = tuple[int, int, int]


def stock_cell_usage() -> collections.Counter:
    """Pixels per (red, green) cell across CoW's shipped sprites."""
    use: collections.Counter = collections.Counter()
    for p in STOCK.rglob("*.png"):
        use.update(cp.coord_histogram(Image.open(p).convert("RGBA")))
    return use


def opaque_colors(paths) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for p in paths:
        a = np.array(Image.open(p).convert("RGBA"))
        c.update(tuple(int(v) for v in px) for px in a[a[..., 3] > 0][:, :3])
    return c


def calibration(army: str) -> dict[RGB, RGB]:
    """Exact color pairs from units drawn identically in `army` and Orange Star.

    A unit qualifies when its silhouette matches pixel for pixel; then each
    color's Orange Star counterpart is its majority partner at the same pixels.
    """
    pairs: dict[RGB, collections.Counter] = collections.defaultdict(collections.Counter)
    for f in SRC.glob(f"{army} *.png"):
        twin = SRC / f"Orange Star {f.stem[len(army) + 1:]}.png"
        a = np.array(Image.open(f).convert("RGBA"))
        b = np.array(Image.open(twin).convert("RGBA"))
        if a.shape != b.shape or ((a[..., 3] > 0) != (b[..., 3] > 0)).any():
            continue
        m = a[..., 3] > 0
        for x, y in zip(a[m][:, :3], b[m][:, :3]):
            pairs[tuple(int(v) for v in x)][tuple(int(v) for v in y)] += 1
    return {c: votes.most_common(1)[0][0] for c, votes in pairs.items()}


def army_map(team: str, colors, ps, usage, os_palette: np.ndarray,
             neutrals: set = frozenset(), calibrated: dict | None = None) -> dict[RGB, RGB]:
    """Map every color an army's sprites use to its Orange Star counterpart."""
    calibrated = calibrated or {}
    src = ps[team].table
    dst = ps["orange_star"].table

    # color in this army's table -> weighted votes for an Orange Star color
    votes: dict[RGB, collections.Counter] = collections.defaultdict(collections.Counter)
    for y, x in zip(*np.nonzero(src[..., 3])):
        if not dst[y, x, 3]:
            continue
        here = tuple(int(v) for v in src[y, x, :3])
        there = tuple(int(v) for v in dst[y, x, :3])
        # +1 keeps unused cells in play as a tiebreak-free fallback, while any
        # cell real art draws from outweighs them.
        votes[here][there] += 1 + usage.get((int(x), int(y)), 0) * 100

    table_colors = np.array(list(votes), dtype=np.int32)
    keys = list(votes)

    out: dict[RGB, RGB] = {}
    for c in colors:
        if c in neutrals:
            out[c] = c
            continue
        if c in calibrated:
            out[c] = calibrated[c]
            continue
        if c in votes:
            target = votes[c].most_common(1)[0][0]
        else:
            d = np.sqrt(((table_colors - np.array(c)) ** 2).sum(1))
            i = int(d.argmin())
            target = votes[keys[i]].most_common(1)[0][0] if d[i] <= MAX_SNAP else c
        # Prefer the exact AW2 red when one is close: CoW's table shades drift a
        # little from AW2's, and the gallery should match the original sprites.
        d = np.sqrt(((os_palette - np.array(target)) ** 2).sum(1))
        i = int(d.argmin())
        out[c] = tuple(int(v) for v in os_palette[i]) if d[i] <= SNAP_TO_OS else target
    return out


def recolor(path: pathlib.Path, mapping: dict[RGB, RGB]) -> Image.Image:
    a = np.array(Image.open(path).convert("RGBA"))
    m = a[..., 3] > 0
    px = a[m]
    for i, p in enumerate(px):
        px[i, :3] = mapping[tuple(int(v) for v in p[:3])]
    a[m] = px
    return Image.fromarray(a, "RGBA")


def on_green(im: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", (im.width + 2 * PAD, im.height + 2 * PAD), GREEN)
    bg.paste(im, (PAD, PAD), im)
    return bg.convert("RGB")


# Category collages: square sheets, units placed on an invisible square grid
# (ceil(sqrt(n)) per side) with leftover slots left as plain green.
CATEGORIES = {
    "infantry": ("Soldier Rifle", "Soldier Bazooka"),
    "air": ("Heli Battle", "Heli Transport", "Plane Fighter", "Planet Bomber"),
    "sea": ("Battleship", "Cruiser", "Lander Boat", "Submarine (Surfaced)"),
    "ground": ("APC", "Recon Car", "Tank Light", "Tank Heavy", "Neo Tank",
               "Mobile AA Gun", "Mobile AA Missle", "Mobile Arty Shell", "Mobile Arty Rocket"),
}
SHEET_DIR = PROJ / "pipeline" / "reference"


def build_sheets() -> None:
    for cat, units in CATEGORIES.items():
        ims = []
        for u in units:
            for army in ARMIES:
                f = OUT / "1x" / f"{u} - {army}.png"
                if f.exists():
                    ims.append(Image.open(f).convert("RGB"))
        side = int(np.ceil(np.sqrt(len(ims))))
        cell = max(max(i.width, i.height) for i in ims)
        sheet = Image.new("RGB", (side * cell, side * cell), GREEN[:3])
        for k, im in enumerate(ims):
            r, c = divmod(k, side)
            sheet.paste(im, (c * cell + (cell - im.width) // 2, r * cell + (cell - im.height) // 2))
        sheet.save(SHEET_DIR / f"sheet_{cat}_1x.png")
        sheet.resize((sheet.width * SCALE, sheet.height * SCALE),
                     Image.NEAREST).save(SHEET_DIR / f"sheet_{cat}.png")
        print(f"  sheet_{cat}.png  {len(ims)} units, {side}x{side} slots, "
              f"{sheet.width * SCALE}px square")


def main() -> int:
    ps = cp.load_palettes()
    usage = stock_cell_usage()
    os_counts = opaque_colors(SRC.glob("Orange Star *.png"))
    os_palette = np.array(list(os_counts), dtype=np.int32)
    neutrals = set.intersection(*(set(opaque_colors(SRC.glob(f"{a} *.png"))) for a in ARMIES))

    (OUT / "1x").mkdir(parents=True, exist_ok=True)
    seen: dict[bytes, str] = {}
    written = skipped = 0
    for army, team in ARMIES.items():
        files = sorted(SRC.glob(f"{army} *.png"))
        mapping = army_map(team, opaque_colors(files), ps, usage, os_palette,
                           neutrals, calibration(army))
        for f in files:
            unit = f.stem[len(army) + 1:]
            im = recolor(f, mapping) if team != "orange_star" else Image.open(f).convert("RGBA")
            small = on_green(im)
            # An army whose drawing is the Orange Star one (the Neo Tank)
            # recolors to an exact duplicate; one copy is enough.
            sig = small.tobytes() + bytes(small.size)
            name = f"{unit} - {army}"
            if sig in seen:
                skipped += 1
                print(f"  same as {seen[sig]}: {name} skipped")
                continue
            seen[sig] = name
            small.save(OUT / "1x" / f"{name}.png")
            small.resize((small.width * SCALE, small.height * SCALE),
                         Image.NEAREST).save(OUT / f"{name}.png")
            written += 1
    build_sheets()
    print(f"wrote {written} references to {OUT.relative_to(PROJ)} ({skipped} duplicates skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
