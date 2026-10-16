"""Phase 5.5 -- the sheet splitter.

Cuts a sheet of many units apart and runs each one through the Phase 5
conversion chain. Built for the **hand-authored** case first: an artist lays
every unit out on one canvas, because comparing sizes side by side is easier
there than across separate files, and then wants them cut out and converted
with as little manual work as possible.

    python tools/split.py SHEET.png OUT [--names names.txt] [--class all]

The order is **split first, detect the grid per sprite afterwards**. Running
`convert.detect_grid` on a whole sheet locks onto the spacing *between* units --
the stronger periodic signal -- and reduces a 6x sheet by 14x at high
confidence. No tuning fixes that; it answers a different question.

Per sprite, the scale is found in one of two ways:

1. **Exact.** A hand-authored sheet saved as PNG is a true nearest-neighbour
   upscale (or 1x), so every color change falls on a pixel boundary. The
   greatest common divisor of the change positions *is* the scale, and its
   phase is the offset. This is lossless, works at 1x, and needs each sprite to
   be aligned only to its own grid -- units pasted at arbitrary positions are
   fine.
2. **Detected.** When there is no exact grid (JPEG damage, AI output), fall back
   to `convert.detect_grid` on that sprite alone. Sprites whose pitch disagrees
   with the sheet's consensus go to `needs-review/` rather than being guessed.

Pieces are found as connected components of the opaque mask, then nearby
components are merged so detached parts -- a missile's shadow, a rotor -- stay
with their unit. The join distance is in *logical* pixels, so it means the same
thing at any scale.

Naming is positional (`sheet_01`, `sheet_02`... in reading order) unless a names
file gives one name per line in the same order (`--names`, or a
`<sheet>.names.txt` beside the sheet, picked up automatically).
`rgb/<sheet>.index.png` is written with every piece boxed and numbered, so
writing that file is a matter of reading it.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import pathlib
import sys
from functools import reduce

import numpy as np
from PIL import Image, ImageDraw

import convert
import cowpalette

SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".bmp"}


# --------------------------------------------------------------------------
# connected components
# --------------------------------------------------------------------------


def label_components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """8-connected components of a boolean mask, as (x0, y0, x1, y1, area).

    Run-based union-find: each row's opaque runs are unioned with the runs they
    touch in the row above. A sheet has a few thousand runs, so this is fast in
    plain Python and keeps scipy out of the release build.
    """
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs: list[tuple[int, int, int]] = []  # (y, x0, x1) half-open
    prev: list[int] = []
    for y in range(mask.shape[0]):
        row = mask[y].astype(np.int8)
        d = np.diff(np.concatenate(([0], row, [0])))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        cur = []
        j = 0
        for x0, x1 in zip(starts.tolist(), ends.tolist()):
            idx = len(runs)
            runs.append((y, x0, x1))
            parent.append(idx)
            cur.append(idx)
            # 8-connectivity: a run above touches if it overlaps [x0-1, x1+1)
            while j < len(prev) and runs[prev[j]][2] < x0:
                j += 1
            k = j
            while k < len(prev) and runs[prev[k]][1] <= x1:
                a, b = find(prev[k]), find(idx)
                if a != b:
                    parent[b] = a
                k += 1
        prev = cur

    boxes: dict[int, list[int]] = {}
    for idx, (y, x0, x1) in enumerate(runs):
        r = find(idx)
        b = boxes.get(r)
        if b is None:
            boxes[r] = [x0, y, x1, y + 1, x1 - x0]
        else:
            b[0] = min(b[0], x0); b[1] = min(b[1], y)
            b[2] = max(b[2], x1); b[3] = max(b[3], y + 1)
            b[4] += x1 - x0
    return [tuple(b) for b in boxes.values()]


def _gap(a, b) -> int:
    """Chebyshev gap between two boxes in px; 0 when they touch or overlap."""
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return max(dx, dy)


def merge_boxes(boxes: list, join: float) -> list:
    """Merge boxes whose gap is at most `join` px, to a fixed point."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[int]] = []
        for b in boxes:
            for o in out:
                if _gap(o, b) <= join:
                    o[0] = min(o[0], b[0]); o[1] = min(o[1], b[1])
                    o[2] = max(o[2], b[2]); o[3] = max(o[3], b[3])
                    o[4] += b[4]
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return [tuple(b) for b in boxes]


