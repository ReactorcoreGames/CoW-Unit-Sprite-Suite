# Promo — CoW Unit Sprite Suite

Free, open-source (MIT) modding tool, so no business model section. The aim is findability: put it where Commander Wars modders and Advance Wars fans already look, and let it sit there.

## Strategy

- **Lead with the workflow, not the novelty.** The hook is "draw in normal Orange Star red, the suite handles CoW's bizarre mask format". Drawing directly in the mask palette is what puts people off making units; the suite removes that step. The 32-army preview is a nice extra for showing units off to communities, so use `promo/01_one_sprite_32_armies.png` as eye candy, not as the pitch.
- **Link the game's own site first.** https://commander-wars.pages.dev/ introduces Commander Wars properly; the GitHub repo is bare for newcomers, so it goes second or into credits.
- **The audience is small and specific.** Commander Wars modders, plus Advance Wars fans who want to make units for their own fan games. Go where they already are rather than broad gamedev channels.
- **ModDB/IndieDB first.** Commander Wars' mods live on ModDB, and you already publish CoW content there. A tool listed under the game's addons sits right next to the mods people browse, permanently.
- **Commander Wars Discord second.** The game's community Discord is where many CoW mods are made. One post in its modding channel with the 32-armies image reaches exactly the people who'd use this. Ask the maintainer (Robosturm) before posting if the server has a rule about it.
- **itch.io as the download home.** Tag it as a tool; it'll be found by people searching "pixel art", "sprite", "advance wars".
- **Be upfront about what the AI route is.** Modding communities are often wary of AI art. Lead with the hand-drawn route (it's the primary use anyway) and present the AI Sprite Fixer as a way to turn AI concept sheets into a starting point for hand-fixing, at battle scale only. `09_ai_fixer.jpg` shows exactly that: the raw JPEG, the fixed sprite, four armies. That framing is honest and avoids a pile-on.
- **Show a real result.** When you have a custom unit made with the suite in an actual CoW match, a screenshot of it on the map is worth more than every other image here.

## Images

All in `promo/`:

- `01`–`04` — composed showcase images (one sprite in 32 armies, the pipeline, the reference gallery, CoW's own units).
- `05_previewer.png` — the previewer, full screen, in its default dark theme: the megatank in Blue Moon on the dusk background.
- `06_wildcard_generator.png` — the wildcard generator, full screen, in its default light theme, with a rolled subject and the prompt row filled in.
- `07_wildcard_ideas.png` — a cloud of 123 vehicle ideas rolled by the generator from its default slots. Good for posts about the AI side, or just for fun.
- `08_sheet_cutter.png` — a hand-drawn 4x sheet of 20 units with the cutter's numbered index boxes, next to the 20 sprites it cut, each converted and shown in a different army.
- `09_ai_fixer.jpg` — before and after: one unit from a real Nano Banana 2 Lite sheet as generated (JPEG, on green), the fixer's sprite at its native size, and that sprite in four armies. Built from real fixer output by `build_promo.py`.
- `10_ai_fixer_gui.png` — the AI Sprite Fixer, full screen, in its default dark theme: a 4x4 Nano Banana sheet with its cut lines, and the 16 results in Blue Moon.
- `11_edit_mode_setup` — annotated Venice.ai Edit Image screen: what to set on the generator (edit mode, the shipped reference sheet as the image to edit, 16:9 canvas, the copied prompt, Nano Banana 2 Lite). The how-to image for the AI route.
- `12_edit_mode_result` — annotated prompt and result: one edit, 16 SUV variants in the reference's colors.
- `13_sheet_to_cow_files` — step 3: a generator JPEG beside the 16 `+mask.png` sprites the fixer made from it, with a line explaining the mask palette.
- `14_every_sheet` — three more sheets through the same route, each captioned with what it shows (wrong layout corrected, edit mode on palette, described colors drifting).
- `15_color_cleanup` — six units through nearest-color, the fixer's quantizer and Even out, then in three armies.
- `16_even_out` — numbered walk-through of one peach SUV pulled back to team red, then shown in three armies.

11–16 are built by `build_promo.py` from the screenshots and results in `promo/source/` (source repository only), with titles, numbered badges and captions added. For an AI-route post, 11 → 12 → 13 (or 09) reads as a three-step how-to.
- `cover_1280x720`, `thumbnail_630x500` — itch.io page art.

The three programs have different icons (the same heavy tank in Blue Moon blue, Yellow Comet yellow and Green Earth green) and different themes, so they are easy to tell apart in screenshots and in the taskbar.

---

## itch.io

**Name:**

CoW Unit Sprite Suite

**Subtitle (≤100 chars):**

Draw new units for Commander Wars mods easier than ever before!

**Long description:**

```html
<h1 id="cow-unit-sprite-suite">CoW Unit Sprite Suite</h1>
<p><strong>Draw your units in plain Orange Star red. Let the suite handle the mask format.</strong></p>
<p>A free workflow toolkit for making new unit sprites for <a href="https://commander-wars.pages.dev/">Commander Wars</a>, the open-source Advance Wars-style strategy game.</p>
<h2>Why</h2>
<p>Commander Wars stores unit sprites in a special mask format, nicknamed the "ugly palette": washed-out greens and browns whose red and green channels are really just coordinates into a color table - not something you look at:&nbsp;</p>
<p><img src="https://img.itch.zone/aW1nLzMwOTMxNTQxLnBuZw==/original/15eZpf.png"><br></p>
<p>Although this method and technology has the great benefit of&nbsp;how one file serves every army with just a palette swap... but&nbsp;drawing in that format directly feels bizarre and awkward.&nbsp;</p>
<p>With this suite you draw in normal Orange Star colors, like the original games, and convert to the mask whenever you need the game file.&nbsp;</p>
<p>Existing sprites already&nbsp;in the ugly palette can be viewed afterwards in any team palette and exported to any team color you want!</p>
<h2 id="what-s-inside">What's inside</h2>
<ul>
<li><strong>Sprite converter</strong> &mdash; the heart of the suite. Draw in Orange Star red on a flat green background, one unit per image or a whole sheet of them. The suite cuts units apart, finds each one's true pixel scale, snaps colors to the game's palette and writes the finished <code>+mask.png</code> the game reads.<br><img src="https://img.itch.zone/aW1nLzMwOTMwNTIzLnBuZw==/original/21dl1S.png"><br><br></li>
<li><strong>Previewer</strong> &mdash; check any mask sprite in proper team colors on four time-of-day backgrounds. As a bonus it can show and export units in any of CoW's 32 armies, handy for showing your work off to the community.<br><img src="https://img.itch.zone/aW1nLzMwOTMwNTU2LnBuZw==/original/eFRRSH.png"><br><br></li>
<li><strong>91 reference units</strong> &mdash; every Advance Wars 2 unit from all five armies, recolored to Orange Star red, plus exact per-class palettes to draw with.<br><img src="https://img.itch.zone/aW1nLzMwOTMxNjE4LnBuZw==/original/K1ScsS.png"><br><br></li>
<li><strong>Wildcard Vehicle Generator</strong> rolls random vehicle ideas and builds prompts for an image generator's edit mode:<br><img src="https://img.itch.zone/aW1nLzMwOTMyNTI2LnBuZw==/original/3qPEFr.png"><br><br></li><li><strong>AI Sprite Fixer</strong> turns the sheet that comes back into usable CoW sprites: cut apart, snapped to a pixel grid, on the game's palette, in all 32 armies, and exportable as upscaled PNGs for other games:<br><img src="https://img.itch.zone/aW1hZ2UvNTE2NTM3NS8zMDkyOTIxMi5qcGc=/original/LBf84F.jpg"></li><br>
</ul>
<h3>Not limited to just for Commander Wars!</h3>
<p>You can use the CoW Unit Sprite Suite to make Advance Wars style sprites for any game or mod project&nbsp;with up to 32 different team colors.</p>
<p>In the <strong>Wildcard Generator 🎲</strong>&nbsp;you can set the prompts to ask for side-view, front-view, top-view or isometric/oblique camera angles.</p>
<p>In the <strong>AI Sprite Fixer 🔧</strong> you can turn sheets into individual sprites.</p>
<p>In the <strong>Previewer 🔍</strong> you can take those individual sprites and convert them to whichever team colors you need them to be.&nbsp;</p>
<p>This workflow is very versatile and easy - allowing you to use it anywhere it fits!</p>
<h2 id="how-to-use">How to use</h2>
<p>(Below is the "quickstart" intended for making new units for CoW mods.)</p>
<ol>
<li>Unzip, run <strong>CoW Sprite Previewer.exe</strong>.</li>
<li>Draw your unit in red on #00ff00 green.</li>
<li>Click <strong>Cut sheet&hellip;</strong> and pick your drawing.</li>
<li>Your <code>+mask.png</code> files are ready for Commander Wars.</li>
</ol>
<p>More information is available in Guide.md.</p>
<p>Built for Windows 10 or newer, no installing needed. Standalone, portable, offline.&nbsp;</p>
<h3>AI Sprite Route</h3>
<p>If you wanna do the AI Route or your sheet is not exactly in the correct palette, check out <strong>CoW Wildcard Generator.exe</strong> and then <strong>CoW AI Sprite Fixer.exe afterwards</strong>.</p>
<p>I recommend using Nano Banana 2 Lite or similar capable model. They're usually paid - and somewhat pricy - and not perfect/finicky - so your mileage may vary. Weaker models usually struggle worse.</p>
<p><img src="https://img.itch.zone/aW1hZ2UvNTE2NTM3NS8zMDkyOTI5NS5qcGc=/original/t7w%2B8v.jpg"></p>
<p><img src="https://img.itch.zone/aW1hZ2UvNTE2NTM3NS8zMDkyOTI5Ni5qcGc=/original/aM5aIj.jpg"></p>
<p><img src="https://img.itch.zone/aW1hZ2UvNTE2NTM3NS8zMDkyOTI5NC5qcGc=/original/fYXyD1.jpg"></p>
<p><img src="https://img.itch.zone/aW1hZ2UvNTE2NTM3NS8zMDkyOTI5Ny5wbmc=/original/kHgAyQ.png"><br></p>
<p>I used <a href="https://venice.ai/chat?ref=ajFOrf" target="_blank">Venice.ai</a> as the provider/interface/service, but if you use another platform that has access to a good model, then by all means use that.</p>
<p>In my testing I found the best method to edit an existing reference image (included in the suite) and aim for a 16:9 aspect ratio image, using the prompts from the <strong>CoW Wildcard Generator.exe</strong>.<br></p>
<h2 id="credits">Credits</h2>
<p>Commander Wars colortables and sample sprites are from Commander Wars by Robosturm and contributors (LGPL-3.0, source: <a href="https://github.com/Robosturm/Commander_Wars">https://github.com/Robosturm/Commander_Wars</a>), included unmodified.</p>
<p>Made by Reactorcore &mdash; <a href="https://linktr.ee/reactorcore">https://linktr.ee/reactorcore</a></p>
```

**Tags:**

commander-wars, advance-wars, pixel-art, sprites, modding, tool, palette, game-assets, turn-based-strategy, free

---

## GitHub

**Description:**

Workflow tools for making Commander Wars unit sprites: draw in Orange Star red (or fix up an AI sheet), convert to CoW's mask format, preview in team colors.

**Topics:**

commander-wars
advance-wars
pixel-art
sprite-tools
modding
palette
python
ttkbootstrap
pillow
game-assets

---

## YouTube

**Title:**

CoW Unit Sprite Suite — make Commander Wars units without drawing in the mask palette (free tool)

**Description:**

```
Draw Commander Wars units in plain Orange Star red, and let the suite turn them into the game's mask format.

CoW Unit Sprite Suite is a free Windows workflow toolkit for Commander Wars modders: it converts your pixel art into the "ugly" mask palette the game reads, cuts whole sheets of units apart automatically, and previews the results in team colors (all 32 armies, if you want to show them off).

Download: https://reactorcore.itch.io/
Commander Wars: https://commander-wars.pages.dev/

Contact: reactorcoregames@gmail.com

Check out everything else I do: ✨🚀
https://linktr.ee/reactorcore
https://reactorcore.itch.io/

-Reactorcore

My other links:
Home/Links: https://linktr.ee/reactorcore
Releases: https://reactorcore.itch.io
Blog: https://www.patreon.com/ReactorcoreGames
Discord: https://discord.gg/UdRavGhj47
Catalog: https://reactorcoregames.github.io/
```

**Tags:**

commander wars, advance wars, pixel art, sprite, modding, mod tools, game modding, palette swap, turn based strategy, advance wars fan game

---

## Marketing targets

1. **ModDB / IndieDB — Commander Wars page** (moddb.com/games/commander-wars). The game's mods are listed here and you already publish to it. List the suite as an addon/tool so it sits beside the mods permanently.
2. **Commander Wars community Discord** — linked from the game's ModDB and GitHub pages. Many CoW mods are built there; a single post in the modding channel reaches the exact audience.
3. **Commander Wars website and GitHub** (commander-wars.pages.dev, github.com/Robosturm/Commander_Wars) — if the wiki or discussions have a modding-resources section, ask whether a link can go there. Persistent, and seen by every new modder.
4. **Wars Central / Wars Wiki** (warscentral.com) — the long-running Advance Wars fan hub with forums; a post in its fan-game/creative section suits the "make your own AW units" angle.
5. **Advance Wars By Web (AWBW) forums** — the biggest active AW community. Less about modding, but its members are the core AW audience and some make custom art.
