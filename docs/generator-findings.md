# Generator findings

What image models actually return when asked for Commander Wars sprites, measured, and what the suite does about it. Current as of October 2026. The sheets measured here are the author's own Nano Banana 2 Lite generations (Venice.ai); the ones the tests use are in `testdata/ai sheets/`.

**Short version:** a usable AI route exists at **battle-animation / info-chibi scale**. Load the hand-made Orange Star reference sheet into the generator's **edit mode**, ask for a grid of variants, and run the sheet through the **AI Sprite Fixer**. What comes out is a set of CoW-encoded sprites in all 32 armies that are a good *starting point* for hand-fixing, not finished art. Map-tile scale (16–32 px) is out of reach: no model holds a grid that small.

## The workflow that works

```
pipeline/reference/AW2Style Units 4x green safety BG.png    (the "edit this" image)
        |
        v   Venice.ai -> Edit Image -> Nano Banana 2 Lite, 16:9 canvas,
        |   wildcard-generator prompt ending "Keep the same color palette
        |   and style as original image."
        v
JPEG sheet, 4x4-ish grid of variants on chroma green
        |
        v   CoW AI Sprite Fixer: key -> cut by layout -> snap at one pitch
        |   -> quantize (usage prior + team lock) -> merge specks -> encode
        v
name+mask.png per kept unit  ->  previewer, or army PNGs at Nx for other games
```

- **Edit mode is the only way to give Nano Banana a reference on Venice.ai.** Its "use reference image" option silently switches the model to Krea, which is much worse at this. Edit Image keeps Nano Banana 2 Lite and works well.
- **The reference carries the palette and the style.** Without one, Nano Banana picks its own colors whatever the prompt says, and drifts toward isometric views. With the reference loaded and the closing clause *"Keep the same color palette and style as original image."*, the units come back in Orange Star's reds and the AW look.
- **A 16:9 canvas gives side views more often than 1:1** — still a coin toss per sheet. The canvas ratio is set in the generator's UI; the prompt never mentions it.
- **Sheets beat single images.** One generation gives up to 16 variants, which matters because Nano Banana 2 Lite is paywalled and not cheap per image, and the layout comes back clean enough to cut. Any other image from `pipeline/reference/` works as the edit image when a specific look or unit type is wanted.

## The measurements

The five real sheets the fixer was built against, plus the hand-made reference for comparison:

| Sheet | Size | Colors | Layout returned | Grain | Grain strength | Native median width |
|---|---|---|---|---|---|---|
| AW2Style reference (hand-made, 4x) | 1280x1280 | 16 | 5x4 | 4.00 | **0.328** | — |
| Nanobanana Attempts (1) | 1024x1024 | 180,721 | 5x4 (asked 4x4) | none | 0.0008 | 96 (fallback) |
| Nanobanana Attempts (2) | 1376x768 | 163,280 | 4x4 | 2.84 | 0.0087 | 79 |
| Nanobanana Attempts (3) | 1376x768 | 165,245 | 4x4 | 2.84 | 0.0081 | 90 |
| cars2 (described palette) | 1376x768 | 155,062 | 4x4 | 2.84 | 0.0051 | 86 |
| cars (hex palette) | 1024x1024 | 221,383 | 6x4 | 3.40 | 0.0121 | 61 |

"Grain" is the period of the edge-energy autocorrelation pooled over every unit on the sheet, in sheet pixels per drawn pixel; "strength" is how much that period stands out (`aifix.measure_grain`).

### 1. The pixel grain is real but weak: ~2.84 px

Real pixel art at 4x scores a grain strength of 0.33. The Nano Banana sheets score 0.005–0.012 — about fifty times weaker, but consistently at **2.84 px** on the 1376x768 sheets (3.4 on cars.jpeg). Sheet (1) has no measurable grain at all. The model draws something pixel-shaped, but there is no grid to snap to cell by cell: per unit, the pipeline's grid detector returns anything from 3 to 30 px.

So the grain only sets the *finest* pitch worth snapping at, and that is the fixer's default size: one output pixel per pixel the model drew. On these sheets that puts the median unit at 79–90 px wide, about twice CoW's own battle sprites (ground units 39–75 px, median 47). Going finer would invent pixels; the fixer's size slider only goes down from native.

### 2. The scale is consistent across a sheet