def reading_order(boxes: list) -> list:
    """Sort into rows (by vertical overlap with the row's first box), then by x."""
    rows: list[list] = []
    for b in sorted(boxes, key=lambda b: (b[1], b[0])):
        cy = (b[1] + b[3]) / 2
        for row in rows:
            top, bottom = row[0][1], row[0][3]
            if top <= cy < bottom:
                row.append(b)
                break
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: b[0])]


# --------------------------------------------------------------------------
# exact scale
# --------------------------------------------------------------------------


def exact_scale(rgba: np.ndarray) -> tuple[int, int, int, int] | None:
    """The integer scale and phase of an exact nearest-neighbour upscale.

    Returns (sx, sy, ox, oy), or None when the crop has no exact grid (a scale
    of 1 is still an answer -- that is 1x art -- but a crop whose change
    positions share no divisor *and* whose runs are mostly short reads as noise,
    not as 1x art, so it is reported as None and goes to detection).
    """
    a = rgba.copy()
    a[a[..., 3] == 0] = 0  # transparent is one color, whatever its RGB
    v = a.view(np.uint32).reshape(a.shape[:2])
    # pad with transparency so the silhouette's own edges count as changes
    v = np.pad(v, 1)

    out = []
    for axis in (1, 0):
        # diff index i is the boundary just before unpadded pixel i
        diff = np.any(np.diff(v, axis=axis) != 0, axis=1 - axis)
        pos = np.flatnonzero(diff)
        if len(pos) < 2:
            return None
        step = reduce(math.gcd, (pos[1:] - pos[0]).tolist())
        out.append((step, int(pos[0] % step) if step else 0))
    (sx, ox), (sy, oy) = out
    if sx < 1 or sy < 1:
        return None
    if sx == 1 or sy == 1:
        # Genuine 1x art has a sane run structure; noise has single-pixel runs
        # nearly everywhere. Check the share of distinct colors -- 1x pixel art
        # uses a handful, a damaged upscale thousands.
        opaque = a[..., 3] > 0
        n = len(np.unique(v[1:-1, 1:-1][opaque]))
        if n > 256:
            return None
    return sx, sy, ox, oy


