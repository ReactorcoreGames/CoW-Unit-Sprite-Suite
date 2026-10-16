# CoW Unit Sprite Suite

A mostly-manual workflow suite for making unit sprites for **Commander Wars (CoW)**, an Advance Wars clone for PC (https://github.com/Robosturm/Commander_Wars, **LGPL-3.0** — the upstream LICENSE file is the LGPL, not the GPL). The modder draws units in a friendly palette (Orange Star red on `#00ff00`), the suite converts them into CoW's index-encoded mask format, and a previewer shows them in any team color.

**Status: ready for its first public release, v1.0 (October 2026), with three exes.** All deliverables are built and tested. The user-facing docs are `readme.md` and `guide.md` in the root; `build_release.bat` produces `dist/CoW Unit Sprite Suite/` and its zip. Test fixtures live in `testdata/` (hand-drawn sheets + `ai sheets/`, the real AI sheets the fixer tests, promo and screenshots use). Everything retired — the old `context/` corpus, the full AI experiment folder, the user's favourite fixer results, the dev history — is parked in `old wip/` (git-ignored); read it only for archaeology.

## The deliverables

| Tool | Files | Ships as |
|---|---|---|
| Palette core | `tools/cowpalette.py` | (library) |
| Conversion pipeline | `tools/convert.py` (core), `tools/batch_convert.py` (CLI), `tools/split.py` (sheet cutter, core + CLI) | buttons in the previewer |
| Previewer / recolor exporter | `tools/previewer.py` | `CoW Sprite Previewer.exe` |
| Shared GUI chrome | `tools/guikit.py` (icon, light/dark switch, Guide button, tooltips, muted text style) | (all three exes) |
| Prompt templates | `pipeline/prompts.json`, `tools/prompts.py` | inside the wildcard generator |
| Wildcard vehicle generator | `tools/wildcards.py` (core, GUI-free), `tools/wildcard_gui.py` | `CoW Wildcard Generator.exe` |
| AI sprite fixer | `tools/aifix.py` (core + CLI, GUI-free), `tools/aifix_gui.py` | `CoW AI Sprite Fixer.exe` |

Loosely coupled: GUIs hold no logic (tests assert they call the cores — `test_wildcards`, and `test_aifix.test_gui_holds_no_logic` for the fixer), and `convert`/`split` are shared by CLI and GUI without reimplementation.

Build/support scripts: `tools/build_pipeline.py` (builds `pipeline/` from `pipeline/source/`), `tools/build_references.py` (the 91-unit gallery), `tools/build_promo.py` (promo images, from real pipeline output), `tools/build_screenshots.py` (the two GUI shots in promo/, needs a desktop), `tools/build_trailer.py` (the YouTube trailer, thumbnail and `youtube.md` in `promo/trailer/`, from real pipeline output; `--preview`, `--stills`, `--music`, `--bpm`), `tools/record_trailer_clips.py` (the previewer/fixer GUI clips it uses, one PNG per UI state, needs a desktop), `tools/synth.py` (synthetic damaged sprites for testing), `tools/_verify_recolor.py`.

**Two routes.** Route A, hand-authored, is the primary use and is verified end to end. Route B, AI-assisted, works at **battle / info-chibi scale only** (map-scale AI output is hopeless): the wildcard generator's prompt goes into the generator's **edit mode** — on Venice.ai, *Edit Image* with Nano Banana 2 Lite (paywalled; "use reference image" silently switches to Krea, which is worse) — editing `pipeline/reference/AW2Style Units 4x green safety BG.png` (the user's own hand-drawn sheet; "Main Input - BattleAnim.png" in `old wip/Experimental AI art workflow/` is the same art) on a 16:9 canvas (side view more often, still a coin toss), prompt ending "Keep the same color palette and style as original image." Without a reference NB picks its own colors and drifts isometric. The JPEG sheet then goes through the AI Sprite Fixer; the output is a starting point for hand-fixing. Evidence: `docs/generator-findings.md`.

## Paths and the frozen build

Every module that reads data by path takes `ROOT` from `tools/approot.py`: the project root from source, **the exe's own folder when frozen**. Never derive a data path from `__file__` in a new module — a test in `test_wildcards.py` enforces this for the five data-reading modules. The release layout therefore mirrors the project layout: `reference/colortables/`, `pipeline/`, `wildcards/`, `samples/` sit beside the exes.

Data each tool needs: `cowpalette` → `reference/colortables/`; `convert`/`split`/`aifix` → `pipeline/classes.json` (aifix reads nothing itself; it goes through `convert` and `cowpalette`); `prompts` → `pipeline/prompts.json` (+ `pipeline/classes.json` for the palette hex list); `guikit` → `guide.md` and, from source, `promo/icon_*.ico`; `wildcards` → `wildcards/`; previewer start folder → `samples/cow map units`. Source and release use the same paths for all of these. `convert.frame_counts` reads CoW's `res.xml` from an optional checkout of the game's repo in `upstream/` (git-ignored) and returns `{}` when it is absent.

## Release build

`build_release.bat` (add `notests` to skip tests): runs every `tools/test_*.py`, builds `cow_suite.spec` with PyInstaller (three windowed exes in **one one-dir COLLECT**, sharing one `_internal/`; one icon each, `promo/icon_previewer.ico`, `promo/icon_wildcards.ico`, `promo/icon_aifix.ico`), copies the data beside them, zips to `dist/`. One-dir is deliberate: the wildcard lists must stay user-editable beside the exe. Data comes from `wildcards/`, `samples/`, `pipeline/` (reference, palette, the two json files), `reference/`, `promo/` (minus `.ico`, `trailer/`, `source/`), and the root `.md` files.

Ships: three exes, colortables + `reference/Commander_Wars-LICENSE-LGPL-3.0.txt`, pipeline materials and reference gallery, wildcard lists + `wildcards.config.example.json`, CoW sample sprites, promo, readme/guide/promo.md. Does **not** ship: Python sources, tests, `testdata/`, `pipeline/source/`, `docs/` (developer docs, repo only — `guide.md` is the user-facing version and must not link into `docs/`).

`python tools/build_promo.py` regenerates every promo image and both icons. `promo/icon_og.png` is the red hero tank (`pipeline/reference/units/1x/Tank Heavy - Blue Moon.png`, which like every reference unit is drawn in Orange Star red), keyed and 17x upscaled onto 1024x1024; the cover and thumbnail use it. The exe icons are that tank converted to its mask and recolored through CoW's tables: **Blue Moon for the previewer, Yellow Comet for the wildcard generator, Green Earth for the AI sprite fixer**. Their `.ico` files hold **BMP entries** (`bitmap_format="bmp"`): Tk's own .ico reader shows a blank title-bar icon for Pillow's default PNG entries, and `iconphoto` shows a blank too. From source `guikit.set_icon` loads the .ico; frozen, it reads the icon embedded in the exe. `07_wildcard_ideas` is a cloud of real subjects rolled from the default slots. `09_ai_fixer` is one unit of `testdata/ai sheets/Nanobanana Attempts (3).jpg` through the fixer (crop → sprite → 4 armies). `11`-`16` are annotated versions (title, numbered badges, legend/caption) of the user's screenshots and results in `promo/source/` (repo only, never ships). The trailer's music track sits in `promo/trailer/` and is git-ignored (`*.mp3`). `python tools/build_screenshots.py` retakes `05_previewer.png`, `06_wildcard_generator.png` and `10_ai_fixer_gui.png` (maximized, default themes, pointer parked off-window, clipboard restored).

## CoW's palette system — the key technical fact

**Read `docs/cow-palette-format.md` before touching any color code.** From `SpriteCreator::recolorImageWithTable` (vendored at `reference/spritecreator.cpp`):

- A CoW sprite's **red and green channels are 2D coordinates into a 256x256 colortable**: `out = table[src.green, src.red]`. Blue is unused. Transparent stays transparent. No fallthrough, no row-0 restriction. (An old 1D row-0 model was wrong; if you see it anywhere, it is stale.)
- Each table holds **four palettes in quadrants**: UI (0,0), tertiary (129,0), secondary (0,129), primary (129,129), with row/col 128 an empty gutter. Swatches are 3x3 px, in rows by unit class. `reference/colortables/Color_Codes.png` is the legend.
- Neutral materials (treads, glass, wakes) are coordinates that hold a grey/brown in every team table — not an exception to recoloring.
- Tables are used **as-is** from `reference/colortables/` (33 tables incl. `mask`, plus the legend). Nothing is decoded or regenerated.
- 100% of pixels in both shipped CoW sprite sets address a defined cell in every team table.

Stock CoW map units encode on the `unit` band (green 132-134), not the per-class bands `classes.json` targets.

**Cross-team aliases:** 18 of the 93 `unit`-band colors are two coordinates identical in Orange Star and different in 18 other teams. Any palette keyed on one team's RGB silently collapses them, so palette entries are keyed on the full 32-team signature. Known limitation: aliases cannot be resolved from Orange Star art; ties go to the first entry (affects 5 naval sprites plus `infantry+bh`, which still validate everywhere). `test_alias_limitation` pins it.

## The conversion pipeline

```
chroma-key  ->  pixel-snap downscale  ->  quantize  ->  encode  ->  validate
```

- Quantize **after** the downscale, so the modal vote runs on the full color range.
- **Grid detection** (`convert.detect_grid`): 1D edge-energy per axis (color and alpha), comb-filtered over candidate pitches at 0.02 px, then a least-squares boundary fit to kill drift. Cells reduce by modal color on bucketed RGB over the middle half of each cell. Confidence is per-cell agreement with the mode; below 0.55 the file goes to `needs-review/` with intermediates.
- **Quantize against `all` by default** — the 63-color union of the six class palettes. Class is not a color restriction (skin on drivers, water blues on windows). The union is safe because every color shared between classes renders identically in all 32 teams; `test_convert.py` asserts it.
- **AW2 water/glass blues are pinned** in `build_pipeline.py` to CoW's water cells `(145,4)` and `(139,7)`; nearest-RGB had sent them to cells that turn grey, green or gold in other teams.
- **Sheet cutter** (`split.py`): connected components merged within 2 logical px (`--join`), then each piece's scale found exactly from the GCD of its color-change positions; `detect_grid` is only the fallback for non-exact (JPEG/AI) pieces. Verified pixel-identical on the hand-authored sheets in `testdata/`. Running a whole sheet through `batch_convert` locks onto inter-unit spacing — sheets must go through the cutter.
- **Testing without AI art:** `tools/synth.py` round-trips shipped sprites through synthetic upscale-and-damage; the criterion is render identity across all 32 teams.

Still open, AI sheets only: does a generator drift in scale across a sheet (caught by the outlier-pitch gate, not corrected), and do AI units touch (touching units come out as one piece).

## Prompts (revision 4, the user's own format)

Eight independent parts: subject, addon (bold / none / detailed), view (6), layout (single or 2x2-4x4 sheet), background (chroma green/magenta or a `{color}` theme), fixed style suffix, optional outlines, palette request (**keep** / none / described, last in the prompt). All strings live in `pipeline/prompts.json`. See `docs/prompt-templates.md`.

- **Aspect ratio is never set, suggested or mentioned in the prompt** — the canvas ratio (16:9 recommended) is set in the generator's UI; a test checks no prompt contains a ratio.
- Prompts ignore unit class. The palette part defaults to **keep** ("Keep the same color palette and style as original image.", for edit mode); *described* is the user's wording for no-reference runs (drifts unit to unit, cars2.jpg). **The hex-code option was removed**: the model painted the codes on as car paint (cars.jpeg); a test forbids hex codes in any palette option. Worst-case prompt is 936 of the 1000-char budget, so adding prompt text needs a length check.
- Sheets capped at 16 units. The reference is the image being **edited** — the old "attach as style reference, never edit" rule is obsolete.

## Wildcard list format

Plain text, one entry per line, slots are the `.txt` files themselves (add/delete a file to add/remove a slot), ordered by plain basename sort (`a1`, `a2`, `a3`, `b1`...). `#` lines are comments. **Every entry ends in `__`, which is a space placeholder, not a delimiter** (from the user's original JS tool: `sequence.join('').replace(/__/g, ' ')`). Load with `rstrip()`, concatenate the picked entries, then `.replace('__', ' ').strip()`. The trailing `__` is what lets a disabled slot drop out cleanly.

`a3` is the connector (`with a__`, `mounting a__`, `armed with a__`) as an ordinary slot — no hardcoded connector logic. Default enabled slots: `a2`, `a3`, `c3`. `wildcards.config.json` (optional; see the `.example`) overrides default slots, the civilian preset, and list folders.

**The lists are the user's live data and they move.** `test_wildcards.py` asserts entry counts as a floor, not an equality. The one strict invariant: every entry in every list ends in `__`.

## Settled decisions — don't relitigate

- **Dual-purpose:** the encoded `+mask.png` is the master; all teams are a table lookup from it. Author in Orange Star red on flat `#00ff00`.
- **Single idle sprites only**; animation frames are out of scope (the previewer can still step through frames of existing strips).
- Three AWDS sprite types: map tile (16-32 px, in CoW), info chibi (~64 px, **not** in CoW — for the user's own game), battle animation (freeform, in CoW). Battleanim frame counts are not derivable from dimensions.
- Export naming: `sprite_name+palette_name.png`.
- Licensing: shipping CoW's colortables and sample sprites is fine (freeware modding tool, open-source game, the user is an official CoW content contributor). Attribute Commander_Wars, LGPL-3.0. Third-party Aseprite palettes (MaskRef, now in `old wip/`) never ship.
- GUIs: **ttkbootstrap 2.x** (2.2.3). The previewer uses **pydata, dark by default**; the wildcard generator uses **gruvbox, light by default**; the AI fixer uses **everforest, dark by default**; all three have a Light/Dark switch, a Guide button (opens `guide.md`) and a tooltip on nearly every control. Keyboard-first (previewer WASD+QE+RF+T, plus clickable steppers under the canvas), Esc leaves a text field and never quits. A small "Made by Reactorcore" label sits in each GUI's bottom bar and opens https://reactorcoregames.github.io/ (`guikit.credit_label`). The previewer's Convert/Cut dialog asks for the background key (green or magenta) as well as the class. In 2.x, `secondary` is an accent color (purple / orange), not grey: quiet text uses `guikit.MUTED`, and yellow `warning` or cyan `info` text washes out on pydata-light.
- Free and open source under MIT (`LICENSE`, shipped as `LICENSE.txt`; covers our code only, not the CoW or AW2 material). No pricing talk in promo material. Root `*.url` shortcuts ship in the zip.
- **Edit mode is the recommended Route B mode** (see Two routes above).
- **AI sprite fixer** (`aifix.py`) works per sheet, not per unit: key (sampled from the border) → cut by layout (guessed from gutters, user-overridable up to 8x8) → isolate → measure the grain → snap every unit at **one pitch** → quantize (weighted YCbCr + per-sheet usage prior + exact-match lock + **team lock**, on by default) → merge specks (smooth 40) → encode. **Default size is native** (pitch = grain, ~2.84 px on Nano Banana sheets → median unit 79-90 px; no grain → 96 px fallback); the size slider only goes smaller. Per-unit *even out* is off by default (costs accuracy on correct art). Outlines and shadows are deliberately untouched; colour budgets and median pre-filters measured worse — don't retry them. The GUI runs the core on a worker thread and exports `+mask.png` (always 1x), `rgb/` and army PNGs (`armies/`, or `armies Nx/` when upscaled) to `output/aifix/<sheet>/`; a later-rejected unit's old files move to `rejected/`, never deleted.

## Repo layout

```
build_release.bat, cow_suite.spec   Release build (PyInstaller one-dir + zip)
readme.md, guide.md, promo.md       User-facing docs (ship in the release)
.gitignore                          Ignores dist/, build/, output/, old wip/, .claude/, upstream/, *.mp3, trailer renders
tools/                              All code; approot.py, cores, GUIs, builders, test_*.py
wildcards/                          Wildcard lists a1..d3 -- the user's live data (same path in the release)
samples/                            CoW's own mask sprites: cow map units/, cow battle animations/ (same path in the release)
reference/                          Vendored CoW material, used as-is
  colortables/                          33 tables + Color_Codes legend
  spritecreator.cpp                     Upstream recolor algorithm, for reference only
  Commander_Wars-LICENSE-LGPL-3.0.txt
pipeline/                           Conversion + prompt materials (README.md explains them)
  classes.json, prompts.json, palette/, reference/ (class sheets + units/ gallery)
  source/                               Real AW2 sprites, all five armies (build input, not shipped)
testdata/                           Hand-drawn test sheets (6x/4x + their 1x originals); ai sheets/ (real AI sheets)
docs/                               Developer docs: cow-palette-format.md, prompt-templates.md, generator-findings.md (not shipped)
promo/                              icon_og.png, icon_*.ico/_og.png, promo images 01-16, cover, thumbnail
  source/                               Raw screenshots/results behind 11-16 (not shipped)
  trailer/                              Trailer video, clips, keyframes, youtube.md, music (not shipped)
upstream/                           Optional checkout of the CoW repo for convert.frame_counts (git-ignored, usually absent)
output/                             Tool output (git-ignored)
old wip/                            Retired material incl. the old context/ corpus (git-ignored)
```

## Environment and conventions

- Windows 11, Python 3.13.1; ttkbootstrap 2.2.3, Pillow 10.4.0, numpy, PyInstaller 6.19.
- **Not a git repo — do not `git init`.** The user will move it to `C:\14 Repos\` and publish it open source with GitHub Desktop; `.gitignore` is ready for that.
- Deletes are blocked (a hook catches the text `del`/`rm` even inside scripts): move unwanted files to `C:\trash\` or `old wip/` instead.
- GUI work can be checked visually: launch the window maximized and topmost, then `PIL.ImageGrab.grab()` after a short delay and read the PNG (see `tools/build_screenshots.py`). Hover behavior and real use still need the user. A frozen-exe smoke test: launch, wait, read `MainWindowTitle` (a crash shows PyInstaller's "Unhandled exception" dialog instead).
- Tests: run each `python tools/test_*.py`; all must pass. They need `samples/`, `testdata/` and `pipeline/`.
- Markdown prose is **never hard-wrapped** — one line per paragraph.
- `wildcards/` is the user's live data; `samples/` and `testdata/` are fixtures, not to be edited. `reference/` and `pipeline/` are generated or vendored; regenerate rather than hand-edit. Never paste upstream CoW code into our sources.
- Palette correctness is verifiable: round-trip against `samples/` rather than eyeballing.
