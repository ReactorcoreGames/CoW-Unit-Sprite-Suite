# CoW's sprite palette format — verified spec

Derived from upstream source (`reference/spritecreator.cpp`, vendored from `spritingsupport/spritecreator.cpp` at `Robosturm/Commander_Wars@master`) and checked against Commander Wars' own sprite sets (`samples/`). Where a number appears below, it was measured, not assumed.

> **Corrected 2026-09-20.** An earlier revision of this document described the format as a 1D lookup into row 0 of the colortable, with transparent entries meaning "pass the source pixel through unchanged". **That was wrong.** It came from reading `createSprite` (an authoring/import path) instead of `recolorImageWithTable` (the runtime path), and from assuming rows 1..255 of the tables were decorative padding. They are the actual data. The consequence was that only ~7% of each sprite recolored — hulls stayed muddy grey across every team. The rule below is the correct one and is verified at 100% coverage.

## The one-sentence version

A CoW sprite's **red and green channels are 2D coordinates into a 256x256 colortable**: red is the X, green is the Y, and the pixel at that coordinate is the output color.

## The rendering algorithm

From `SpriteCreator::recolorImageWithTable`, verbatim:

```cpp
QColor pixel = image.pixelColor(x, y);
if (pixel.alpha() > 0)
{
    qint32 red = pixel.red();
    qint32 green = pixel.green();
    targetImage.setPixel(x, y, colorTable.pixel(red, green));
}
else
{
    targetImage.setPixelColor(x, y, Qt::transparent);
}
```

In practical form:

```python
out_rgb = table[src.green, src.red]   # numpy is [y, x]
alpha   = src.alpha                    # transparent stays transparent
```

Things that are **not** part of this rule, and were wrongly asserted before:

- There is **no fallthrough**. Every opaque pixel takes the table's color outright. A pixel is never "passed through unchanged".
- There is **no row-0 restriction**. The full 256x256 table is addressable.
- **Blue is unused.** It carries no meaning at render time.
- Green is **not** a cosmetic brightness record. It selects which palette row — i.e. which material role and unit class — the pixel belongs to.

### Verification

Every `(red, green)` coordinate appearing in the shipped sprite sets lands on an **opaque** cell of **every** team table:

| Set | Opaque px | Distinct coords | Coords defined in all teams |
|---|---|---|---|
| map units (55 files) | 92,754 | 22 | **100%** |
| battle animations (26 files) | 229,006 | 86 | **100%** |

Under the old row-0 reading the same measurement gives **7.3%**. That gap is the whole bug, and `tools/test_cowpalette.py` now asserts the 100% figure directly.

## Table layout

Each colortable is 256x256 and holds **four separate palettes** in quadrants, read like a 2x2 comic panel. `reference/colortables/Color_Codes.png` is the legend; the tables also label themselves in pixel text.

```
+------------------+------------------+
| UI               | TERTIARY         |
|   origin (0,0)   |   origin (129,0) |
+------------------+------------------+
| SECONDARY        | PRIMARY          |
|  origin (0,129)  | origin (129,129) |
+------------------+------------------+
```

Row and column 128 are a gutter carrying no swatch data — the lower quadrants start at 129, not 128. (Label text can overlap the gutter with black pixels; no sprite coordinate ever addresses it.)

Within each quadrant, swatches are **3x3 pixels**, not 1x1, arranged in rows by unit class in this order:

`BASE, UNIT, INFANTRY, SMALLVEHICLE, RECON, AIRVEHICLE, SHIP, LARGEVEHICLE`

`BASE` is populated only in the UI and PRIMARY quadrants; tertiary and secondary leave it empty in the team tables. In `mask.png` an unused slot is drawn as a black X. Every team table also carries a 3x3 **ID swatch** in the extreme lower-right corner.

The 3x3 swatches and the labelling are there to make the tables hand-editable. **None of it has to be modelled to recolor correctly** — the 2D lookup handles the layout implicitly. It matters only to the converter (`tools/convert.py`), which must *choose* coordinates when encoding new art.

### What the four palettes are for

This is what lets one table serve every material in a sprite:

- **primary** — the main team-colored body: hull plating, uniforms
- **secondary** — a second team-related ramp
- **tertiary** — a third
- **ui** — interface elements, a single row