def exact_downscale(rgba: np.ndarray, sx: int, sy: int, ox: int, oy: int) -> np.ndarray:
    """Sample one pixel per cell. Lossless for an exact upscale."""
    x0, y0 = ox % sx, oy % sy
    # include a partial leading cell if the phase leaves one
    xs = np.arange(x0 - sx if x0 else 0, rgba.shape[1], sx)
    ys = np.arange(y0 - sy if y0 else 0, rgba.shape[0], sy)
    xs = np.clip(xs + sx // 2, 0, rgba.shape[1] - 1)
    ys = np.clip(ys + sy // 2, 0, rgba.shape[0] - 1)
    small = rgba[np.ix_(ys, xs)]
    small[small[..., 3] < 128] = 0
    small[small[..., 3] >= 128, 3] = 255
    return small


def _trim(rgba: np.ndarray) -> np.ndarray:
    op = rgba[..., 3] > 0
    if not op.any():
        return rgba[:0, :0]
    ys, xs = np.flatnonzero(op.any(axis=1)), np.flatnonzero(op.any(axis=0))
    return rgba[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]


# --------------------------------------------------------------------------
# keying the sheet
# --------------------------------------------------------------------------


def key_sheet(image: Image.Image, key: str = "green", tolerance: int = 90,
              fringe: int = 2) -> tuple[np.ndarray, bool]:
    """Key the backdrop out. Returns (rgba, strict).

    A hand-authored sheet uses the exact key color, and keying it strictly is
    both lossless and safe for art that is itself near the key. Strict keying is
    used whenever it leaves no near-key fringe behind; otherwise the tolerant
    `convert.chroma_key` takes over.
    """
    src = np.array(image.convert("RGBA"))
    k = np.array(convert.CHROMA_KEYS[key], dtype=np.int32)
    rgb = src[..., :3].astype(np.int32)
    exact = np.all(rgb == k, axis=2) | (src[..., 3] < 128)

    dist = np.sqrt(((rgb - k) ** 2).sum(axis=2))
    near = (dist <= tolerance) & ~exact
    if near.sum() <= 0.0005 * near.size:
        out = src.copy()
        out[exact] = 0
        out[~exact, 3] = 255
        return out, True
    keyed = convert.chroma_key(image, key=key, tolerance=tolerance, fringe=fringe)
    return np.array(keyed), False


# --------------------------------------------------------------------------
# the splitter
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Piece:
    """One unit cut from a sheet, through the conversion chain."""

    index: int
    name: str
    box: tuple[int, int, int, int]       # in sheet px, half-open
    method: str = ""                     # "exact" or "detected"
    scale: tuple[float, float] = (1.0, 1.0)
    result: convert.ConversionResult | None = None

    @property
    def needs_review(self) -> bool:
        return self.result is None or self.result.needs_review

    @property
    def reason(self) -> str:
        return self.result.reason if self.result else "not converted"


@dataclasses.dataclass
class SplitResult:
    sheet: str
    pieces: list[Piece]
    dropped: list[tuple[int, int, int, int]]  # specks below min_area
    strict_key: bool
    consensus: tuple[float, float] | None

    def index_image(self, image: Image.Image) -> Image.Image:
        """The sheet with every piece boxed and numbered, for writing a names file."""
        im = image.convert("RGB").copy()
        d = ImageDraw.Draw(im)
        for p in self.pieces:
            x0, y0, x1, y1 = p.box
            color = (255, 0, 255) if p.needs_review else (0, 0, 0)
            d.rectangle([x0 - 2, y0 - 2, x1 + 1, y1 + 1], outline=color, width=2)
            label = f"{p.index:02d}"
            tw = d.textlength(label)
            d.rectangle([x0 - 2, y0 - 14, x0 + tw + 2, y0 - 2], fill=color)
            d.text((x0, y0 - 13), label, fill=(255, 255, 255))
        return im


def split_sheet(image: Image.Image, unit_class: str | convert.ClassPalette = convert.ALL_CLASSES,
                *, name: str = "sheet", names: list[str] | None = None,
                key: str = "green", tolerance: int = 90, fringe: int = 2,
                join: float = 2.0, min_area: int = 4,
                scale: int | None = None, pitch_tolerance: float = 0.05,
                confidence_floor: float = 0.55) -> SplitResult:
    """Cut a sheet into units and convert each one.

    `join` is in logical pixels: components closer than that belong to one unit.
    `min_area` is in logical pixels: smaller pieces are dropped as specks.
    `scale` forces an integer scale for every piece instead of finding it.
    """
    pal = convert._resolve_class(unit_class)
    rgba, strict = key_sheet(image, key, tolerance, fringe)
    mask = rgba[..., 3] > 0
    raw = label_components(mask)
    if not raw:
        return SplitResult(name, [], [], strict, None)

    # Join distance needs a scale, and the scale is per piece -- so estimate a
    # sheet-wide one from the larger raw components first.
    def piece_scale(box) -> tuple[int, int] | None:
        if scale:
            return scale, scale
        x0, y0, x1, y1, _ = box
        sub = rgba[y0:y1, x0:x1].copy()
        sub[~mask[y0:y1, x0:x1]] = 0
        s = exact_scale(sub)
        return (s[0], s[1]) if s else None

    big = sorted(raw, key=lambda b: -b[4])[:8]
    est = [s for s in map(piece_scale, big) if s]
    if est:
        s_est = float(np.median([max(s) for s in est]))
    else:
        s_est = None
        for b in big:
            try:
                g = convert.detect_grid(Image.fromarray(rgba[b[1]:b[3], b[0]:b[2]]))
                s_est = max(g.pitch_x, g.pitch_y)
                break
            except convert.GridDetectionError:
                continue
        s_est = s_est or 1.0

    merged = merge_boxes(raw, join * s_est)
    keep = [b for b in merged if b[4] >= min_area * s_est * s_est]
    dropped = [b[:4] for b in merged if b[4] < min_area * s_est * s_est]
    keep = reading_order(keep)

    pieces: list[Piece] = []
    for i, b in enumerate(keep, 1):
        nm = (names[i - 1] if names and i - 1 < len(names) and names[i - 1]
              else f"{name}_{i:02d}")
        pieces.append(Piece(i, nm, tuple(int(v) for v in b[:4])))

    # cut every piece out and look for an exact grid
    subs, exes = [], []
    for p in pieces:
        x0, y0, x1, y1 = p.box
        sub = rgba[y0:y1, x0:x1].copy()
        # Only components wholly inside the box: a neighbour's corner can poke in.
        sub_mask = np.zeros_like(mask[y0:y1, x0:x1])
        for b in raw:
            if b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1:
                sub_mask |= _component_mask(mask, b, p.box)
        sub[~sub_mask] = 0
        subs.append(sub)
        exes.append((scale, scale, *_phase(sub, scale)) if scale else exact_scale(sub))

    # A sprite whose every feature happens to be 2 logical px wide has a GCD of
    # twice the true scale. The sheet's most common exact scale settles it: a
    # whole multiple of it is redone at it, which is still lossless, because a
    # grid of 2s is also a grid of s.
    found = [(e[0], e[1]) for e in exes if e]
    if found and not scale:
        cx, cy = max(set(found), key=found.count)
        for k, e in enumerate(exes):
            if e and (e[0], e[1]) != (cx, cy) and e[0] % cx == 0 and e[1] % cy == 0:
                exes[k] = (cx, cy, e[2] % cx, e[3] % cy)

    # convert every piece
    detected: list[Piece] = []
    for p, sub, ex in zip(pieces, subs, exes):
        if ex:
            sx, sy, ox, oy = ex
            small = _trim(exact_downscale(sub, sx, sy, ox, oy))
            p.method, p.scale = "exact", (float(sx), float(sy))
            p.result = _finish(Image.fromarray(small, "RGBA"), pal, p.name,
                               keyed=Image.fromarray(sub, "RGBA"),
                               grid=convert.Grid(sx, sy, ox, oy, small.shape[1],
                                                 small.shape[0], 1.0))
        else:
            p.method = "detected"
            res = convert.convert_image(Image.fromarray(sub, "RGBA"), pal, name=p.name,
                                        key=key, tolerance=tolerance, fringe=0,
                                        confidence_floor=confidence_floor)
            p.result = res
            if res.grid:
                p.scale = (res.grid.pitch_x, res.grid.pitch_y)
                detected.append(p)

    # Hand-authored sheets hold one scale. A detected piece that disagrees with
    # the rest is the likeliest wrong answer on the sheet.
    consensus = None
    scales = [p.scale for p in pieces if p.result and not p.result.needs_review]
    if scales:
        consensus = (float(np.median([s[0] for s in scales])),
                     float(np.median([s[1] for s in scales])))
        for p in detected:
            if p.result.needs_review:
                continue
            dev = max(abs(p.scale[0] / consensus[0] - 1), abs(p.scale[1] / consensus[1] - 1))
            if dev > pitch_tolerance:
                p.result.needs_review = True
                p.result.reason = (f"pitch {p.scale[0]:.2f}x{p.scale[1]:.2f} disagrees with the "
                                   f"sheet's {consensus[0]:.2f}x{consensus[1]:.2f} by {dev:.0%}")
    return SplitResult(name, pieces, dropped, strict, consensus)


def _component_mask(mask, comp, box) -> np.ndarray:
    """The opaque pixels of `comp`'s box, clipped to `box`'s frame."""
    x0, y0, x1, y1 = box
    out = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    cx0, cy0, cx1, cy1 = comp[:4]
    out[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = mask[cy0:cy1, cx0:cx1]
    return out


def _phase(sub: np.ndarray, s: int) -> tuple[int, int]:
    """Phase of a forced scale: where the first opaque column and row start."""
    op = sub[..., 3] > 0
    ys, xs = np.flatnonzero(op.any(axis=1)), np.flatnonzero(op.any(axis=0))
    return (int(xs[0] % s), int(ys[0] % s)) if len(xs) else (0, 0)


def _finish(small: Image.Image, pal, name: str, *, keyed=None, grid=None) -> convert.ConversionResult:
    """Quantize, encode and validate a piece that is already at 1x."""
    res = convert.ConversionResult(name=name, unit_class=pal.name, grid=grid,
                                   keyed=keyed, snapped=small)
    res.quantized = convert.quantize_to_class(small, pal)
    res.encoded = convert.encode(res.quantized, pal)
    res.validation = convert.validate(res.encoded)
    if not res.validation["ok"]:
        res.needs_review = True
        res.reason = (f"{res.validation['undefined_worst']} pixel(s) undefined "
                      f"in at least one team table")
    return res


def read_names(path: pathlib.Path) -> list[str]:
    """One name per line, in reading order. `#` comments skipped; `-` keeps the default."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        out.append("" if line in ("", "-") else line)
    # trailing blank lines are not entries
    while out and not out[-1]:
        out.pop()
    return out


def names_file_for(sheet: pathlib.Path) -> pathlib.Path | None:
    """`<sheet>.names.txt` beside the sheet, if there is one."""
    p = sheet.with_name(sheet.stem + ".names.txt")
    return p if p.is_file() else None


def write_outputs(res: SplitResult, image: Image.Image, out: pathlib.Path) -> tuple[int, int]:
    """Write encoded sprites, their 1x RGB, review cases and the index image."""
    out.mkdir(parents=True, exist_ok=True)
    rgb_dir = out / "rgb"
    review = out / "needs-review"
    done = flagged = 0
    for p in res.pieces:
        r = p.result
        if r is None or r.needs_review:
            flagged += 1
            review.mkdir(parents=True, exist_ok=True)
            for tag, img in (("keyed", r and r.keyed), ("snapped", r and r.snapped)):
                if img is not None:
                    img.save(review / f"{p.name}.{tag}.png")
            (review / f"{p.name}.txt").write_text(p.reason + "\n", encoding="utf-8")
            continue
        rgb_dir.mkdir(exist_ok=True)
        r.quantized.save(rgb_dir / f"{p.name}.png")
        # `+mask` is CoW's own suffix for the index-encoded form.
        r.encoded.save(out / f"{p.name}+mask.png")
        done += 1
    # Beside the RGB pieces rather than the masks, so a folder of masks stays
    # loadable in the previewer as-is.
    rgb_dir.mkdir(exist_ok=True)
    res.index_image(image).save(rgb_dir / f"{res.sheet}.index.png")
    return done, flagged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet", type=pathlib.Path, help="the sheet image")
    ap.add_argument("output", type=pathlib.Path, help="folder to write sprites to")
    ap.add_argument("--names", type=pathlib.Path,
                    help="text file, one sprite name per line in reading order "
                         "('-' or blank keeps the positional name); default: "
                         "SHEET.names.txt beside the sheet, if present")
    ap.add_argument("--class", dest="unit_class", default=convert.ALL_CLASSES,
                    help="unit class from pipeline/classes.json (default: all)")
    ap.add_argument("--key", default="green", choices=sorted(convert.CHROMA_KEYS),
                    help="backdrop color; transparent sheets need no key (default: green)")
    ap.add_argument("--tolerance", type=int, default=90,
                    help="chroma key radius when the backdrop is not exact (default: 90)")
    ap.add_argument("--join", type=float, default=2.0,
                    help="merge parts closer than this many logical px (default: 2)")
    ap.add_argument("--min-area", type=int, default=4,
                    help="drop specks smaller than this many logical px (default: 4)")
    ap.add_argument("--scale", type=int,
                    help="force this integer scale for every sprite instead of finding it")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.sheet.is_file():
        ap.error(f"{args.sheet} is not a file")
    names_path = args.names or names_file_for(args.sheet)
    names = read_names(names_path) if names_path else None
    with Image.open(args.sheet) as im:
        image = im.copy()

    try:
        res = split_sheet(image, args.unit_class, name=args.sheet.stem, names=names,
                          key=args.key, tolerance=args.tolerance, join=args.join,
                          min_area=args.min_area, scale=args.scale)
    except KeyError as exc:
        ap.error(str(exc))
    done, flagged = write_outputs(res, image, args.output)

    if not args.quiet:
        for p in res.pieces:
            tag = "??" if p.needs_review else "ok"
            size = (f"{p.result.snapped.width}x{p.result.snapped.height}"
                    if p.result and p.result.snapped else "-")
            extra = f"  {p.reason}" if p.needs_review else ""
            print(f"  {tag} {p.index:02d} {p.name:<24} {size:>7}  {p.method} "
                  f"{p.scale[0]:g}x{p.scale[1]:g}{extra}")
    if names and len(names) != len(res.pieces):
        print(f"warning: {len(names)} names for {len(res.pieces)} sprites -- "
              f"check rgb/{res.sheet}.index.png", file=sys.stderr)
    print(f"\n{len(res.pieces)} sprites: {done} converted, {flagged} need review"
          + (f", {len(res.dropped)} specks dropped" if res.dropped else "")
          + ("" if res.strict_key else "  (backdrop was not exact; tolerant key used)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
