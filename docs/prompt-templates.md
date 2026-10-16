# Prompt templates (revision 4)

Revision 4 is the author's own format. A prompt is eight parts in a fixed order, each picked independently, assembled from `pipeline/prompts.json` by `tools/prompts.py`. The wildcard generator's **Copy full prompt** row does the same with the subject it just rolled.

```
python tools/prompts.py --list
python tools/prompts.py -s "suv vehicle armed with a shock launcher" --view elevated_side --layout sheet --grid 4x4
```

## How the prompt is used (October 2026)

The prompt goes into the generator's **edit mode**, with a reference sheet loaded as the image to edit — by default `pipeline/reference/AW2Style Units 4x green safety BG.png`, or any other image from `pipeline/reference/` when you want a particular look or unit type. On Venice.ai that is **Edit Image** with Nano Banana 2 Lite; its "use reference image" option switches the model to Krea, which does much worse. Set the canvas to **16:9** in the generator, which gives side views more often than 1:1. The resulting JPEG sheet goes into the **AI Sprite Fixer**. The evidence is in `docs/generator-findings.md`; the steps are in `guide.md`.

## The parts

| # | Part | Options (default first) |
|---|---|---|
| 1 | Subject | `<subject> as a game sprite in the style of Advance Wars.` |
| 2 | Addon | bold simple shape, readable very small · **none** · detailed meticulous shape |
| 3 | View | side · front · top · **elevated side** · three-quarter orthographic · oblique 45-degree slant |
| 4 | Layout | single (centered, isolated, filling the frame) · **sheet** (2x2, 3x3, **4x4** grid of distinct designs) |
| 5 | Background | **chroma green** · chroma magenta · `{color}` themed environment · abstract `{color}` theme · black-to-`{color}` gradient |
| 6 | Style | fixed: chunky low-resolution pixel art suffix |
| 7 | Outlines | **on**: "Thick dark outlines." · off |
| 8 | Palette | **keep**: "Keep the same color palette and style as original image." · none · described: "Warm coral-red, orange-peach and pale peach body colors with dark burgundy and muted purple-gray shadow clusters." |

Defaults are in bold. The wildcard generator also starts with only slots `a2`, `a3` and `c3` enabled (`default_slots` in `wildcards.config.json` changes that). The parts are joined into one paragraph. An empty part, such as addon *none*, outlines off or palette *none*, just drops out. `{color}` is one of red, orange, yellow, green, blue, indigo, violet, pink, magenta, cyan, teal, brown, gold, silver, white or grey, with blue as the default. Every string is data in `prompts.json`, so rewording one is an edit, not a code change.

The defaults, **elevated side** view and a **4x4** sheet, are what worked for the battle-scale sheets the fixer is built for. *Oblique* matches CoW map units.

The *detailed* addon deliberately pulls against the style suffix's "chunky low-resolution". It is there on purpose, as an option for when that tension is what you want.

## The palette request

Last in the prompt. **Keep** is the default and belongs with edit mode: the reference sheet is the image being edited, and the clause keeps the model on its Orange Star colors and Advance Wars style. Without a reference loaded it does nothing useful — the model picks its own colors whatever the prompt says.

**Described** is for generating without a reference. It gets close, but the shade drifts from unit to unit (red to peach on cars2.jpg); the fixer's per-unit *even out* repairs some of that. It leaves out highlights and light-dark separation, since the style suffix already asks for them.

**There is no hex-code option any more.** It listed 12 of the converter's colors as hex codes, and Nano Banana painted them on as car paint: cars.jpeg came back as 24 SUVs in 24 different solid colors. The fixer snaps every color on import in any case.

## What is deliberately left out

- **Aspect ratio.** The canvas ratio is set in the generator's UI (16:9 is recommended above), so the prompt never chooses, suggests or mentions one. A test checks that no prompt contains a ratio.
- **Unit class.** A tank prompt and a frigate prompt read the same.
- **The reference image's name or description.** You load it in the generator. The only words spent on it are the keep clause's "original image".

## Backgrounds and the tools

The fixer and `convert.py` can chroma-key only **green** and **magenta**, so only those two backgrounds give art that goes straight through. Use magenta when the subject itself is green. The themed backgrounds are for concept art, or for anything you plan to cut out by hand. The CLI and GUI tell you which case you are in when you build the prompt.

## Carried over from earlier revisions

These come from measurements in `docs/generator-findings.md`:

- **Sheets are capped at 16.** One run returned 85 units and another 29, and both were unreadable. Pixel budget per unit was not the problem; the model's attention was.
- **The sheet layout asks for even spacing, wide flat gaps, the same size and the same angle.** The fixer cuts by layout and needs the gutters; the model still sometimes returns 5x4 or 6x4 for a 4x4 request, which the fixer's layout control corrects.
- Revision 4 asks for pixel art outright ("crisp stepped pixel edges"). No model holds an exact grid, but the wording gets a consistent ~2.84 px drawn grain, which the fixer uses as its native size.

The old rule "attach the reference as a style reference, never as an image to edit" is **obsolete**. Edit mode keeps no pixel grid, but neither does text-to-image, and edit mode is the one that keeps the palette and style; the fixer handles the grid.

## What is verified

`tools/test_prompts.py` has 92 checks. It checks that the eight parts appear in the specified order with the chosen defaults, that all 21,600 option combinations assemble cleanly within budget for the longest wildcard subject (the worst case is 936 of 1000 characters), that the keep clause is the default and lands last, that no palette option lists hex codes, that no aspect ratio appears anywhere, that empty parts drop out cleanly, that the default background is one the tools can key, and that the sheet wording keeps what the cutter needs.

How a prompt generates is not something a test can check; `docs/generator-findings.md` records what real runs returned.
