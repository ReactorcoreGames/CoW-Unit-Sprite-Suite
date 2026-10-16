"""Verification harness: recolor the vendored ground-truth sprites across teams.

Proves the rule in docs/cow-palette-format.md, taken from
SpriteCreator::recolorImageWithTable:

  out = table.pixel(src.red, src.green)   i.e. table[green, red]

Writes reference/verify_teams.png -- whole units should recolor per column
(hull, skin, glass), while treads/wheels stay grey and ship wakes stay white.
"""
import pathlib

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
REF = HERE.parent / "reference"
TABLES = HERE.parent / "reference" / "colortables"
SPRITES = HERE.parent / "samples" / "cow map units"

TEAMS = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole"]
SCALE = 8


def table_of(name):
    return np.array(Image.open(TABLES / f"{name}.png").convert("RGBA"))


def recolor(path, team):
    """out = table[green, red]; transparent source pixels stay transparent."""
    table = table_of(team)
    src = np.array(Image.open(path).convert("RGBA"))
    out = np.zeros_like(src)
    op = src[:, :, 3] > 0
    out[op] = table[src[:, :, 1][op].astype(int), src[:, :, 0][op].astype(int)]
    out[op, 3] = src[op, 3]
    return Image.fromarray(out, "RGBA")


def main():
    srcs = [p for p in sorted(SPRITES.glob("*.png")) if p.stem.split("+")[0] in ("apc","light_tank","bomber","battleship","recon","megatank")]
    if not srcs:
        raise SystemExit(f"no sprites in {SPRITES}")
    # Sprites are multi-frame strips of varying width; show only the first
    # frame (a square cell of side == image height) so the sheet stays legible.
    tiles = []
    for s in srcs:
        row = []
        for t in TEAMS:
            im = recolor(s, t)
            n = min(im.height, im.width)
            row.append(im.crop((0, 0, n, n)))
        tiles.append(row)
    cw = max(im.width for r in tiles for im in r)
    ch = max(im.height for r in tiles for im in r)
    sheet = Image.new(
        "RGBA", (cw * len(TEAMS) * SCALE, ch * len(srcs) * SCALE), (0, 255, 0, 255)
    )
    for ri, row in enumerate(tiles):
        for ci, im in enumerate(row):
            big = im.resize((im.width * SCALE, im.height * SCALE), Image.NEAREST)
            sheet.paste(big, (ci * cw * SCALE, ri * ch * SCALE), big)
    dest = REF / "verify_teams.png"
    sheet.save(dest)
    print("wrote", dest, sheet.size)
    print("rows:", [s.name for s in srcs])
    print("cols:", TEAMS)


if __name__ == "__main__":
    main()