Neutral materials are not a special case or an exception to team coloring — they are simply coordinates whose cell holds a grey or brown in *every* team table. That is why treads, wheels, inner mechanism, window glass and ship wakes stay consistent while hulls change: greys are in the table, deliberately, per team.

## Using the tables

The tables are copied verbatim to `reference/colortables/` (33 tables plus the `Color_Codes.png` legend) and used **as-is**. `tools/cowpalette.py` loads the PNG and indexes it; nothing is parsed, decoded, or regenerated. `reference/palettes.json` and `tools/extract_palettes.py` are obsolete artifacts of the old row-0 model and should not be used.

`mask.png` is the authoring/source table. Rendering a sprite to `mask` reproduces its own encoded colors, which makes it a useful round-trip check.

## The `useColorBox` path — do not confuse it

`SpriteCreator::createSprite` has a second branch using `colorBoxSize = 43`, `getColorBox` and `getImageColor`. This is **fuzzy** matching that buckets colors into a coarse grid, used by the **CO-style editor** (`dialogcostyle.cpp`), not by unit rendering. It is also the path the earlier, incorrect reading of this document was drawn from. Do not use it for unit sprites.

## The three sprite categories

Advance Wars: Dual Strike has three per-unit sprite types. **CoW only ships two of them.**

| # | Type | Size / shape | In CoW? | Where ours live |
|---|---|---|---|---|
| 1 | Map tile sprites | 16x16 (and up), square frames | **yes** | `samples/cow map units/` |
| 2 | Info chibi sprites | ~64x64, confined to a square | **no** | — (AWDS only; wanted for the author's own game) |
| 3 | Battle animation sprites | freeform, no fixed ratio | **yes** | `samples/cow battle animations/` |

Category 2 does not exist in CoW. It is still a first-class target for this project because the author wants it for their own game — it simply has no CoW counterpart to validate against, and no mod path.

### Category 1 — map tile sprites

All 55 files are **horizontal strips of exactly 4 frames**, each frame square:

| File size | Frame | Count |
|---|---|---|
| 64x16 | 16x16 | 24 |
| 80x20 | 20x20 | 1 |
| 96x24 | 24x24 | 3 |
| 128x32 | 32x32 | 27 |

Frame size is `height`; frame count is `width / height`. "16x16 map units" is the common case but **not universal** — the previewer must derive frame size from the image rather than assume 16.

Measured: **92,754** opaque px, **7.3%** team-colored, **11 distinct indices** (`2, 5, 8` team; `130..151` step 3 literal).

### Category 3 — battle animation sprites

Side-view, tightly cropped to content, genuinely freeform. Sizes in the shipped set include `33x32`, `48x56`, `49x64`, `100x70`, `64x73` — odd numbers, no consistent aspect ratio. A handful of naval/air units are multi-frame strips instead (`battleship` 896x160, `lander` and `submarine` 640x96, `cruiser` 768x128, `megatank+cannon` 448x64, `t_heli` 256x104).

**Frame count is not reliably derivable from dimensions here.** It is, however, *recorded* in the Commander Wars source: `resources/images/battleanimations/res.xml` and `.../units/res.xml` carry `cols`/`rows` per sprite. Read it from there rather than guessing; `convert.frame_counts` does, when a checkout of the game's repository sits in `upstream/`.

**(Original note:)** `640x96` could be 4, 5, 8 or 10 frames. If we ever need to split these, the count has to come from CoW's `res.xml` or be entered by hand — do not guess it from the aspect ratio.

Measured: **229,006** opaque px, **5.1%** team-colored, **16 indices** — team `2, 5, 8, 11`, literal band `130..160`, plus a trace of index `14`.

### On index 14 and the "5th ramp step"

Battleanim sprites contain a few pixels at index 14, which looks at first like a 5th team step. It is not usable as one: **team tables define opaque entries only at indices 1-12**, so index 14 always falls through to literal. It accounts for 268 px across 26 sprites (0.1%) and is almost certainly encoder drift, not intent.

**The 4-step ramp (canonical slots 1/4/7/10) holds for all sprite types.** Our converter should emit only those.

## Reference file naming

Shipped sprites follow `<unit>[+<variant>][+walk]+mask.png` — e.g. `apc+mask.png`, `apc+walk+mask.png`, `infantry+ac+mask.png`. The `+mask` suffix marks the index-encoded form. If we ever emit sprites for direct drop-in, match this convention.
