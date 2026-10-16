# Pipeline materials

Everything needed to take an AI-generated sprite to a CoW-compatible one. The materials are sampled per **unit class**, but conversion defaults to the union of all of them. Built by `tools/build_pipeline.py` from `pipeline/source/` (real Advance Wars 2 sprites, all five armies); regenerate rather than hand-edit.

## The workflow

```
1. pick a reference      AW2Style Units 4x green safety BG.png (battle scale), or any
                         image here: units/, a class sheet
2. feed the generator    edit mode on that image  +  a prompt from tools/prompts.py
3. get art back          Orange Star red on flat #00ff00, a sheet of variants
4. convert               AI sheet: the AI Sprite Fixer (tools/aifix.py)
                         hand-drawn: chroma-key -> pixel-snap -> quantize (all) -> encode
5. out                   sprite+mask.png   <- the master
      |
      +-- CoW mod:  ships as-is
      +-- own game: previewer exports any of the 33 teams
```

**Author once, in Orange Star. Everything else is a table lookup.** You never generate Blue Moon or Black Hole art — those fall out of the colortable for free, which is what the previewer already demonstrates.

## Why the default is `all`, not one class

The palettes were first meant to be picked per class, on the theory that a narrow palette leaves off-model colors nowhere wrong to go. That theory treats class as a color restriction, and it is not one. A tank can carry a driver's skin tone, a car's windows can use the ship palette's water blues, and a masked soldier or humanoid robot has no skin at all. A single-class palette snaps those pixels to the wrong color, silently.

So `convert.py` synthesizes an `all` palette: the union of the six, **63 colors**, and both `batch_convert.py` and the previewer use it by default. This is safe because of a measured property of `classes.json`: all 30 colors that appear in more than one class encode to coordinates that render identically in all 32 teams, so merging them loses nothing. `test_convert.py` asserts it. On each class's own reference sheet, quantizing against `all` gives exactly the same colors and coordinates as quantizing against the class itself.

The single-class palettes remain available (`--class ship`) for deliberately constraining a conversion.

## What is here

| Path | What it is |
|---|---|
| `classes.json` | The six class palettes, each color with its `(red, green)` encode coordinate |
| `reference/<class>.png` | 6x nearest-neighbour sheet on `#00ff00` — **this is what you feed the generator** |
| `reference/<class>_1x.png` | The same sheet at native size, for reference |
| `reference/units/<Unit> - <Army>.png` | 91 single units, every AW2 unit from all five armies recolored to Orange Star red, 6x on `#00ff00`. Built by `tools/build_references.py` |
| `reference/units/1x/` | The same units at native size |
| `palette/<class>_swatch.png` | The class palette as swatches; a magenta corner dot marks an inexact color |

Sheets are upscaled 6x because models do not emit alpha, and large blocky pixels make the pixel structure the dominant feature to imitate.

## classes.json

Each palette entry:

```json
{
  "rgb": "#f04848",        // the authored color, as it appears in AW2 art
  "pixels": 4349,          // how often it occurs in that class
  "share": 0.14,
  "exact": true,           // is it verbatim in orange_star.png?
  "encode_rgb": "#f04848", // the table color it encodes to
  "red": 129, "green": 138,// the coordinate a CoW sprite stores
  "quadrant": "primary",
  "distance": 0.0          // RGB distance when not exact
}
```

`red`/`green` are the payload: a finished sprite stores those two numbers per pixel, and CoW renders `table[green, red]` for whichever team owns the unit.

## Fidelity

How much of each class's art lands verbatim on CoW's colortable:

| Class | Units | Colors | Exact | Pixels exact |
|---|---|---|---|---|
| infantry | 2 | 12 | 12 | **100.0%** |
| recon | 1 | 24 | 17 | 92.2% |
| smallvehicle | 6 | 24 | 24 | **100.0%** |
| largevehicle | 2 | 24 | 24 | **100.0%** |
| airvehicle | 4 | 33 | 15 | 57.0% |
| ship | 4 | 17 | 14 | 86.7% |

Inexact colors snap to the nearest table cell. Nearly all are distance **8-18**, which is shade drift between AW2 revisions and invisible in practice — `airvehicle`'s low percentage is many such near-misses, not bad mappings.

**Pinned blues.** CoW's tables hold no blue that is constant across teams, so nearest-RGB sent the three AW2 water/glass blues to cells that turn grey, green or even gold in other armies. `build_pipeline.py` pins them instead: `#c0e0f8` and `#7098e8` go to `(145,4)`, the water cell every stock CoW ship uses (pale cyan in 23 teams, mid blue in 9), and `#586098` goes to `(139,7)`, dark teal-blue in 31 of 32 teams. Their large distances in the JSON are expected, because the RGB is farther away but the material is right. `test_convert.py` checks that every blue renders blue in at least 31 teams.

## Notes on scope

- Only **Orange Star** is analysed for the palettes. The other four armies in `pipeline/source/` are **not** recolors: AW2 draws each unit differently per army, and only the Neo Tank is the same drawing in all five. They add designs rather than palette information, which is why they feed the reference gallery and not `classes.json`.
- `recon` has one unit in AW2, hence the thin sample. The `all` default makes that thinness harmless.

## The reference gallery

`tools/build_references.py` turns all 95 `pipeline/source/` sprites into 91 references; the Neo Tank's four duplicates are dropped. Each colour in another army's sprite is resolved by the first rule that applies:

1. **AW2 neutrals stay.** A colour drawn by all five armies (blacks, whites, skin, tread greys, the three water blues) is not a team colour. Without this rule, Blue Moon's water turned pink, because its pale water blue doubles as a highlight in Blue Moon's table.
2. **Neo Tank calibration.** The Neo Tank is one drawing in every army, so it gives exact army-to-red colour pairs.
3. **CoW's colortables.** The same cell holds Blue Moon's blue in `blue_moon.png` and the matching red in `orange_star.png`. Every cell holding a colour votes, weighted by how much CoW's stock sprites use that cell.

Some greys survive on purpose. Yellow Comet really does draw grey roofs and dark cabs. `tools/test_references.py` checks coverage, the green border, the exact 6x copies, and that every ship keeps its water.
