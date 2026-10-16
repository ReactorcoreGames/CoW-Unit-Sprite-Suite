"""The AI sprite fixer -- turns an AI-generated sheet into rough CoW sprites.

Built for one input: a battle-scale sheet from an image model (Nano Banana 2
Lite in edit mode, as of October 2026), a grid of units in Orange Star-ish
colors on a chroma backdrop, saved as JPEG. `split.py` cannot cut these. It
looks for each unit's own pixel grid, and an AI unit does not have one -- the
per-unit pitch comes back anywhere from 3 to 30 px -- and units in one column
touch through their drop shadows, so the component cutter merges them.

What these sheets *do* have is a known layout and one scale across the whole
sheet, so the fixer works per sheet instead of per unit:

    key  ->  cut by layout  ->  isolate  ->  measure grain  ->  snap at one pitch
         ->  (even out)  ->  quantize  ->  merge specks  ->  encode  ->  validate

1. **Key.** The backdrop color is sampled from the sheet's border rather than
   assumed, because a generator's "pure green" never is. Edges are despilled so
   the green halo does not survive into the modal vote.
2. **Cut by layout.** The sheet is a known rows x cols grid (the prompt asked
   for one). Cut lines are placed at the emptiest column/row near each expected
   boundary, so units that touch are still separated.
3. **Isolate.** Inside a cell, the largest component is the unit; parts close
   to it (a rotor, a muzzle flash) stay, and a neighbour's corner poking in is
   dropped.
4. **Grain.** The edge-energy autocorrelation, summed over every unit, peaks at
   multiples of the grain. Autocorrelation ignores phase, which is what lets
   sixteen units with sixteen different grid offsets pool their evidence. The
   grain is weak evidence -- some sheets have none -- so it only sets the
   finest pitch allowed, never the size.
5. **Snap.** Every unit is reduced at the *same* pitch, with each unit's own
   offset fitted, through `convert.pixel_snap`'s modal vote. One pitch per
   sheet keeps the units' sizes relative to each other. The default pitch is
   the grain -- the sheet's native size, about twice CoW's ~48 px battle
   sprites on a Nano Banana sheet -- and a target width for the median unit
   can ask for smaller (`Sheet.native_pitch`, `Sheet.pitch_for_width`).
6. **Even out** (optional, per unit). Pulls a drifted unit's body colors -- a
   peach SUV on a sheet of red ones -- toward Orange Star's usual tones. Off by
   default: on art that is already right it costs accuracy.
7. **Quantize.** Not the pipeline's nearest-RGB: JPEG shifts colors onto the
   palette's near-twins, some of which sit in quadrants that do not follow the
   team color. Weighted-YCbCr matching with a per-sheet usage prior gets 93% of
   pixels rendering right in all 32 teams on a synthetic JPEG sheet, against
   83% for nearest-RGB, and stays exact on lossless art. See
   `quantize_pixels`. Body colors are kept on cells that follow the team
   color, so no orange patch survives into another army.
8. **Merge specks.** Tiny same-color regions fold into a similar neighbour.
   Outlines and shadows are left alone: measured, they already match CoW's
   own sprites. See `merge_specks` for what was tried and why it stops there.

The work splits in two: `prepare_sheet` (key, cut, isolate, grain) is the slow
half and does not depend on the pitch; `snap_sheet` is the fast half, so a GUI
size slider re-runs only that.

Encode and validate are the conversion pipeline's, unchanged. The result is a
starting point for hand-fixing, not a finished sprite.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import os
import pathlib

import numpy as np
from PIL import Image

import convert
import cowpalette
import split

# Pitches the grain search considers, in source px per logical px.
GRAIN_RANGE = (2.0, 8.0)

# After the snap, parts further than SPECK_JOIN logical px from the unit are
# dropped as specks; parts of SPECK_AREA px or more extend the unit's reach
# (see `isolate`).
SPECK_JOIN = 2
SPECK_AREA = 3


# --------------------------------------------------------------------------
# key
# --------------------------------------------------------------------------


def sample_key(image: Image.Image, ring: int = 4) -> tuple[int, int, int]:
    """The backdrop color: the median of the sheet's outer `ring` px.

    A sheet's border is backdrop on every generator seen so far, and the median
    shrugs off a unit that grazes the edge.
    """
    a = np.asarray(image.convert("RGB"))
    border = np.concatenate([a[:ring].reshape(-1, 3), a[-ring:].reshape(-1, 3),
                             a[:, :ring].reshape(-1, 3), a[:, -ring:].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(border, axis=0))


def key_sheet(image: Image.Image, key: str | tuple[int, int, int] = "auto",
              tolerance: int = 90, fringe: int = 2, despill: int = 3) -> np.ndarray:
    """Key the backdrop out and despill the edges. Returns RGBA, binary alpha.

    `key` is "auto" (sampled from the border), a name from
    `convert.CHROMA_KEYS`, or an RGB tuple. Despill pulls the key's dominant
    channel down to the others' level within `despill` px of the backdrop: a
    JPEG edge pixel is part art, part green, and without this the green part
    tints every outline cell.
    """
    rgb_key = sample_key(image) if key == "auto" else (
        convert.CHROMA_KEYS[key] if isinstance(key, str) else key)
    keyed = np.array(convert.chroma_key(image, key=rgb_key, tolerance=tolerance,
                                        fringe=fringe))
    if despill > 0:
        op = keyed[..., 3] > 0
        near = convert._dilate(~op, despill) & op
        rgb = keyed[..., :3].astype(np.int32)
        k = np.array(rgb_key)
        if k[1] >= k[0] and k[1] >= k[2]:      # green-like key: cap G
            cap = np.maximum(rgb[..., 0], rgb[..., 2])
            rgb[..., 1] = np.where(near, np.minimum(rgb[..., 1], cap), rgb[..., 1])
        else:                                   # magenta-like key: cap R and B
            for c in (0, 2):
                cap = np.maximum(rgb[..., 1], rgb[..., 2 - c])
                rgb[..., c] = np.where(near, np.minimum(rgb[..., c], cap), rgb[..., c])
        keyed[..., :3] = rgb.astype(np.uint8)
    return keyed


# --------------------------------------------------------------------------
# layout and cutting
# --------------------------------------------------------------------------


MAX_LAYOUT = 8  # rows or columns; sheets are asked for 2x2 to 4x4


def _profile(mask: np.ndarray, axis: int) -> np.ndarray:
    """Content per line along `axis` (0 = rows, 1 = columns), lightly smoothed
    so a one-line notch in a shadow does not pass for a gutter."""
    proj = mask.sum(axis=1 - axis).astype(float)
    return np.convolve(proj, np.ones(3) / 3, mode="same")


def find_cuts(mask: np.ndarray, n: int, axis: int, window: float = 0.3) -> list[int]:
    """n+1 cut positions along `axis` (0 = rows, 1 = columns).

    The content span (first to last non-empty line) is split evenly -- a sheet's
    outer margin is not part of any cell's pitch -- and each inner cut then
    moves to the quietest line within `window` cells of its even position;
    among equally quiet lines, the middle one.
    """
    proj = _profile(mask, axis)
    extent = proj.size
    filled = np.flatnonzero(proj > 0)
    if n <= 1 or filled.size == 0:
        return [0, extent]
    lo_c, hi_c = float(filled[0]), float(filled[-1] + 1)
    cell = (hi_c - lo_c) / n
    cuts = [0]
    for i in range(1, n):
        lo = int(max(cuts[-1] + 1, round(lo_c + cell * (i - window))))
        hi = int(min(extent - 1, round(lo_c + cell * (i + window))))
        seg = proj[lo:hi + 1]
        best = np.flatnonzero(seg <= seg.min() + 1e-9)
        cuts.append(lo + int(best[len(best) // 2]))
    cuts.append(extent)
    return cuts


def guess_layout(mask: np.ndarray, floor: float = 0.12) -> tuple[int, int]:
    """(rows, cols): along each axis, the most cells whose cuts all fall in gutters.

    A cut is in a gutter when its line holds under `floor` of the busiest
    line's content -- not only when it is empty, because in a 4x4 sheet a gun
    barrel or drop shadow crosses most gutters somewhere. Fewer cells always
    pass too (every other cut of a 4-row sheet is a 2-row sheet's cut), so the
    largest passing count wins; more cells than there are rows put cuts through
    units and fail. Every cell must also hold real content, which stops a
    rotor's thin gap from passing for a row of its own.
    """
    def count(axis: int) -> int:
        proj = _profile(mask, axis)
        top = proj.max() if proj.size else 0
        if top <= 0:
            return 1
        best = 1
        for n in range(2, MAX_LAYOUT + 1):
            cuts = find_cuts(mask, n, axis)
            quiet = all(proj[c] <= floor * top for c in cuts[1:-1])
            full = all(proj[a:b].max() >= 0.25 * top for a, b in zip(cuts, cuts[1:]))
            if quiet and full:
                best = n
        return best

    return count(0), count(1)


def label_map(mask: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """8-connected labels of a boolean mask: (labels, area per label).

    Label 0 is background; component k has label k+1. The same run-based
    union-find as `split.label_components`, but painting labels back, because
    isolation has to clear one component's pixels without touching a kept
    component whose box overlaps it.
    """
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs: list[tuple[int, int, int]] = []
    prev: list[int] = []
    for y in range(mask.shape[0]):
        d = np.diff(np.concatenate(([0], mask[y].astype(np.int8), [0])))
        cur = []
        j = 0
        for x0, x1 in zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()):
            idx = len(runs)
            runs.append((y, x0, x1))
            parent.append(idx)
            cur.append(idx)
            while j < len(prev) and runs[prev[j]][2] < x0:
                j += 1
            k = j
            while k < len(prev) and runs[prev[k]][1] <= x1:
                a, b = find(prev[k]), find(idx)
                if a != b:
                    parent[b] = a
                k += 1
        prev = cur

    labels = np.zeros(mask.shape, dtype=np.int32)
    ids: dict[int, int] = {}
    areas: list[int] = []
    for idx, (y, x0, x1) in enumerate(runs):
        r = find(idx)
        if r not in ids:
            ids[r] = len(areas)
            areas.append(0)
        labels[y, x0:x1] = ids[r] + 1
        areas[ids[r]] += x1 - x0
    return labels, areas


def label_regions(key: np.ndarray) -> tuple[np.ndarray, list[int], list[int]]:
    """8-connected same-value regions of an integer image, all values at once.

    `key` < 0 is background. Returns (labels, area per region, value per
    region), numbered exactly as running `label_map(key == v)` over the
    values in ascending order would number them -- by value, then by first
    appearance -- so `merge_specks`' tie-breaks do not depend on which of the
    two did the labelling. One pass instead of one per color: on a grainy AI
    unit with 60 colors that is the difference between 3 s and 0.3 s a sheet.
    """
    h, w = key.shape
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs: list[tuple[int, int, int, int]] = []
    prev: list[int] = []
    for y in range(h):
        row = key[y]
        edge = np.flatnonzero(row[1:] != row[:-1]) + 1
        starts = np.concatenate(([0], edge))
        ends = np.concatenate((edge, [w]))
        vals = row[starts]
        cur = []
        j = 0
        for x0, x1, v in zip(starts.tolist(), ends.tolist(), vals.tolist()):
            if v < 0:
                continue
            idx = len(runs)
            runs.append((y, x0, x1, v))
            parent.append(idx)
            cur.append(idx)
            while j < len(prev) and runs[prev[j]][2] < x0:
                j += 1
            k = j
            while k < len(prev) and runs[prev[k]][1] <= x1:
                if runs[prev[k]][3] == v:
                    a, b = find(prev[k]), find(idx)
                    if a != b:
                        parent[b] = a
                k += 1
        prev = cur

    first: dict[int, int] = {}
    for idx in range(len(runs)):
        first.setdefault(find(idx), idx)
    roots = sorted(first, key=lambda r: (runs[r][3], first[r]))
    ids = {r: i for i, r in enumerate(roots)}
    labels = np.zeros((h, w), dtype=np.int64)
    areas = [0] * len(roots)
    for idx, (y, x0, x1, _) in enumerate(runs):
        i = ids[find(idx)]
        labels[y, x0:x1] = i + 1
        areas[i] += x1 - x0
    return labels, areas, [runs[r][3] for r in roots]


def isolate(rgba: np.ndarray, join: float, min_area: int) -> np.ndarray:
    """Keep the cell's unit: its largest component and parts within `join` px.

    Anything further away is a neighbour's corner, a stray speck or a JPEG
    crumb, and is cleared. Distance is to the unit's bounding box, which grows
    as parts of at least `min_area` px join it -- so a detached rotor extends
    the reach, while a small part (an antenna tip) is kept without extending
    it. Otherwise a trail of crumbs could chain the box out to a neighbour.
    """
    mask = rgba[..., 3] > 0
    labels, areas = label_map(mask)
    if not areas:
        return rgba
    # every component's box in one pass; JPEG crumbs can number in the hundreds
    ys, xs = np.nonzero(labels)
    lab = labels[ys, xs] - 1
    n = len(areas)
    bx0 = np.full(n, 1 << 30); by0 = np.full(n, 1 << 30)
    bx1 = np.zeros(n, dtype=np.int64); by1 = np.zeros(n, dtype=np.int64)
    np.minimum.at(bx0, lab, xs); np.minimum.at(by0, lab, ys)
    np.maximum.at(bx1, lab, xs + 1); np.maximum.at(by1, lab, ys + 1)
    boxes = list(zip(bx0.tolist(), by0.tolist(), bx1.tolist(), by1.tolist()))
    order = sorted(range(len(areas)), key=lambda k: -areas[k])
    keep = {order[0]}
    box = list(boxes[order[0]])
    grew = True
    while grew:
        grew = False
        for k in order[1:]:
            if k not in keep and split._gap(box, boxes[k]) <= join:
                keep.add(k)
                if areas[k] >= min_area:
                    b = boxes[k]
                    box = [min(box[0], b[0]), min(box[1], b[1]),
                           max(box[2], b[2]), max(box[3], b[3])]
                    grew = True
    out = rgba.copy()
    kept = np.isin(labels, [k + 1 for k in keep])
    out[~kept] = 0
    return out


# --------------------------------------------------------------------------
# grain
# --------------------------------------------------------------------------


def _energy(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel edge energy across x and across y (color plus alpha)."""
    rgb = rgba[..., :3].astype(np.float64)
    alpha = (rgba[..., 3] > 0).astype(np.float64)[..., None] * 255.0
    chan = np.concatenate([rgb, alpha], axis=2)
    ex = np.abs(np.diff(chan, axis=1)).sum(axis=2)
    ey = np.abs(np.diff(chan, axis=0)).sum(axis=2)
    return ex, ey


def _acf(lines: np.ndarray, max_lag: int) -> np.ndarray:
    """Summed autocorrelation of each row of `lines`, lags 0..max_lag-1."""
    if lines.size == 0 or lines.shape[1] < 2:
        return np.zeros(max_lag)
    e = lines - lines.mean(axis=1, keepdims=True)
    n = e.shape[1]
    f = np.fft.rfft(e, 2 * n)
    r = np.fft.irfft(f * np.conj(f))[:, :max_lag].sum(axis=0)
    out = np.zeros(max_lag)
    out[:r.size] = r
    return out


def grain_signal(units: list[np.ndarray], max_lag: int = 48) -> np.ndarray:
    """Normalized edge autocorrelation pooled over every unit and both axes."""
    total = np.zeros(max_lag)
    for u in units:
        ex, ey = _energy(u)
        total += _acf(ex, max_lag) + _acf(ey.T, max_lag)
    return total / total[0] if total[0] > 0 else total


def measure_grain(units: list[np.ndarray], lo: float = GRAIN_RANGE[0],
                  hi: float = GRAIN_RANGE[1]) -> tuple[float, float]:
    """The sheet's grain, in source px per logical px.

    A grain p puts autocorrelation peaks at p, 2p, 3p ...; each candidate is
    scored by the signal at its multiples (out to 36 px) minus the mean of the
    two half-points either side, (k - 1/2)p and (k + 1/2)p. The signal falls
    steeply with lag, so a one-sided comparison is dominated by the slope and
    goes negative for every candidate; the symmetric one cancels it. The first
    multiple is skipped, as the curve is too steep there for the cancellation
    to hold. The half-points also reject p/2: they land on the real peaks. A
    multiple 2p scores close to p, so the finest pitch within 90% of the best
    wins.

    Returns (grain, strength). Strength is the winning score, in units of the
    normalized signal; a sheet with no grain at all scores near zero, and
    `fix_sheet` then treats the grain as unknown.
    """
    sig = grain_signal(units)
    lags = np.arange(sig.size, dtype=float)
    cands = np.arange(lo, hi + 1e-9, 0.02)
    scores = np.full(cands.size, -np.inf)
    for i, p in enumerate(cands):
        k = np.arange(2, int(36 / p) + 1)
        if k.size < 2:
            continue
        on = np.interp(k * p, lags, sig)
        off = (np.interp((k - 0.5) * p, lags, sig) + np.interp((k + 0.5) * p, lags, sig)) / 2
        scores[i] = (on - off).mean()
    best = scores.max()
    if not np.isfinite(best) or best <= 0:
        return float(lo), 0.0
    i = int(np.flatnonzero(scores >= 0.9 * best)[0])
    return float(cands[i]), float(scores[i])


# --------------------------------------------------------------------------
# snap
# --------------------------------------------------------------------------


def _best_offset(energy: np.ndarray, pitch: float) -> float:
    """The offset in [0, pitch) whose boundaries collect the most edge energy."""
    if energy.size < 2:
        return 0.0
    n = energy.size
    lags = np.arange(n, dtype=float)
    best, best_o = -1.0, 0.0
    for o in np.arange(0.0, pitch, max(0.1, pitch / 30.0)):
        pos = np.arange(o, n, pitch)
        # a boundary at coordinate c has its evidence at index c-1
        s = np.interp(pos - 1.0, lags, energy).sum() / max(1, pos.size)
        if s > best:
            best, best_o = s, o
    return float(best_o)


def snap_unit(rgba: np.ndarray, pitch: float) -> tuple[Image.Image, convert.Grid] | None:
    """Reduce one isolated unit at a fixed `pitch`, fitting its own offset."""
    op = rgba[..., 3] > 0
    if not op.any():
        return None
    ys, xs = np.nonzero(op)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = rgba[y0:y1, x0:x1]
    ex, ey = _energy(crop)
    ox = _best_offset(ex.sum(axis=0), pitch)
    oy = _best_offset(ey.sum(axis=1), pitch)
    ox, cols = convert._frame_axis(ox, pitch, crop.shape[1])
    oy, rows = convert._frame_axis(oy, pitch, crop.shape[0])
    grid = convert.Grid(pitch, pitch, ox, oy, cols, rows, 1.0)
    small, _ = convert.pixel_snap(Image.fromarray(crop, "RGBA"), grid)
    # A crumb that survived isolation at source scale is a lone pixel or two
    # now, off on its own; the same rule at 1x clears it.
    small = Image.fromarray(isolate(np.array(small), join=SPECK_JOIN,
                                    min_area=SPECK_AREA), "RGBA")
    bb = small.getbbox()
    return (small.crop(bb) if bb else small), grid


# --------------------------------------------------------------------------
# color: quantize and even out
# --------------------------------------------------------------------------

# Matching happens in YCbCr with luma weighted LUMA_WEIGHT times over chroma.
# JPEG keeps luma at full resolution and chroma at half, so on a small feature
# the chroma is the less trustworthy half of a color. Measured on the synthetic
# JPEG sheet, by pixels that render identically in all 32 teams (pooled over
# the sheet): plain nearest-RGB 83.6%; YCbCr luma x3 90%; with the usage prior
# below, 94%. Per unit, averaged, as test_aifix reports it: 83% -> 93%.
LUMA_WEIGHT = 3.0

# The usage prior: each sheet uses a few of the 63 colors, and a color the
# sheet barely uses should lose a near tie to one it uses a lot. That is what
# keeps a JPEG-shifted (248,144,88) from landing on (248,152,96) -- a near
# twin that sits in the tertiary quadrant and so does not follow the team
# color. PRIOR_SIGMA is the assumed color noise, in weighted YCbCr units: 6
# keeps the sheet mean at 94% while the worst unit (a small infantry whose skin
# tones are rare on the sheet) holds 80%; at 8 that unit falls to 71%, at 12 to
# 29% -- a looser sigma lets common colors swallow rare ones.
PRIOR_SIGMA = 6.0
PRIOR_ROUNDS = 10
# Within this weighted-YCbCr distance a pixel counts as exactly a palette
# color and the prior leaves it alone (only rounding separates them).
EXACT_RADIUS = 1.5

_YCC = np.array([[0.299, 0.587, 0.114],
                 [-0.168736, -0.331264, 0.5],
                 [0.5, -0.418688, -0.081312]])
_YCC_INV = np.linalg.inv(_YCC)


def _ycc(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float64) @ _YCC.T


def _rgb(ycc: np.ndarray) -> np.ndarray:
    return np.clip(ycc @ _YCC_INV.T, 0, 255)


def team_cells(pal: convert.ClassPalette) -> np.ndarray:
    """Which palette colors sit in the primary quadrant -- the ones that follow
    the team color when CoW recolors (see docs/cow-palette-format.md)."""
    return (pal.coords[:, 0] >= 129) & (pal.coords[:, 1] >= 129)


def quantize_pixels(rgb: np.ndarray, pal: convert.ClassPalette,
                    team_lock: bool = True) -> np.ndarray:
    """Palette index for every row of `rgb` (n, 3), pooled as one sheet.

    Weighted-YCbCr nearest color, then a few rounds of re-assignment under a
    prior proportional to how much the sheet uses each color (a small EM: the
    cost is distance / 2 sigma^2 - log share). Pass every unit on a sheet in
    one call so the prior pools their evidence.

    `team_lock` keeps body-colored pixels (`_body_mask`) on palette colors
    that follow the team. Without it, AI orange lands on near-twins in the
    tertiary quadrant -- (248,152,96) beside the team's (248,144,88) -- and
    stays orange on a Blue Moon unit: 14.6% of the body on Nanobanana (3),
    against 3.6% in CoW's own sprites (gold trim, wood). With it, 0.1%. The
    cost on the synthetic sheet is 92.8% -> 92.0%: real gold and wood accents
    turn team-colored too. Exact palette pixels are never moved, so clean
    hand-made art keeps its accents either way.
    """
    if rgb.size == 0:
        return np.zeros(0, dtype=np.int64)
    w = np.array([LUMA_WEIGHT, 1.0, 1.0])
    yc = _ycc(rgb)
    a = yc * w
    b = _ycc(pal.colors) * w
    raw = ((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
    d = raw / (2 * PRIOR_SIGMA ** 2)
    if team_lock:
        d[np.ix_(_body_mask(yc), ~team_cells(pal))] = np.inf
    nearest = raw.argmin(axis=1)
    # A pixel that already *is* a palette color is evidence, not noise: the
    # prior must not trade it for a busier twin. Without this lock a lossless
    # sheet of exact palette art came back 97.4% right instead of 100%.
    exact = raw.min(axis=1) <= EXACT_RADIUS ** 2
    pick = d.argmin(axis=1)
    pick[exact] = nearest[exact]
    for _ in range(PRIOR_ROUNDS):
        share = np.bincount(pick, minlength=len(pal)) + 0.5
        new = (d - np.log(share / share.sum())[None, :]).argmin(axis=1)
        new[exact] = nearest[exact]
        if np.array_equal(new, pick):
            break
        pick = new
    return pick


def _body_mask(ycc: np.ndarray) -> np.ndarray:
    """Team-colored body: warm, saturated, neither outline-dark nor highlight-white.

    Warm is a hue angle in the CbCr plane between 60 and 175 degrees, which
    spans Orange Star's whole red-orange-peach range and excludes the greys,
    blues and greens that never recolor.
    """
    hue = np.degrees(np.arctan2(ycc[:, 2], ycc[:, 1]))
    chroma = np.hypot(ycc[:, 1], ycc[:, 2])
    return (chroma > 30) & (ycc[:, 0] > 40) & (ycc[:, 0] < 235) & (hue > 60) & (hue < 175)


def _body_target(pal: convert.ClassPalette) -> tuple[float, float, float]:
    """(luma mean, luma spread, median chroma) of the palette's body colors,
    weighted by how much the real AW2 sprites use each one."""
    y = _ycc(pal.colors)
    m = _body_mask(y)
    w = pal.shares[m] / pal.shares[m].sum()
    lum = y[m, 0]
    mean = float((lum * w).sum())
    spread = float(np.sqrt(((lum - mean) ** 2 * w).sum()))
    chroma = np.hypot(y[m, 1], y[m, 2])
    order = np.argsort(chroma)
    med = float(np.interp(0.5, np.cumsum(w[order]) - w[order] / 2, chroma[order]))
    return mean, spread, med


def even_out(rgb: np.ndarray, pal: convert.ClassPalette, strength: float) -> np.ndarray:
    """Pull one unit's body colors toward Orange Star's usual tones.

    The model drifts per unit -- one SUV red, the next peach -- and a peach
    body lands on the light end of the team ramp, so in CoW that unit renders
    washed out in every army. This moves the unit's body luma to the palette's
    mean and spread, and scales its chroma to the palette's median, by
    `strength` (0 = off, 1 = fully).

    It is a trade, not a fix: on art that was already right it erases the
    shading differences between units. Measured on the synthetic sheet of real
    CoW sprites it costs 93% -> 74% at full strength (pooled: 94% -> 79%, and
    -> 90% at half), so it is off by default and meant for units that visibly
    drifted. Full quantile matching evened units out no better and cost more.
    """
    if strength <= 0 or rgb.size == 0:
        return rgb
    y = _ycc(rgb)
    m = _body_mask(y)
    if m.sum() < 20:
        return rgb
    t_mean, t_spread, t_chroma = _body_target(pal)
    body = y[m]
    lum = body[:, 0]
    target = (lum - lum.mean()) / (lum.std() + 1e-6) * t_spread + t_mean
    body[:, 0] = lum + strength * (target - lum)
    k = t_chroma / max(float(np.median(np.hypot(body[:, 1], body[:, 2]))), 1.0)
    body[:, 1:] *= 1.0 + strength * (k - 1.0)
    out = rgb.astype(np.float64).copy()
    out[m] = _rgb(body)
    return out


# --------------------------------------------------------------------------
# cleanup
# --------------------------------------------------------------------------

# Speck merging: a same-color region of at most SPECK_MERGE_AREA px takes the
# color of its most similar neighbouring region, if that neighbour is within
# `smooth` in weighted YCbCr (the quantizer's own metric).
#
# What the AI output actually looks like next to CoW's own battle sprites:
#   mean same-color region   11.1 px (CoW)  vs 2.5-3.0 (AI, after quantizing)
#   pixels in regions <= 3   8.8%           vs 39-49%
#   colors per unit          17             vs 31-36
# Outlines and shadows, by contrast, already match: silhouette edges are dark
# at the same rate (66-72% vs 70%), and CoW draws the same black slab shadow
# under vehicles, so neither is touched.
#
# DEFAULT_SMOOTH = 40 is the point where merging is a pure gain: on the
# synthetic JPEG sheet it lifts render-identical pixels from 91.9% to 93.5%
# (worst unit 74% -> 81%) while changing 0.19% of CoW's own sprites -- their
# rivets and highlights contrast too strongly to merge. Stronger settings
# flatten the AI grain only a little further (tiny-region share 36% -> 28% at
# 90) and start eating real detail (4.8% of CoW art changed). Neither a
# per-unit color budget nor a median filter before snapping did better: the
# remaining grain is shading the model drew, not noise.
DEFAULT_SMOOTH = 40.0
SPECK_MERGE_AREA = 3
SPECK_MERGE_ROUNDS = 4


def merge_specks(rgba: np.ndarray, smooth: float = DEFAULT_SMOOTH,
                 max_area: int = SPECK_MERGE_AREA) -> np.ndarray:
    """Fold tiny same-color regions into their most similar neighbour.

    Works on a quantized unit, and only ever copies a neighbour's color, so
    the result stays on the palette (and inside the team lock's choices for
    any pixel the neighbour's color was allowed for).
    """
    if smooth <= 0:
        return rgba
    a = rgba.copy()
    op = a[..., 3] > 0
    if not op.any():
        return a
    w = np.array([LUMA_WEIGHT, 1.0, 1.0])
    for _ in range(SPECK_MERGE_ROUNDS):
        key = (a[..., 0].astype(np.int64) << 16) | (a[..., 1].astype(np.int64) << 8) | a[..., 2]
        key[~op] = -1
        reg, areas, vals = label_regions(key)
        colors = [((v >> 16) & 255, (v >> 8) & 255, v & 255) for v in vals]
        colors_a = np.array(colors, dtype=np.float64)
        areas_a = np.array(areas)
        yc = _ycc(colors_a) * w

        # 4-neighbour borders between different regions, both directions
        pairs = []
        for p, q in ((reg[:, :-1], reg[:, 1:]), (reg[:-1, :], reg[1:, :])):
            m = (p > 0) & (q > 0) & (p != q)
            pairs.append(np.stack([p[m], q[m]], axis=1))
        pq = np.concatenate(pairs)
        if pq.size == 0:
            break
        pq = np.concatenate([pq, pq[:, ::-1]])
        uniq, counts = np.unique(pq, axis=0, return_counts=True)
        src, dst = uniq[:, 0], uniq[:, 1]

        tiny = areas_a[src - 1] <= max_area
        src, dst, counts = src[tiny], dst[tiny], counts[tiny]
        if src.size == 0:
            break
        dist = np.sqrt(((yc[src - 1] - yc[dst - 1]) ** 2).sum(axis=1))
        # best neighbour per tiny region: nearest color, then longest border,
        # then the bigger region
        order = np.lexsort((-areas_a[dst - 1], -counts, dist, src))
        first = np.ones(order.size, dtype=bool)
        first[1:] = src[order][1:] != src[order][:-1]
        best = order[first]
        best = best[dist[best] <= smooth]
        if best.size == 0:
            break
        remap = np.zeros(len(areas) + 1, dtype=np.int64)
        remap[src[best]] = dst[best]
        hit = remap[reg] > 0
        a[hit, :3] = colors_a[remap[reg[hit]] - 1].astype(np.uint8)
    return a


# --------------------------------------------------------------------------
# the whole sheet
# --------------------------------------------------------------------------


# Strength below which a measured grain is treated as noise. Real pixel art
# scores ~0.33; Nano Banana sheets with a visible ~3 px grain score 0.002-0.012;
# a sheet with no grain at all (Nanobanana Attempts (1)) scores 0.0008.
GRAIN_MIN_STRENGTH = 0.0015

# The default size is the sheet's *native* one: snapped at its own grain, one
# output pixel per pixel the model drew. Nano Banana sheets measure a grain of
# 2.84 px (cars.jpeg 3.4), which puts their median unit at 79-90 px wide (62
# for cars.jpeg) -- about twice CoW's own battle sprites, whose ground units are
# 39-75 px wide, median 47 (COW_BATTLE_WIDTH, the GUI's reference mark). Going
# finer than the grain invents pixels, so native is also the largest size; the
# size slider only goes down from it.
COW_BATTLE_WIDTH = 48

# A sheet with no measurable grain (Nanobanana Attempts (1)) has no native size
# to read off, so its median unit is put at this width -- what the gridded
# Nano Banana sheets come out at natively.
NATIVE_FALLBACK_WIDTH = 96

# The size slider's lower end, in px of median width.
MIN_WIDTH = 16


@dataclasses.dataclass
class Unit:
    """One cell of the sheet, through the chain."""

    index: int
    name: str
    box: tuple[int, int, int, int]        # cell in sheet px, half-open
    isolated: np.ndarray                  # keyed, neighbours cleared, cell-sized
    result: convert.ConversionResult | None = None
    # (pitch, snapped RGBA, grid) of the last snap, so a change that only
    # touches color (even out, team lock, smooth) skips the slow modal vote
    snap: tuple[float, np.ndarray, convert.Grid] | None = None

    @property
    def width(self) -> int:
        """The isolated unit's width in source px (0 for an empty cell)."""
        xs = np.flatnonzero((self.isolated[..., 3] > 0).any(axis=0))
        return int(xs[-1] - xs[0] + 1) if xs.size else 0

    @property
    def size(self) -> tuple[int, int] | None:
        r = self.result
        return r.snapped.size if r and r.snapped is not None else None


@dataclasses.dataclass
class Sheet:
    """A keyed, cut and measured sheet: everything that does not depend on pitch."""

    name: str
    key: tuple[int, int, int]
    layout: tuple[int, int]               # rows, cols
    cuts_x: list[int]
    cuts_y: list[int]
    grain: float | None                   # None when too weak to trust
    grain_strength: float
    units: list[Unit]
    pitch: float | None = None            # set by snap_sheet

    @property
    def min_pitch(self) -> float:
        """The finest pitch worth snapping at: the grain, or the search floor."""
        return self.grain or GRAIN_RANGE[0]

    @property
    def median_source_width(self) -> float:
        """The median unit's width in sheet px (0 for an empty sheet)."""
        widths = [u.width for u in self.units if u.width]
        return float(np.median(widths)) if widths else 0.0

    def pitch_for_width(self, width: int) -> float:
        """The pitch that makes the sheet's *median* unit `width` px wide.

        The median, not the widest, because a sheet's outlier is usually a unit
        the model drew too big rather than the size the rest should follow.
        Never finer than `min_pitch`: below the grain the snap invents pixels.
        """
        med = self.median_source_width
        if not med:
            return self.min_pitch
        return max(self.min_pitch, med / max(1, width))

    def width_at(self, pitch: float) -> int:
        """The median unit's output width at `pitch` (approximate: each unit's
        own offset can add or lose a pixel)."""
        return max(1, round(self.median_source_width / pitch)) if pitch else 0

    @property
    def native_pitch(self) -> float:
        """The default pitch: the grain, or for a sheet without one, the pitch
        that puts the median unit at NATIVE_FALLBACK_WIDTH."""
        return self.grain or self.pitch_for_width(NATIVE_FALLBACK_WIDTH)

    @property
    def native_width(self) -> int:
        """The median unit's width at `native_pitch` -- the default size."""
        return self.width_at(self.native_pitch)

    @property
    def max_width(self) -> int:
        """The largest median width worth asking for: the one at `min_pitch`."""
        return self.width_at(self.min_pitch)


def prepare_sheet(image: Image.Image, *, name: str = "sheet",
                  layout: tuple[int, int] | None = None,
                  key: str | tuple[int, int, int] = "auto",
                  tolerance: int = 90) -> Sheet:
    """Key, cut, isolate and measure. The slow half; independent of pitch.

    `layout` is (rows, cols), guessed from the gutters when omitted.
    """
    rgb_key = sample_key(image) if key == "auto" else (
        convert.CHROMA_KEYS[key] if isinstance(key, str) else key)
    rgba = key_sheet(image, rgb_key, tolerance)
    mask = rgba[..., 3] > 0
    rows, cols = layout or guess_layout(mask)
    cuts_y = find_cuts(mask, rows, axis=0)
    cuts_x = find_cuts(mask, cols, axis=1)

    cells = []
    for r in range(rows):
        for c in range(cols):
            box = (cuts_x[c], cuts_y[r], cuts_x[c + 1], cuts_y[r + 1])
            cells.append((box, rgba[box[1]:box[3], box[0]:box[2]]))

    # Isolation's join distance scales with the grain, which is measured on
    # isolated units. A first pass at a fixed join is enough to measure it: the
    # grain is not sensitive to a stray speck.
    rough = [isolate(sub, join=12, min_area=40) for _, sub in cells]
    grain, strength = measure_grain(rough)
    known = grain if strength >= GRAIN_MIN_STRENGTH else None
    g = known or 3.0
    units = [Unit(i, f"{name}_{i:02d}", box,
                  isolate(sub, join=4 * g, min_area=int(4 * g * g)))
             for i, (box, sub) in enumerate(cells, 1)]
    return Sheet(name, rgb_key, (rows, cols), cuts_x, cuts_y, known, strength, units)


def snap_sheet(sheet: Sheet, pitch: float,
               unit_class: str | convert.ClassPalette = convert.ALL_CLASSES,
               even: float | dict[int, float] = 0.0, team_lock: bool = True,
               smooth: float = DEFAULT_SMOOTH) -> Sheet:
    """Snap every unit at one `pitch`, then quantize, clean, encode and validate.

    `even` is the `even_out` strength: one value for every unit, or
    {unit index: strength} with missing units at 0. Quantizing is pooled over
    the whole sheet; `team_lock` is explained at `quantize_pixels`, `smooth`
    at `merge_specks` (0 turns it off).
    """
    pal = convert._resolve_class(unit_class)
    sheet.pitch = float(pitch)
    snaps: list[tuple[Unit, np.ndarray, convert.Grid]] = []
    for u in sheet.units:
        u.result = None
        if u.snap is None or u.snap[0] != sheet.pitch:
            snapped = snap_unit(u.isolated, sheet.pitch)
            u.snap = None if snapped is None else (
                sheet.pitch, np.array(snapped[0]), snapped[1])
        if u.snap is not None:
            snaps.append((u, u.snap[1], u.snap[2]))

    pixels = []
    for u, a, _ in snaps:
        k = even.get(u.index, 0.0) if isinstance(even, dict) else even
        pixels.append(even_out(a[a[..., 3] > 0][:, :3], pal, k))
    picks = quantize_pixels(np.concatenate(pixels) if pixels else np.zeros((0, 3)), pal,
                            team_lock)

    at = 0
    for (u, a, grid), px in zip(snaps, pixels):
        q = np.zeros_like(a)
        op = a[..., 3] > 0
        q[op, :3] = pal.colors[picks[at:at + len(px)]]
        q[op, 3] = 255
        q = merge_specks(q, smooth)
        at += len(px)
        res = convert.ConversionResult(name=u.name, unit_class=pal.name, grid=grid,
                                       keyed=Image.fromarray(u.isolated, "RGBA"),
                                       snapped=Image.fromarray(a, "RGBA"),
                                       quantized=Image.fromarray(q, "RGBA"))
        res.encoded = convert.encode(res.quantized, pal)
        res.validation = convert.validate(res.encoded)
        if not res.validation["ok"]:
            res.needs_review = True
            res.reason = (f"{res.validation['undefined_worst']} pixel(s) undefined "
                          f"in at least one team table")
        u.result = res
    return sheet


def fix_sheet(image: Image.Image, *, name: str = "sheet",
              layout: tuple[int, int] | None = None, pitch: float | None = None,
              width: int | None = None, key: str | tuple[int, int, int] = "auto",
              tolerance: int = 90,
              unit_class: str | convert.ClassPalette = convert.ALL_CLASSES,
              even: float | dict[int, float] = 0.0, team_lock: bool = True,
              smooth: float = DEFAULT_SMOOTH) -> Sheet:
    """Both halves. `pitch` wins over `width`; with neither, the sheet's
    native size (`Sheet.native_pitch`)."""
    sheet = prepare_sheet(image, name=name, layout=layout, key=key, tolerance=tolerance)
    if pitch is None:
        pitch = sheet.pitch_for_width(width) if width else sheet.native_pitch
    return snap_sheet(sheet, pitch, unit_class, even, team_lock, smooth)


def upscale(image: Image.Image, scale: int) -> Image.Image:
    """Integer nearest-neighbour upscale: every pixel becomes a scale x scale block."""
    if scale <= 1:
        return image
    return image.resize((image.width * scale, image.height * scale), Image.NEAREST)


def write_outputs(sheet: Sheet, out: pathlib.Path, *, keep: set[int] | None = None,
                  teams: list[str] | tuple[str, ...] = (), scale: int = 1,
                  palettes: cowpalette.PaletteSet | None = None) -> int:
    """Write the sheet's units to `out`. Returns how many were written.

    Every unit gets `name+mask.png`, the index-encoded form CoW reads (always
    1x: CoW draws battle sprites at their own size), and `rgb/name.png`, the
    Orange Star art for hand-fixing. For each army in `teams` it also gets
    `name+army.png` -- the suite's export naming -- rendered through CoW's
    own table, in `armies/`, or `armies Nx/` when `scale` > 1 blows them up
    by nearest neighbour for use outside CoW.

    `keep` is the set of unit indices to write; None writes every unit that
    converted. A rejected or empty unit is skipped, and whatever an earlier
    export wrote for it is moved into `rejected/` -- out of the previewer's
    sight (it lists only the folder's top level) without deleting anything.
    """
    out.mkdir(parents=True, exist_ok=True)
    rgb_dir = out / "rgb"
    rgb_dir.mkdir(exist_ok=True)
    army_dir = out / ("armies" if scale <= 1 else f"armies {scale}x")
    if teams:
        army_dir.mkdir(exist_ok=True)
        palettes = palettes or cowpalette.load_palettes()
    n = 0
    for u in sheet.units:
        r = u.result
        if r is None or r.encoded is None or (keep is not None and u.index not in keep):
            _set_aside(out, u.name)
            continue
        r.quantized.save(rgb_dir / f"{u.name}.png")
        # `+mask` is CoW's own suffix for the index-encoded form.
        r.encoded.save(out / f"{u.name}+mask.png")
        for t in teams:
            army = cowpalette.recolor(r.encoded, t, palettes)
            upscale(army, scale).save(army_dir / cowpalette.export_name(u.name, t))
        n += 1
    return n


def _set_aside(out: pathlib.Path, name: str) -> None:
    """Move an earlier export of unit `name` into out/rejected/, same layout."""
    found = [out / f"{name}+mask.png", out / "rgb" / f"{name}.png"]
    for d in out.glob("armies*"):
        found += d.glob(f"{glob.escape(name)}+*.png")
    for f in found:
        if f.is_file():
            dest = out / "rejected" / f.relative_to(out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, dest)


def _layout_arg(s: str) -> tuple[int, int]:
    try:
        rows, cols = (int(v) for v in s.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s!r} is not ROWSxCOLS, e.g. 4x4")
    if not (1 <= rows <= MAX_LAYOUT and 1 <= cols <= MAX_LAYOUT):
        raise argparse.ArgumentTypeError(f"layout {s} is outside 1x1..{MAX_LAYOUT}x{MAX_LAYOUT}")
    return rows, cols


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cut an AI-generated sheet into rough CoW sprites.",
        epilog="python tools/aifix.py SHEET.jpg OUT [--width 48] [--layout 4x4] "
               "[--armies blue_moon,green_earth] [--scale 4]")
    ap.add_argument("sheet", type=pathlib.Path, help="the AI sheet")
    ap.add_argument("output", type=pathlib.Path, help="folder to write sprites to")
    ap.add_argument("--layout", type=_layout_arg,
                    help="ROWSxCOLS of the sheet (default: guessed from the gutters)")
    ap.add_argument("--width", type=int,
                    help="width of the sheet's median unit in px (default: the sheet's "
                         "native size, one pixel per pixel the model drew; CoW's own "
                         f"battle sprites are ~{COW_BATTLE_WIDTH})")
    ap.add_argument("--pitch", type=float,
                    help="snap at exactly this many source px per pixel (overrides --width)")
    ap.add_argument("--key", default="auto", choices=["auto", *sorted(convert.CHROMA_KEYS)],
                    help="backdrop color (default: sampled from the sheet's border)")
    ap.add_argument("--even", type=float, default=0.0, metavar="0..1",
                    help="even out every unit's body colors toward Orange Star's "
                         "usual tones, at this strength (default: 0, off)")
    ap.add_argument("--smooth", type=float, default=DEFAULT_SMOOTH, metavar="0..90",
                    help="fold specks of up to 3 px into a neighbour this similar "
                         f"(default: {DEFAULT_SMOOTH:g}; 0 = off; higher flattens more "
                         "but starts eating real detail)")
    ap.add_argument("--no-team-lock", dest="team_lock", action="store_false",
                    help="let body colors use palette cells that do not follow the "
                         "team color (keeps gold/wood accents, risks orange patches)")
    ap.add_argument("--armies", default="", metavar="A,B|all",
                    help="also write each unit recolored into these armies, as "
                         "name+army.png in armies/ (default: none)")
    ap.add_argument("--scale", type=int, default=1, metavar="N",
                    help="blow the army PNGs up N times, nearest neighbour, for use "
                         "outside CoW (default: 1; the +mask.png stays 1x)")
    args = ap.parse_args(argv)
    if not args.sheet.is_file():
        ap.error(f"{args.sheet} is not a file")
    if not 0.0 <= args.even <= 1.0:
        ap.error("--even must be between 0 and 1")
    if not 1 <= args.scale <= 16:
        ap.error("--scale must be between 1 and 16")
    palettes = cowpalette.load_palettes()
    known = palettes.team_names()
    armies = known if args.armies == "all" else [a for a in args.armies.split(",") if a]
    unknown = [a for a in armies if a not in known]
    if unknown:
        ap.error(f"unknown army {unknown[0]!r}; choose from {', '.join(known)}")

    with Image.open(args.sheet) as im:
        sheet = fix_sheet(im.convert("RGB"), name=args.sheet.stem, layout=args.layout,
                          pitch=args.pitch, width=args.width, key=args.key,
                          even=args.even, team_lock=args.team_lock, smooth=args.smooth)
    n = write_outputs(sheet, args.output, teams=armies, scale=args.scale,
                      palettes=palettes)
    grain = f"{sheet.grain:.2f}" if sheet.grain else "none found"
    print(f"{sheet.layout[0]}x{sheet.layout[1]} sheet, grain {grain}, pitch {sheet.pitch:.2f}")
    for u in sheet.units:
        r = u.result
        tag = "ok" if r and r.ok else "??"
        size = "%dx%d" % u.size if u.size else "empty"
        print(f"  {tag} {u.name:<28} {size:>7}" + (f"  {r.reason}" if r and r.reason else ""))
    print(f"\n{n} of {len(sheet.units)} written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