The grain measures the same on every unit of a sheet, and the units come back at sensible sizes relative to each other. That is what lets the fixer snap **every unit at one pitch** with each unit's own offset fitted, instead of detecting a grid per unit. Units also touch through their drop shadows, so the component cutter (`split.py`) merges neighbours; the fixer cuts by the known layout at the quietest line near each expected gutter instead.

The layout is not always what was asked: sheet (1) came back 5x4 and cars.jpeg 6x4 for a 4x4 prompt. The fixer guesses the layout from the gutters, and the GUI lets you correct it.

### 3. JPEG costs color, and nearest-RGB makes it worse

Generators return JPEG. On a synthetic sheet built from CoW's own battle sprites at a non-integer scale (`test_aifix.py`), the geometry comes back exact and the colors do not: plain nearest-RGB quantizing gets **83%** of pixels rendering identically in all 32 armies. JPEG shifts colors onto the palette's near-twins, and some twins sit in table quadrants that do not follow the team color.

The fixer's quantizer measures:

| Step | Render-identical pixels (synthetic JPEG sheet) |
|---|---|
| Nearest RGB (the pipeline's quantizer) | 83% |
| Weighted YCbCr (luma x3) | 90% |
| + per-sheet usage prior | 92.8% (worst unit 80%) |
| + team lock | 92.0% (worst unit 75%) |
| + speck merging | **93.5%** (worst unit 81%) |

Lossless palette art stays 100% exact through all of it.

- **Team lock** keeps red-orange body pixels on cells that follow the team. Without it, 14.3% of the body on Nanobanana (3) lands on near-twin cells that stay orange in other armies; with it, 0.1% (CoW's own sprites: 3.6%, their gold trim and wood). The cost is that real gold accents the AI drew turn team-colored too; the switch is in the GUI.
- **Even out** (per unit, off by default) fixes a unit the model drew in the wrong shade — cars2's peach SUVs among red ones. It narrows the spread of body tones across the sheet from 30.7 to 3.5 luma, but on art that was already right it costs accuracy (93% to 74% on the synthetic sheet), so it is a per-unit tick, not a default.

### 4. The leftover grain is drawn shading

After quantizing, an AI unit's same-color regions average 2.5–3 px against 11 px in CoW's own battle sprites, with 39–49% of pixels in regions of 3 px or less (CoW: 9%). Speck merging at the default strength folds the JPEG specks back for a pure gain, but the rest is shading the model actually drew. Per-unit color budgets and median filters before snapping were both tried and made things worse; this is where hand-fixing starts.

Outlines and drop shadows, by contrast, already match CoW: silhouette edges are dark at 66–72% against CoW's 70%, and CoW draws the same black slab shadow under vehicles. The fixer leaves both alone.

## Prompting lessons

- **Hex-code palettes backfire.** "Use only these colors: #000000, #f04848, …" came back as cars.jpeg: 24 SUVs, each painted a different solid car color, with the codes read as paint choices. The option has been removed from the prompt builder.
- **Described palettes drift.** A worded red scheme without a reference (cars2.jpg) gets close but varies unit to unit, red to peach. Fine for another game, a chore for CoW, where the team-color mask depends on the reds. Even out exists for this.
- **Sheets are capped at 16.** An early Krea run returned 85 units when nothing capped the count, every one an unreadable blob — at the same pixel budget per unit as the reference. Count costs attention, not pixels.
- **A strong reference also carries shape.** At sheet scale, units drift toward the reference's silhouettes. That is mostly welcome in edit mode, where the reference is meant to set the style, but it narrows the variety.
- **Map scale is hopeless.** Map-tile attempts (Krea, and edit mode on `AW Map Units 6x Safety Green.png`) come back as mush; no model holds a 16–32 px grid. Draw map units by hand or downconvert them from finished battle sprites.

## Earlier conclusions this replaces

The first runs (Krea and one Nano Banana edit, September 2026) concluded that edit mode "destroys pixel art" and that text-to-image with an attached style reference was the only viable mode. The first half is true — edit mode keeps no grid, 16 colors in, ~150,000 out — but text-to-image keeps none either, and loses the palette and style on top. The fixer makes the missing grid a solved problem at battle scale, which leaves edit mode as the clear winner. The October qualitative note that view and color were "hit or miss" also predates the edit-mode workflow: with the reference loaded, color is reliable and the view is the remaining coin toss.
