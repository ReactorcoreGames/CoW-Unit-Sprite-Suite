# CoW Unit Sprite Suite — Guide

## Quickstart

1. Draw your unit in **Orange Star red** on a flat **`#00ff00`** green background. Use any image from `pipeline/reference/units/1x/` as a style and palette guide. Several units on one canvas is fine — leave a few pixels between them.
2. Open **CoW Sprite Previewer.exe** and click **Cut sheet…**. Pick your drawing. Each unit is cut out, its pixel scale found, and converted into Commander Wars' mask format.
3. Your units open in the previewer straight away (they're saved in `output/cut/<your sheet>/`). **W / S** flips through all 32 army colors.
4. The `+mask.png` files are the finished mod assets. CoW recolors them per army itself.

That's the whole loop. The rest of this guide is detail.

In all three programs, **hover over any button or box for a short explanation**. The **Guide** button (top right) opens this file, and **Light / Dark** next to it switches the look. The previewer and the AI Sprite Fixer start dark (sprites read better on it) and the wildcard generator starts light. **Esc** leaves a text box so the keyboard shortcuts work again; it never closes a program.

---

## What's in the folder

| Item | What it is |
|---|---|
| `CoW Sprite Previewer.exe` | Preview any CoW sprite in any army color, export PNGs, and convert your art (**Cut sheet…**, **Convert folder…**) |
| `CoW Wildcard Generator.exe` | Rolls random vehicle ideas and builds image-generator prompts from them (Route B) |
| `CoW AI Sprite Fixer.exe` | Turns an AI-generated sheet into CoW sprites and army-colored PNGs (Route B) |
| `_internal/` | Program files for all three exes. Leave it alone |
| `pipeline/reference/` | Reference art: 91 single AW2 units from all five armies, recolored to Orange Star red on green (6x, with `1x/` originals), class sheets, and the two hand-drawn sheets — `AW2Style Units 4x green safety BG.png` (battle scale, the image to edit in Route B) and `AW Map Units 6x Safety Green.png` (map scale) |
| `pipeline/palette/` | The exact palette per unit class, as swatches. Stay on these colors and conversion is lossless |
| `pipeline/classes.json`, `pipeline/prompts.json` | Palette and prompt data the programs read. `prompts.json` is safe to reword |
| `reference/colortables/` | Commander Wars' 33 team colortables plus `mask.png`. Used as-is |
| `wildcards/` | The wildcard word lists. Plain text, editable |
| `wildcards.config.example.json` | Optional settings for the wildcard generator; instructions inside |
| `samples/` | Commander Wars' own map units and battle animations in mask format, for trying the previewer |
| `LICENSE.txt`, `*.url` | The suite's MIT license, and shortcuts to the Commander Wars community and the author's links |
| `output/` | Created on first use. Everything the programs export or convert goes here (the fixer's results in `output/aifix/`) |

---

## How CoW sprites work (one paragraph)

A Commander Wars unit sprite is not a normal picture. Its **red and green channels are coordinates** into a 256x256 color table, and the game swaps in a different table for each army. That's why a CoW sprite looks like washed-out green-and-brown nonsense on its own — the "ugly palette", properly called the mask palette, which is why the files end in `+mask.png` — and why one file serves all 32 armies. You never draw in that format. You draw in Orange Star red, and the suite encodes it.

---

## Route A — draw it yourself (recommended)

### Drawing

- **Colors:** Orange Star red for the team-colored parts, plus the neutral greys, blacks, skin tones and water blues the reference units use. `pipeline/palette/<class>_swatch.png` shows each class's exact colors. Off-palette colors still work — they get snapped to the nearest one — but you lose control over the result.
- **Red means team color.** Anything red changes per army. Keep red off parts that should stay the same in every army.
- **Background:** exactly `#00ff00`, flat. No anti-aliasing against it, no soft brushes on edges.
- **Size:** CoW map units are 16–32 px. Battle animations are freeform. Draw at 1x, or upscale by any whole number. The cutter finds each unit's scale separately, so mixed scales on one sheet are fine.
- **Spacing:** keep units at least 2 px apart (at 1x). Parts closer than that count as one unit, which is what keeps a shadow or a rotor attached to its vehicle.

### Converting

- **A sheet with many units → Cut sheet…** Output goes to `output/cut/<sheet>/`. Units are numbered in reading order; `rgb/<sheet>.index.png` shows the numbers. To name them, put a text file named `<sheet>.names.txt` next to the sheet, one name per line in that order (`-` keeps the number), and cut again.
- **A folder of single-unit images → Convert folder…** Output goes to `output/converted/`. Do not put a multi-unit sheet through this — it will lock onto the spacing between units and give confident nonsense. Sheets go through Cut sheet.
- Both ask which **background color** to remove: green `#00ff00` (the default, and what hand-drawn art uses) or magenta `#ff00ff` (for AI images made on a magenta background, or art that itself uses bright green).
- Both ask which **class palette** to use. Leave it on `all` unless you have a reason not to; class is not a color limit, so a tank's driver keeps their skin tone.

What happens inside: green is keyed out → the true pixel grid is found → each pixel cell is reduced to its main color → colors snapped to the palette → encoded into CoW's mask format → checked against all 32 army tables. Anything the converter is unsure of goes to `needs-review/` with its in-between images and a reason, instead of being guessed at.

### Previewing and exporting

| Key | Action |
|---|---|
| A / D (or ← →) | previous / next sprite |
| W / S (or ↑ ↓) | previous / next army color |
| Q / E | previous / next background (dawn, day, dusk, night, key green, key magenta) |
| R / F | zoom out / in |
| T | next animation frame (set the frame count with **Split into**) |
| Ctrl+O | open a folder |
| Ctrl+E | export the current sprite in the current army |
| Ctrl+Shift+E | export every sprite in the current army |
| Ctrl+G | export a collage of every sprite |

The arrow buttons under the preview do the same as the keys, for mouse users. Exports go to `output/` and are named `sprite+army.png`. Check that each unit reads well in several armies and on both light and dark backgrounds.

### Into Commander Wars

The `+mask.png` is the mod asset — it ships as-is and CoW handles the army colors. The files in `samples/` show the naming CoW itself uses (map units like `apc+mask.png`, battle animations like `heavy_tank+os+mask.png`).

---

## Route B — AI-assisted, battle scale

**What to expect:** sheets of battle-animation or info-chibi sized units that are a good starting point for hand-fixing, not finished sprites. Map-sized units (16–32 px) are out of reach for current image models — draw those by hand, or downconvert them from a finished battle sprite. Tested in October 2026 with Nano Banana 2 Lite on Venice.ai, which is paywalled; free models tried were not usable.

The whole loop: **roll a subject → generate a sheet in edit mode → AI Sprite Fixer → previewer or export.**

### 1. Roll a subject and copy the prompt

Open **CoW Wildcard Generator.exe** and press **Randomize** (or Space). Lock slots you like and re-roll the rest. **Unarmed civilian** gives vehicles with no weapons. Each list is a `.txt` file in `wildcards/` — add a file and it becomes a new slot, delete one and it goes away. Every entry ends in `__`, which becomes a space; keep it.

Below the subject, **View** starts on *Slightly elevated side* and **Layout** on a *4x4* *Sheet* — the combination that worked for battle sprites. (*Oblique* matches CoW map units; the *Direct* side, front and top views are there for other art styles, from total-conversion mods to a game of your own.) Keep **Background** on *Chroma green* (or *Chroma magenta* if the subject itself is green) and **Palette** on *Keep reference colors*. Then **Copy full prompt**. It ends with "Keep the same color palette and style as original image.", which is what keeps the model on Orange Star's colors.

### 2. Generate in edit mode

1. In the image generator, use **edit image** mode, not text-to-image. On Venice.ai that is **Edit Image** with **Nano Banana 2 Lite**. Do not use Venice's "use reference image" option: it switches the model to Krea, which does much worse.
2. Load `pipeline/reference/AW2Style Units 4x green safety BG.png` as the image to edit. It is a hand-drawn sheet of 20 battle-scale units in Orange Star red, and sheets come back better and cheaper than single images. Any other image from `pipeline/reference/` works too when you want a particular look or unit type. The wildcard generator's **Reference sheets** button opens that folder.
3. Set the canvas to **16:9**. It gives side views more often than a square canvas — still a coin toss per sheet.
4. Paste the prompt and generate. Without the reference loaded, the model picks its own colors and drifts toward isometric views.

### 3. Fix the sheet

Open **CoW AI Sprite Fixer.exe**, click **Open sheet** (Ctrl+O) and pick the image (JPEG, PNG or WebP). The sheet is cut and processed in a few seconds; the left side shows it with the cut lines, the right side shows every unit as a CoW sprite.

- **Check the cut lines.** The model sometimes returns 4x3 or 5x4 when asked for 4x4. If lines cut through units, set **Layout** (rows x columns) to what is really there; **Guess** goes back to the automatic layout.
- **Size** starts at *native*: one pixel per pixel the model drew (on Nano Banana sheets, about 80–90 px wide — twice CoW's own battle sprites). Native is as big as the art supports. Slide left, or press **[** / **]**, for smaller sprites; the label shows the middle unit's width and the range across the sheet.
- **keep** under each unit decides whether it is exported. Untick the duds. Space toggles the selected unit; double-clicking a unit, on the sheet or in the results, does the same.
- **even** pulls one unit's body colors toward Orange Star's usual tones. Use it on a unit the model drew in the wrong shade (a peach car among red ones); leave it off on units that look right, because it flattens their shading. V toggles it for the selected unit.
- **Team lock** (on by default) makes sure every red-orange body pixel changes color with the army. Turn it off only to keep gold or wood accents the model drew, at the risk of orange patches in other armies.
- **Smooth** folds tiny specks into a similar neighbour. 40 is the measured sweet spot; higher flattens more of the AI grain but starts eating real detail.
- **Backdrop** is normally *auto*, read from the sheet's border. Force *green* or *magenta* if the automatic pick goes wrong.
- **W / S** shows the results in any of the 32 armies, through the game's own color tables; *mask* shows the files as CoW stores them. **R / F** zooms the results.

| Key | Action |
|---|---|
| A / D (or ← →) | previous / next unit |
| Space | keep / reject the selected unit |
| V | even out the selected unit |
| W / S (or ↑ ↓) | previous / next army |
| R / F | zoom the results out / in |
| [ / ] | smaller / bigger size |
| Ctrl+O | open a sheet |
| Ctrl+E | export |
| Ctrl+P | export, then open the results in the previewer |

### 4. Export

**Export** (Ctrl+E) writes the kept units to `output/aifix/<sheet name>/`:

- `<sheet>_NN+mask.png` — the CoW sprite, always at 1x. This is the mod asset.
- `rgb/<sheet>_NN.png` — the same unit in Orange Star red, for touching up in an image editor and converting again with Route A's **Convert folder…**.
- `armies/<sheet>_NN+<army>.png` — army-colored PNGs, set by **Export armies**: *none*, *this army* (the one on screen) or *all armies*. With **at** above 1x they are blown up by that whole number, nearest neighbour, into `armies Nx/` — crisp sprites for a game or tool that is not Commander Wars.

Exporting again replaces the earlier files; a unit you have since rejected has its old files moved into `rejected/`, so nothing is lost. **Open in previewer** (Ctrl+P) exports and opens the folder in the previewer, for backgrounds, collages and everything else in Route A's previewing step.

Every prompt string lives in `pipeline/prompts.json` and can be reworded there.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Output is tiny or huge | A whole sheet went through Convert folder | Use Cut sheet |
| Two units came out as one | They're less than 2 px apart | Move them apart |
| A unit came out in pieces | A detached part sits more than 2 px away | Move it closer, or bridge it |
| Files land in `needs-review/` | The converter couldn't find a clean pixel grid | Read the reason file; usually the art isn't on a clean whole-number scale |
| Green fringe on edges | Background not exactly `#00ff00`, or anti-aliased edges | Redraw edges hard against the green |
| Colors shifted after converting | Art used colors off the palette | Compare against `pipeline/palette/` |
| Wrong colors in other armies, right in red | A color used for the wrong material (e.g. red on a neutral part) | Keep red to team-colored parts only |
| Previewer opens empty | `samples/` was moved | Use **Open folder** |
| Your drawing shows as a flat block in the previewer | It's still red-on-green art, not a mask sprite. The previewer only displays `+mask.png` files | Convert it first with **Cut sheet…** or **Convert folder…** |
| Wildcard generator shows a config error | A typo in `wildcards.config.json` | Fix the JSON, or rename the file to switch it off |
| Fixer: cut lines run through units | The model returned a different grid than asked | Set **Layout** to the real rows x columns |
| Fixer: one unit in the wrong shade (peach, washed out) | The model drifted on that unit | Tick **even** under it |
| Fixer: orange patches in other armies | Team lock is off | Switch **Team lock** on |
| Fixer: sprites too big for CoW | Native size is about twice CoW's battle sprites | Slide **Size** down (CoW's own are ~48 px wide) |

---

## Contact

mailto:reactorcoregames@gmail.com

Made by Reactorcore — https://reactorcoregames.github.io/ · https://linktr.ee/reactorcore · https://reactorcore.itch.io/
