"""Synthetic generator-output, so Phase 5 is testable with no AI art in hand.

The pipeline's job is to undo what an image generator did to a pixel sprite.
Nothing here pretends to be a generator -- it reproduces the specific damage the
pixel-snap step has to survive, and it does so from *real* CoW sprites, so a
successful round trip is a statement about real data:

    real sprite -> decode to Orange Star RGB -> upscale -> damage
                -> [the pipeline] -> encoded sprite
                -> must equal the original, pixel for pixel

The damage model, each piece chosen because it breaks a naive approach:

* **non-integer pitch** -- 1024/17 = 60.24. Breaks "divide by 6".
* **fractional offset and a margin** -- the grid does not start at (0, 0).
  Breaks any fixed-stride sampling.
* **per-cell gradient** -- generators shade inside a "flat" block. Breaks
  mean-pooling, and makes a point sample depend on where in the cell it lands.
* **gaussian noise** -- breaks raw-RGB modal voting, which is why the real
  reducer votes on bucketed color.
* **edge bleed** -- a blur across boundaries. This is the one that punishes a
  naive box filter hardest, and it is why cells are voted on with an inset.
* **imperfect chroma key** -- the backdrop is not exactly #00ff00, and there is
  a halo of desaturated key around the silhouette.

`damage_level` scales all of it at once so tests can assert both "clean input
recovers exactly" and "realistically dirty input still recovers".
"""

from __future__ import annotations

import numpy as np
from PIL import Image

import cowpalette
import convert


def decode_to_authored(sprite: Image.Image, team: str = "orange_star") -> Image.Image:
    """Render an index-encoded CoW sprite into the RGB an artist would draw.

    This is the inverse of the pipeline's final step, and it is how a real
    shipped sprite becomes a stand-in for generator input: `recolor` to Orange
    Star gives exactly the colors `pipeline/classes.json` was built from.
    """
    return cowpalette.recolor(sprite, team)


def synthesize(small: Image.Image, *, target: int = 1024,
               damage: float = 1.0, seed: int = 0,
               key: str = "green", margin: int = 0) -> tuple[Image.Image, float, float]:
    """Blow `small` up to roughly `target` px and rough it up.

    Returns (image, true_pitch, true_offset) so a test can check the detected
    grid against ground truth rather than only checking the final pixels.

    `damage` of 0 gives a clean non-integer-pitch upscale on a perfect key;
    1.0 is the full model above. Values above 1 overdrive it, which is useful
    for finding where detection actually breaks rather than asserting it never
    does.
    """
    rng = np.random.default_rng(seed)

    a = np.array(small.convert("RGBA"))
    h, w = a.shape[:2]

    pitch = target / max(w, h)
    # a deliberately ugly non-integer pitch: no generator lands on a round one
    pitch = float(pitch) + 0.237

    offset = (pitch * 0.41) if damage > 0 else 0.0
    off_x = offset + margin
    off_y = offset * 0.63 + margin

    out_w = int(np.ceil(off_x + pitch * w)) + margin
    out_h = int(np.ceil(off_y + pitch * h)) + margin

    key_rgb = np.array(convert.CHROMA_KEYS[key], dtype=np.float64)
    canvas = np.tile(key_rgb, (out_h, out_w, 1))
    alpha = np.zeros((out_h, out_w), dtype=np.float64)

    # --- paint each logical pixel as a block at its fractional position ---
    yy, xx = np.mgrid[0:out_h, 0:out_w]
    for j in range(h):
        ya, yb = off_y + pitch * j, off_y + pitch * (j + 1)
        rows = (yy[:, 0] >= ya) & (yy[:, 0] < yb)
        if not rows.any():
            continue
        for i in range(w):
            if a[j, i, 3] == 0:
                continue
            xa, xb = off_x + pitch * i, off_x + pitch * (i + 1)
            cols = (xx[0] >= xa) & (xx[0] < xb)
            if not cols.any():
                continue
            sel = np.ix_(np.nonzero(rows)[0], np.nonzero(cols)[0])
            canvas[sel] = a[j, i, :3].astype(np.float64)
            alpha[sel] = 255.0

    if damage > 0:
        canvas = _add_cell_gradient(canvas, alpha, off_x, off_y, pitch, w, h,
                                    amount=10.0 * damage)
        canvas, alpha = _bleed(canvas, alpha, radius=max(1, int(round(pitch * 0.06 * damage))))
        canvas += rng.normal(0.0, 4.0 * damage, canvas.shape)
        # the key itself is not exactly the key
        far = alpha < 128
        canvas[far] += rng.normal(0.0, 6.0 * damage, canvas[far].shape)

    rgb = np.clip(canvas, 0, 255).astype(np.uint8)
    # a generator emits no alpha: everything is flattened onto the key
    out = np.dstack([rgb, np.full((out_h, out_w), 255, dtype=np.uint8)])
    return Image.fromarray(out, "RGBA"), pitch, off_x


def _add_cell_gradient(canvas: np.ndarray, alpha: np.ndarray, ox: float,
                       oy: float, pitch: float, w: int, h: int,
                       amount: float) -> np.ndarray:
    """Shade each logical cell across its own width -- generators love doing this.

    A cell whose true color is C becomes a ramp centred on C. Mean-pooling
    recovers C and so does a modal vote; a *point sample* does not, which is
    exactly the distinction the pipeline is built around.
    """
    out = canvas.copy()
    yy, xx = np.mgrid[0:canvas.shape[0], 0:canvas.shape[1]]
    fx = ((xx - ox) / pitch) % 1.0
    fy = ((yy - oy) / pitch) % 1.0
    ramp = ((fx - 0.5) + (fy - 0.5)) * amount
    lit = alpha >= 128
    out[lit] += ramp[lit][:, None]
    return out


def _bleed(canvas: np.ndarray, alpha: np.ndarray, radius: int
           ) -> tuple[np.ndarray, np.ndarray]:
    """Box-blur color and alpha together, smearing every cell boundary.

    Blurring alpha too is the important part: it creates the halo of
    part-key, part-art pixels that `chroma_key`'s fringe pass exists to remove.
    """
    if radius < 1:
        return canvas, alpha
    k = 2 * radius + 1

    def blur(x: np.ndarray) -> np.ndarray:
        pad = np.pad(x, ((radius, radius), (radius, radius)) + ((0, 0),) * (x.ndim - 2),
                     mode="edge")
        acc = np.zeros_like(x, dtype=np.float64)
        for dy in range(k):
            for dx in range(k):
                acc += pad[dy:dy + x.shape[0], dx:dx + x.shape[1]]
        return acc / (k * k)

    return blur(canvas), blur(alpha)


def frame_of(sprite: Image.Image, index: int = 0) -> Image.Image:
    """One square frame out of a map-unit strip.

    Map-unit strips are `width / height` frames of `height` px (verified across
    all 55 files), so this needs no res.xml lookup. Battleanim strips do; use
    `convert.frame_counts` there.
    """
    h = sprite.size[1]
    n = max(1, sprite.size[0] // h)
    i = index % n
    return sprite.crop((i * h, 0, (i + 1) * h, h))
