"""Builds the promo/ images from real sprite output. Rerun after art changes.

    python tools/build_promo.py

Everything here is composed from the suite's own pipeline -- a reference unit
is cut, converted to a CoW mask and recolored through the shipped colortables
-- so the pictures show what the tools actually produce. GUI screenshots are
not made here; those have to be captured by hand.

Writes, into promo/:
    01_one_sprite_32_armies   one converted unit in all 32 team colors
    02_pipeline               red-on-green -> encoded mask -> any army
    03_reference_gallery      the 91-unit AW2 reference gallery
    04_cow_map_units          CoW's own map units across many teams
    08_sheet_cutter           a hand-drawn 4x sheet cut into 1x sprites, any army
    09_ai_fixer               an AI sheet's unit -> the fixer's sprite -> 4 armies
    11-16                     annotated outside material from promo/source/:
                              the Venice edit setup and result,
                              sheet -> CoW masks, color cleanup and even out
    thumbnail_630x500         itch.io thumbnail
    cover_1280x720            itch.io cover
    icon_og                   the red hero at 1024px (cover / thumbnail art)
    icon_previewer            Blue Moon .ico + _og.png for the previewer exe
    icon_wildcards            Yellow Comet .ico + _og.png for the generator exe
    icon_aifix                Green Earth .ico + _og.png for the AI sprite fixer exe
Each is saved as PNG or JPG, whichever is smaller.
"""

from __future__ import annotations

import io
import pathlib
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cowpalette as cp  # noqa: E402
import split  # noqa: E402
from approot import ROOT  # noqa: E402

PROMO = ROOT / "promo"
UNITS_1X = ROOT / "pipeline" / "reference" / "units" / "1x"
UNITS_6X = ROOT / "pipeline" / "reference" / "units"
HERO = UNITS_1X / "Tank Heavy - Blue Moon.png"
COW_MAP = ROOT / "samples" / "cow map units"
SHEET = ROOT / "testdata" / "AW2Style Units 4x green safety BG.png"
# A real Nano Banana 2 Lite sheet (the user's own generation); 09 is skipped
# without it. Only generator *output* goes in promo -- not the reference image
# that was fed in.
AI_SHEET = ROOT / "testdata" / "ai sheets" / "Nanobanana Attempts (3).jpg"
AI_UNIT = 7

FONT_TITLE = "impact.ttf"
FONT_BODY = "bahnschrift.ttf"
FONT_BOLD = "segoeuib.ttf"

PAL = cp.load_palettes()
TEAMS = [t for t in PAL.team_names() if t != cp.MASK]  # the 32 armies


# ----------------------------------------------------------------- helpers

def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(name, size)


def shadow_text(img: Image.Image, xy, text: str, f, fill=(255, 255, 255),
                anchor: str = "la", shadow: int = 6, stroke: int = 0) -> None:
    """Text with a soft, heavy drop shadow so it reads over busy art."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((xy[0] + shadow // 2, xy[1] + shadow // 2), text, font=f, fill=(0, 0, 0, 255),
           anchor=anchor, stroke_width=stroke + shadow // 2, stroke_fill=(0, 0, 0, 255))
    layer = layer.filter(ImageFilter.GaussianBlur(shadow))
    img.alpha_composite(layer)
    img.alpha_composite(layer)
    ImageDraw.Draw(img).text(xy, text, font=f, fill=fill, anchor=anchor,
                             stroke_width=stroke, stroke_fill=(0, 0, 0))


def key_green(im: Image.Image) -> Image.Image:
    a = np.array(im.convert("RGBA"))
    a[(a[..., 0] == 0) & (a[..., 1] == 255) & (a[..., 2] == 0)] = 0
    return Image.fromarray(a, "RGBA")


def scale(im: Image.Image, k: int) -> Image.Image:
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def convert_hero() -> Image.Image:
    """The hero unit through the real cutter + converter: its CoW mask."""
    res = split.split_sheet(Image.open(HERO), name="hero")
    piece = max(res.pieces, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1]))
    assert piece.result is not None and piece.result.ok, piece.reason
    return piece.result.encoded


def save_smallest(img: Image.Image, stem: str) -> pathlib.Path:
    rgb = img.convert("RGB")
    png, jpg = io.BytesIO(), io.BytesIO()
    rgb.save(png, "PNG", optimize=True)
    rgb.save(jpg, "JPEG", quality=90, optimize=True, subsampling=0)
    ext, data = (".png", png) if png.tell() <= jpg.tell() else (".jpg", jpg)
    for old in (".png", ".jpg"):          # leave no stale twin behind
        stale = PROMO / f"{stem}{old}"
        if old != ext and stale.exists():
            stale.rename(PROMO / f"{stem}{old}.stale")
    out = PROMO / f"{stem}{ext}"
    out.write_bytes(data.getvalue())
    return out


def pretty(team: str) -> str:
    return team.replace("_", " ").title()


# ------------------------------------------------------------------ images

def one_sprite_32_armies(mask: Image.Image) -> Image.Image:
    W, H = 1280, 720
    img = cp.make_background("night", (W, H))
    cols, rows, k = 8, 4, 2
    tw, th = 148, 141
    gx, gy = (W - cols * tw) // 2, 154
    small = font(FONT_BODY, 14)
    for i, team in enumerate(TEAMS[:32]):
        x, y = gx + (i % cols) * tw, gy + (i // cols) * th
        tile = cp.make_background("day", (tw - 8, th - 4))
        spr = scale(cp.recolor(mask, team, PAL), k)
        tile.alpha_composite(spr, ((tile.width - spr.width) // 2, 1))
        img.alpha_composite(tile, (x + 4, y + 2))
        ImageDraw.Draw(img).text((x + tw // 2, y + th - 4), pretty(team), font=small,
                                 fill=(20, 20, 30), anchor="md")
    shadow_text(img, (W // 2, 26), "ONE SPRITE  →  32 ARMIES", font(FONT_TITLE, 80), anchor="ma")
    return img


def pipeline(mask: Image.Image) -> Image.Image:
    W, H = 1280, 720
    img = cp.make_background("dusk", (W, H))
    d = ImageDraw.Draw(img)
    k = 4
    panels = [
        ("1. DRAW", "Orange Star red\non flat green", Image.open(HERO).convert("RGBA")),
        ("2. CONVERT", "CoW's index-encoded\nmask = the master", mask),
    ]
    px = 60
    for title, sub, im in panels:
        spr = scale(im, k)
        card = Image.new("RGBA", (spr.width + 40, spr.height + 40), (255, 255, 255, 235))
        card.alpha_composite(spr, (20, 20))
        img.alpha_composite(card, (px, 230))
        shadow_text(img, (px + card.width // 2, 120), title, font(FONT_TITLE, 64), anchor="ma")
        d.multiline_text((px + card.width // 2, 230 + card.height + 18), sub,
                         font=font(FONT_BOLD, 26), fill=(255, 255, 255), anchor="ma", align="center",
                         stroke_width=3, stroke_fill=(0, 0, 0))
        px += card.width + 90
        shadow_text(img, (px - 62, 360), "→", font(FONT_TITLE, 90), anchor="mm")
    # 3. any army: a 2x3 stack of teams
    shadow_text(img, (px + 230, 120), "3. ANY ARMY", font(FONT_TITLE, 64), anchor="ma")
    picks = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole", "pink_frontier"]
    for i, t in enumerate(picks):
        spr = scale(cp.recolor(mask, t, PAL), 2)
        x, y = px + (i % 3) * 156, 220 + (i // 3) * 170
        tile = cp.make_background("day", (148, 160))
        tile.alpha_composite(spr, ((148 - spr.width) // 2, (160 - spr.height) // 2))
        img.alpha_composite(tile, (x, y))
    d.text((px + 230, 580), "via CoW's own colortables", font=font(FONT_BOLD, 26),
           fill=(255, 255, 255), anchor="ma", stroke_width=3, stroke_fill=(0, 0, 0))
    return img


def reference_gallery() -> Image.Image:
    W, H = 1280, 720
    img = cp.make_background("day", (W, H))
    files = sorted(p for p in UNITS_1X.glob("*.png"))
    cols, rows = 13, 7
    tw, th = W // cols, (H - 110) // rows
    for i, p in enumerate(files[: cols * rows]):
        spr = key_green(Image.open(p))
        k = max(1, min(tw // spr.width, th // spr.height))
        spr = scale(spr, k)
        x, y = (i % cols) * tw, 110 + (i // cols) * th
        img.alpha_composite(spr, (x + (tw - spr.width) // 2, y + (th - spr.height) // 2))
    shadow_text(img, (W // 2, 10), f"{len(files)} REFERENCE UNITS, ALL IN RED", font(FONT_TITLE, 78), anchor="ma")
    return img


def cow_map_units() -> Image.Image:
    W, H = 1280, 720
    img = cp.make_background("dawn", (W, H))
    files = sorted(COW_MAP.glob("*.png"))
    cols = 11
    rows = -(-len(files) // cols)
    tw, th = W // cols, (H - 120) // rows
    for i, p in enumerate(files):
        im = Image.open(p).convert("RGBA")
        n = max(1, im.width // im.height)
        frame = cp.split_frames(im, n)[0]
        spr = cp.recolor(frame, TEAMS[i % len(TEAMS)], PAL)
        k = max(1, min((tw - 10) // spr.width, (th - 10) // spr.height))
        spr = scale(spr, k)
        x, y = (i % cols) * tw, 120 + (i // cols) * th
        img.alpha_composite(spr, (x + (tw - spr.width) // 2, y + (th - spr.height) // 2))
    shadow_text(img, (W // 2, 14), "PREVIEW ANY SPRITE, ANY TEAM", font(FONT_TITLE, 82), anchor="ma")
    return img

# Twenty armies that read as twenty different colors at a glance.
CUTTER_TEAMS = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole",
                "pink_frontier", "teal_isle", "purple", "brown_desert", "cyan",
                "cherrystone", "gloomwood", "cobalt_ice", "amber_corona", "aurania",
                "golden_sun", "cacophony", "silver", "fumomance", "requiem"]


def sheet_cutter() -> Image.Image:
    """The real cutter on a hand-drawn sheet: the sheet with its numbered
    index boxes on the left, every piece at 1x in a different army on the right."""
    W, H = 1280, 720
    img = cp.make_background("dusk", (W, H))
    sheet = Image.open(SHEET).convert("RGBA")
    res = split.split_sheet(sheet, name="sheet")
    assert all(p.result and p.result.ok for p in res.pieces), [p.reason for p in res.pieces]

    # Left: the sheet, shrunk, with the index boxes drawn crisp at panel scale.
    side, sx, sy = 560, 40, 128          # centred on the 106..711 grid
    k = side / sheet.width
    panel = sheet.resize((side, round(sheet.height * k)), Image.BOX)
    img.alpha_composite(Image.new("RGBA", (side + 12, panel.height + 12), (0, 0, 0, 255)), (sx - 6, sy - 6))
    img.alpha_composite(panel, (sx, sy))
    d = ImageDraw.Draw(img)
    badge = font(FONT_BOLD, 13)
    for p in res.pieces:
        x0, y0, x1, y1 = (round(v * k) for v in p.box)
        x0, y0, x1, y1 = sx + x0 - 3, sy + y0 - 3, sx + x1 + 2, sy + y1 + 2
        d.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=3)
        d.rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], outline=(255, 214, 40), width=1)
        label = f"{p.index:02d}"
        tw = d.textlength(label, font=badge)
        d.rectangle([x0, y0 - 16, x0 + tw + 8, y0], fill=(0, 0, 0))
        d.text((x0 + 4, y0 - 15), label, font=badge, fill=(255, 214, 40))

    shadow_text(img, (sx + side + 46, sy + side // 2), "→", font(FONT_TITLE, 90), anchor="mm")

    # Right: the pieces at 2x, laid out as they sit on the sheet (rows of 4),
    # each recolored from its converted mask into a different army.
    gx, gy, tw_, th_, gap = 700, 106, 140, 118, 3
    for i, p in enumerate(res.pieces):
        x, y = gx + (i % 4) * (tw_ + gap), gy + (i // 4) * (th_ + gap)
        tile = cp.make_background("day", (tw_, th_))
        spr = scale(cp.recolor(p.result.encoded, CUTTER_TEAMS[i % len(CUTTER_TEAMS)], PAL), 2)
        tile.alpha_composite(spr, ((tw_ - spr.width) // 2, th_ - spr.height - 1))
        img.alpha_composite(tile, (x, y))
        d.text((x + 4, y + 2), f"{p.index:02d}", font=badge, fill=(20, 20, 30))

    shadow_text(img, (W // 2, 8), "ONE SHEET  →  20 SPRITES", font(FONT_TITLE, 82), anchor="ma")
    return img


AI_ARMIES = ["blue_moon", "green_earth", "yellow_comet", "black_hole"]


def ai_fixer() -> Image.Image:
    """One unit of a real AI sheet through the fixer at its defaults: the
    JPEG crop as generated, the snapped sprite, and four armies."""
    import aifix

    W, H = 1280, 720
    img = cp.make_background("dusk", (W, H))
    d = ImageDraw.Draw(img)
    with Image.open(AI_SHEET) as im:
        raw = im.convert("RGB")
    sheet = aifix.fix_sheet(raw, name="ai")
    u = sheet.units[AI_UNIT - 1]
    assert u.result is not None and u.result.ok, u.result and u.result.reason

    # before: the unit as generated, cropped from the sheet at 1:1, and after
    # at 3x -- about the same size on screen, since the grain is ~2.84 px
    ys, xs = np.nonzero(u.isolated[..., 3] > 0)
    m = 10
    before = raw.crop((u.box[0] + xs.min() - m, u.box[1] + ys.min() - m,
                       u.box[0] + xs.max() + m, u.box[1] + ys.max() + m)).convert("RGBA")
    after = scale(u.result.quantized, 3)
    ty, top = 112, 236

    def card(im: Image.Image, x: int, title: str, sub: str, bg=None) -> int:
        c = Image.new("RGBA", (im.width + 32, im.height + 32), bg or (255, 255, 255, 235))
        c.alpha_composite(im, (16, 16))
        img.alpha_composite(c, (x, top))
        shadow_text(img, (x + c.width // 2, ty), title, font(FONT_TITLE, 58), anchor="ma")
        d.multiline_text((x + c.width // 2, top + c.height + 18), sub, font=font(FONT_BOLD, 24),
                         fill=(255, 255, 255), anchor="ma", align="center",
                         stroke_width=3, stroke_fill=(0, 0, 0))
        return x + c.width

    x = card(before, 40, "GENERATED", "Nano Banana 2 Lite, JPEG:\nno pixel grid, smeared colors")
    shadow_text(img, (x + 42, top + 100), "→", font(FONT_TITLE, 80), anchor="mm")
    x = card(after, x + 84, "FIXED", f"snapped to {u.size[0]}x{u.size[1]} px,\n"
                                     "CoW's palette, one master")
    shadow_text(img, (x + 42, top + 100), "→", font(FONT_TITLE, 80), anchor="mm")

    # any army: a 2x2 of recolors at 2x
    gx = x + 84
    tw, th = (W - 40 - gx - 8) // 2, 150
    shadow_text(img, (gx + tw + 4, ty), "ANY ARMY", font(FONT_TITLE, 58), anchor="ma")
    for i, t in enumerate(AI_ARMIES):
        spr = scale(cp.recolor(u.result.encoded, t, PAL), 2)
        tile = cp.make_background("day", (tw, th))
        tile.alpha_composite(spr, ((tw - spr.width) // 2, (th - spr.height) // 2))
        img.alpha_composite(tile, (gx + (i % 2) * (tw + 8), top - 6 + (i // 2) * (th + 8)))
    d.text((gx + tw + 4, top - 6 + 2 * (th + 8) + 12), "via CoW's own colortables",
           font=font(FONT_BOLD, 24), fill=(255, 255, 255), anchor="ma",
           stroke_width=3, stroke_fill=(0, 0, 0))
    shadow_text(img, (W // 2, 14), "AI SHEET  →  CoW SPRITE", font(FONT_TITLE, 82), anchor="ma")
    shadow_text(img, (W // 2, H - 64), "AI Sprite Fixer: cut, snap, recolor -- "
                "a starting point for hand-fixing", font(FONT_BOLD, 28), anchor="ma", stroke=1)
    return img


# --------------------------------------------- annotated outside material
#
# Screenshots and results the user collected in promo/source/ (the Venice.ai edit workflow, the fixer's mask output, the color
# cleanup comparisons), each framed with a title, numbered badges on the
# picture and a legend saying what each number shows. Skipped when the folder
# is absent. The badge positions are in the source image's own pixels.

NEW = ROOT / "promo" / "source"
BADGE = (255, 196, 60)
INK = (240, 240, 245)


def badge(img: Image.Image, xy, n: int, r: int = 22) -> None:
    """A numbered yellow disc with a dark ring, readable on any background."""
    d = ImageDraw.Draw(img)
    x, y = xy
    d.ellipse((x - r - 4, y - r - 4, x + r + 4, y + r + 4), fill=(0, 0, 0))
    d.ellipse((x - r, y - r, x + r, y + r), fill=BADGE)
    d.text((x, y + 1), str(n), font=font(FONT_TITLE, int(r * 1.5)), fill=(0, 0, 0), anchor="mm")


def legend(img: Image.Image, x: int, y: int, width: int, items: list[str],
           size: int = 26, gap: int = 22) -> int:
    """Numbered notes, word-wrapped to `width`. Returns the y below the last."""
    d = ImageDraw.Draw(img)
    f = font(FONT_BOLD, size)
    for n, text in enumerate(items, 1):
        badge(img, (x + 22, y + 22), n, r=18)
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if d.textlength(trial, font=f) > width - 64 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        d.multiline_text((x + 60, y), "\n".join(lines), font=f, fill=INK, spacing=6)
        y += len(lines) * (size + 6) + gap
    return y


def blurb(img: Image.Image, y: int, text: str, size: int = 28) -> None:
    W = img.width
    d = ImageDraw.Draw(img)
    f = font(FONT_BOLD, size)
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if d.textlength(trial, font=f) > W - 160 and line:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    d.multiline_text((W // 2, y), "\n".join(lines), font=f, fill=INK, anchor="ma",
                     align="center", spacing=8, stroke_width=2, stroke_fill=(0, 0, 0))


def framed(src: Image.Image, scale_to: tuple[int, int]) -> tuple[Image.Image, float]:
    """`src` fitted into `scale_to` (never enlarged past 1x) with a dark border."""
    k = min(1.0, scale_to[0] / src.width, scale_to[1] / src.height)
    im = src.convert("RGBA").resize((round(src.width * k), round(src.height * k)), Image.LANCZOS)
    out = Image.new("RGBA", (im.width + 12, im.height + 12), (0, 0, 0, 255))
    out.alpha_composite(im, (6, 6))
    return out, k


def canvas(title: str, sub: str = "") -> Image.Image:
    img = cp.make_background("night", (1920, 1080))
    img.alpha_composite(Image.new("RGBA", img.size, (10, 12, 30, 120)))
    shadow_text(img, (960, 22), title, font(FONT_TITLE, 84), anchor="ma")
    if sub:
        shadow_text(img, (960, 122), sub, font(FONT_BOLD, 30), fill=(180, 220, 255),
                    anchor="ma", shadow=4, stroke=1)
    return img


def screenshot_with_legend(src: pathlib.Path, title: str, sub: str,
                           badges: list[tuple[int, int]], notes: list[str],
                           foot: str, box: tuple[int, int]) -> Image.Image:
    """The screenshot on the left with badges on it, the legend on the right."""
    img = canvas(title, sub)
    shot, k = framed(Image.open(src), box)
    sx, sy = 40, 180
    for n, (bx, by) in enumerate(badges, 1):
        badge(shot, (6 + bx * k, 6 + by * k), n)
    img.alpha_composite(shot, (sx, sy))
    lx = sx + shot.width + 40
    legend(img, lx, sy + 10, 1920 - lx - 40, notes)
    blurb(img, 1080 - 92, foot)
    return img


def edit_mode_setup() -> Image.Image:
    return screenshot_with_legend(
        NEW / "venice ai edit image workflow (2).png",
        "STEP 1  ·  GENERATE IN EDIT MODE", "What to set on the image generator (Venice.ai shown)",
        badges=[(205, 69), (1615, 180), (1440, 452), (1830, 300), (1488, 545)],
        notes=["Use Edit Image, not text-to-image. Venice's \"use reference image\" "
               "quietly switches to a weaker model (Krea) — skip it.",
               "The image to edit: the hand-drawn sheet that ships with the suite, "
               "pipeline/reference/AW2Style Units 4x green safety BG.png.",
               "Canvas 16:9 (Cinema). Side views come back more often than on a square.",
               "Paste the prompt from the Wildcard Generator's Copy full prompt. It ends "
               "with \"Keep the same color palette and style as original image.\"",
               "Model: Nano Banana 2 Lite."],
        foot="What worked in October 2026. Any service that offers Nano Banana 2 Lite "
             "(or better) in edit mode works the same way.",
        box=(1180, 760))


def edit_mode_result() -> Image.Image:
    return screenshot_with_legend(
        NEW / "venice ai edit image workflow (1)_img.jpg",
        "STEP 2  ·  WHAT COMES BACK", "One prompt, one edit, sixteen variants",
        badges=[(1060, 40), (440, 377), (680, 430)],
        notes=["The prompt: a subject rolled by the Wildcard Generator, slightly elevated "
               "side view, a 4x4 sheet on chroma green, and the keep-colors clause last.",
               "\"Edited\": Nano Banana 2 Lite editing the reference sheet, which is what "
               "keeps it on Orange Star's reds and the Advance Wars look.",
               "Sixteen distinct SUVs, side on, in the reference's colors. It is a JPEG with "
               "no real pixel grid — fixing that is the AI Sprite Fixer's job (step 3)."],
        foot="Without the reference loaded, the model picks its own colors and drifts "
             "toward isometric views.",
        box=(1000, 770))


def sheet_to_mask() -> Image.Image:
    img = canvas("STEP 3  ·  SHEET IN, CoW FILES OUT",
                 "The AI Sprite Fixer: cut, snap to pixels, snap to the palette, encode")
    raw, _ = framed(Image.open(NEW / "Nanobanana Attempts (3).jpg"), (860, 640))
    mask, _ = framed(Image.open(NEW / "Nanobanana_Attempts_(3).png"), (860, 640))
    y = 230
    img.alpha_composite(raw, (40, y))
    img.alpha_composite(mask, (1920 - 40 - mask.width, y))
    shadow_text(img, (960, y + raw.height // 2), "→", font(FONT_TITLE, 110), anchor="mm")
    d = ImageDraw.Draw(img)
    f = font(FONT_BOLD, 30)
    d.text((40 + raw.width // 2, y - 16), "the generator's JPEG", font=f, fill=INK, anchor="md",
           stroke_width=2, stroke_fill=(0, 0, 0))
    d.text((1920 - 40 - mask.width // 2, y - 16), "16 sprites as Commander Wars stores them",
           font=f, fill=INK, anchor="md", stroke_width=2, stroke_fill=(0, 0, 0))
    blurb(img, y + max(raw.height, mask.height) + 40,
          "Right: the \"ugly\" mask palette. Its red and green are coordinates into each "
          "army's color table, so one +mask.png per unit serves all 32 armies. You never "
          "draw in it — the fixer writes it, and the previewer shows it in real colors.")
    return img


def more_sheets() -> Image.Image:
    img = canvas("EVERY SHEET, SAME ROUTE", "Generator JPEG on the left, the fixer's CoW masks on the right")
    rows = [(ROOT / "testdata" / "ai sheets" / "Nanobanana Attempts (1).jpg",
             NEW / "Nanobanana_Attempts_(1).png",
             "Asked for 4x4, got 5x4: the fixer guesses the real layout (and you can correct it)"),
            (NEW / "Nanobanana Attempts (2).jpg", NEW / "Nanobanana_Attempts_(2).png",
             "Edit mode with the reference: on-palette reds, 16 for 16"),
            (NEW / "cars2.jpg", NEW / "cars2.png",
             "No reference, colors only described: red-to-peach drift — the fixer's Even out repairs it")]
    rows = [r for r in rows if r[0].is_file() and r[1].is_file()]
    y, h = 180, (1080 - 200) // max(1, len(rows))
    d = ImageDraw.Draw(img)
    f = font(FONT_BOLD, 24)
    for raw_p, mask_p, note in rows:
        raw, _ = framed(Image.open(raw_p), (560, h - 52))
        mask, _ = framed(Image.open(mask_p), (760, h - 52))
        x = (1920 - raw.width - mask.width - 120) // 2
        img.alpha_composite(raw, (x, y))
        shadow_text(img, (x + raw.width + 60, y + raw.height // 2), "→",
                    font(FONT_TITLE, 70), anchor="mm", shadow=4)
        img.alpha_composite(mask, (x + raw.width + 120, y + (raw.height - mask.height) // 2))
        d.text((960, y + max(raw.height, mask.height) + 6), note, font=f, fill=INK,
               anchor="ma", stroke_width=2, stroke_fill=(0, 0, 0))
        y += h
    return img


def color_cleanup(src: str, title: str, sub: str, badges: list[tuple[int, int]],
                  foot: str) -> Image.Image:
    img = canvas(title, sub)
    shot, _ = framed(Image.open(NEW / src), (1840, 780))
    for n, xy in enumerate(badges, 1):
        badge(shot, (6 + xy[0], 6 + xy[1]), n)
    img.alpha_composite(shot, ((1920 - shot.width) // 2, 175))
    blurb(img, 175 + shot.height + 24, foot, size=27)
    return img


def cleanup_nb3() -> Image.Image:
    return color_cleanup(
        "step5_nb3.png", "COLOR CLEANUP, THEN ANY ARMY",
        "Six units from one sheet: three cleanup stages, then three armies",
        [],
        "Top three rows: plain nearest-color matching, the fixer's quantizer, and Even out. "
        "Bottom three: the cleaned sprites through Commander Wars' own color tables — "
        "the body follows the army, while tyres, guns and glass stay put.")


def cleanup_cars2() -> Image.Image:
    return color_cleanup(
        "step5_cars2.png", "EVEN OUT: FIXING DRIFTED COLORS",
        "cars2.jpg was generated without a reference, and its SUVs drifted from red to peach",
        [(780, 60), (780, 320), (780, 450)],
        "1: A peach SUV, as drawn. Peach sits at the light end of the team ramp, so it would "
        "look washed out in every army.  2: Even out pulls its body tones back to Orange Star's "
        "usual red.  3: Now it matches its neighbours in Blue Moon, Green Earth and Yellow Comet.")


ANNOTATED = {
    "11_edit_mode_setup": (edit_mode_setup, "venice ai edit image workflow (2).png"),
    "12_edit_mode_result": (edit_mode_result, "venice ai edit image workflow (1)_img.jpg"),
    "13_sheet_to_cow_files": (sheet_to_mask, "Nanobanana_Attempts_(3).png"),
    "14_every_sheet": (more_sheets, "cars2.png"),
    "15_color_cleanup": (cleanup_nb3, "step5_nb3.png"),
    "16_even_out": (cleanup_cars2, "step5_cars2.png"),
}


def hero_backdrop(size: tuple[int, int], mask: Image.Image, bg: str) -> Image.Image:
    """Busy backdrop: rows of the converted unit in every army, icon art on top."""
    W, H = size
    img = cp.make_background(bg, size)
    k = 2
    sw, sh = mask.width * k + 14, mask.height * k + 10
    i = 0
    for row, y in enumerate(range(-20, H, sh)):
        off = (row % 2) * sw // 2
        for x in range(-off, W, sw):
            spr = scale(cp.recolor(mask, TEAMS[i % len(TEAMS)], PAL), k)
            img.alpha_composite(spr, (x, y))
            i += 7
    veil = Image.new("RGBA", size, (10, 12, 30, 150))
    img.alpha_composite(veil)
    return img


def cover(mask: Image.Image) -> Image.Image:
    W, H = 1280, 720
    img = hero_backdrop((W, H), mask, "night")
    icon = Image.open(PROMO / "icon_og.png").convert("RGBA")
    icon = icon.resize((620, 620), Image.NEAREST)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow.alpha_composite(icon, (W - 640, 70))
    sh = glow.split()[3].filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(Image.merge("RGBA", (*[sh.point(lambda v: 0)] * 3, sh)))
    img.alpha_composite(glow)
    shadow_text(img, (50, 60), "CoW UNIT", font(FONT_TITLE, 170), shadow=10, stroke=4)
    shadow_text(img, (50, 240), "SPRITE SUITE", font(FONT_TITLE, 130), fill=(255, 196, 60), shadow=10, stroke=4)
    shadow_text(img, (54, 420), "Creating new unit sprites,", font(FONT_BOLD, 44), stroke=2)
    shadow_text(img, (54, 488), "made fun and easy.", font(FONT_BOLD, 44), stroke=2)
    shadow_text(img, (54, 600), "Commander Wars modding  •  free", font(FONT_BOLD, 38),
                fill=(180, 220, 255), stroke=2)
    return img


def thumbnail(mask: Image.Image) -> Image.Image:
    W, H = 630, 500
    img = hero_backdrop((W, H), mask, "night")
    icon = Image.open(PROMO / "icon_og.png").convert("RGBA").resize((340, 340), Image.NEAREST)
    img.alpha_composite(icon, (W - 330, H - 345))
    shadow_text(img, (24, 14), "CoW UNIT", font(FONT_TITLE, 118), shadow=8, stroke=3)
    shadow_text(img, (24, 140), "SPRITE SUITE", font(FONT_TITLE, 86), fill=(255, 196, 60), shadow=8, stroke=3)
    shadow_text(img, (26, 270), "Drawing units", font(FONT_BOLD, 50), stroke=2)
    shadow_text(img, (26, 330), "Made easy.", font(FONT_BOLD, 50), stroke=2)
    shadow_text(img, (26, 420), "Commander Wars", font(FONT_BOLD, 34), fill=(180, 220, 255), stroke=2)
    return img


# Each exe gets the hero tank in a different army, so the programs tell apart
# at a glance in the taskbar: Blue Moon for the previewer (its cool pydata
# theme), Yellow Comet for the wildcard generator (warm gruvbox), Green Earth
# for the AI sprite fixer (green everforest). The colors come from the real
# pipeline -- the converted mask, recolored through CoW's tables. icon_og.png
# is the red original the cover and thumbnail use.
ICONS = {"icon_previewer": "blue_moon", "icon_wildcards": "yellow_comet",
         "icon_aifix": "green_earth"}
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def icon_art(spr: Image.Image, side: int = 1024) -> Image.Image:
    """The unit, cropped and NEAREST-upscaled by the largest whole factor
    that leaves a 32px margin, centred on a transparent square."""
    spr = spr.crop(spr.getbbox())
    spr = scale(spr, (side - 64) // max(spr.size))   # 32px breathing room
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    img.alpha_composite(spr, ((side - spr.width) // 2, (side - spr.height) // 2))
    return img


def save_ico(art: Image.Image, path: pathlib.Path) -> None:
    """Crisp NEAREST sizes from 64 up; the tiny ones are smoothed, where a
    nearest-neighbour cut of a 1024 image would drop whole pixel rows."""
    frames = [art.resize((s, s), Image.NEAREST if s >= 64 else Image.LANCZOS)
              for s in ICO_SIZES]
    # BMP entries, not PNG: Windows reads either, but Tk's own .ico reader
    # (the title-bar icon when run from source) shows a blank for PNG ones.
    frames[-1].save(path, sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1],
                    bitmap_format="bmp")


def build_icons(mask: Image.Image) -> None:
    icon_art(key_green(Image.open(HERO))).save(PROMO / "icon_og.png")
    for stem, team in ICONS.items():
        art = icon_art(cp.recolor(mask, team, PAL))
        art.save(PROMO / f"{stem}_og.png")
        save_ico(art, PROMO / f"{stem}.ico")
        print(f"{stem}.ico + {stem}_og.png  ({pretty(team)})")


# gruvbox, to match the wildcard generator's own theme.
GRUV_BG = (29, 32, 33)
GRUV_INKS = [(251, 73, 52), (184, 187, 38), (250, 189, 47), (131, 165, 152),
             (211, 134, 155), (142, 192, 124), (254, 128, 25), (235, 219, 178)]


def wildcard_subjects(n: int, seed: int = 7) -> list[str]:
    """`n` distinct subjects from the default slots (a2 + a3 + c3), rolled by
    the generator's own randomize -- real output, nothing hand-picked."""
    import random
    import wildcards as wc

    slots = wc.load_slots()
    on = wc.default_keys(slots)
    for s in slots:
        s.enabled = s.key in on
    rng = random.Random(seed)
    seen: dict[str, None] = {}
    for _ in range(n * 20):
        wc.randomize(slots, rng)
        text = wc.assemble(slots)
        if 12 <= len(text) <= 42:          # long ones turn the cloud to mush
            seen.setdefault(text, None)
        if len(seen) == n:
            break
    return list(seen)


def wildcard_ideas() -> Image.Image:
    """A cloud of generated vehicle ideas spiralling out from a title card."""
    import random

    W, H = 1600, 900
    img = Image.new("RGBA", (W, H), GRUV_BG + (255,))
    # Soft warm glow behind the middle so the cloud has a centre of gravity.
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.18, H * 0.12, W * 0.82, H * 0.88), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    img.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (W, H), v) for v in (80, 56, 36)], glow)))

    free = np.ones((H, W), bool)
    pad = 5

    def claim(box) -> None:
        x0, y0, x1, y1 = box
        free[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = False

    # Title card in the middle; the cloud packs around it.
    cx, cy = W // 2, H // 2
    t1, t2 = font(FONT_TITLE, 104), font(FONT_BOLD, 30)
    title, sub = "ENDLESS UNIT IDEAS", "Wildcard Vehicle Generator  ·  vehicle + connector + weapon"
    tb1 = ImageDraw.Draw(img).textbbox((cx, cy - 34), title, font=t1, anchor="mm")
    tb2 = ImageDraw.Draw(img).textbbox((cx, cy + 46), sub, font=t2, anchor="mm")
    claim((min(tb1[0], tb2[0]) - 24, tb1[1] - 18, max(tb1[2], tb2[2]) + 24, tb2[3] + 18))
    tank = Image.open(PROMO / "icon_wildcards_og.png").convert("RGBA").resize((96, 96), Image.NEAREST)
    tank_xy = (tb1[0] - 112, (tb1[1] + tb1[3]) // 2 - 52)
    claim((tank_xy[0], tank_xy[1], tank_xy[0] + 96, tank_xy[1] + 96))

    rng = random.Random(3)
    draw = ImageDraw.Draw(img)
    placed = 0
    for i, text in enumerate(wildcard_subjects(900)):
        size = max(11, round(34 * 0.982 ** i))
        f = font(FONT_BODY, size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        w, h = r - l, b - t
        # Archimedean spiral, stretched to the canvas aspect, from a random
        # starting angle so the cloud does not grow lopsided.
        a0 = rng.random() * 6.283
        for step in range(0, 9000):
            ang = a0 + step * 0.11
            rad = 2.2 * step ** 0.5 * 6
            x = int(cx + rad * np.cos(ang) * 1.75 - w / 2)
            y = int(cy + rad * np.sin(ang) - h / 2)
            if x < 14 or y < 12 or x + w > W - 14 or y + h > H - 12:
                if rad > W:
                    break
                continue
            if free[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad].all():
                ink = GRUV_INKS[rng.randrange(len(GRUV_INKS))]
                fade = 255 if size >= 18 else 200
                draw.text((x - l, y - t), text, font=f, fill=ink + (fade,))
                claim((x, y, x + w, y + h))
                placed += 1
                break

    shadow_text(img, (cx, cy - 34), title, t1, fill=(250, 189, 47), anchor="mm", shadow=8, stroke=3)
    shadow_text(img, (cx, cy + 46), sub, t2, fill=(235, 219, 178), anchor="mm", shadow=6, stroke=1)
    img.alpha_composite(tank, tank_xy)
    print(f"    wildcard cloud: {placed} subjects placed")
    return img


def main() -> int:
    PROMO.mkdir(exist_ok=True)
    mask = convert_hero()
    build_icons(mask)
    jobs = {
        "01_one_sprite_32_armies": lambda: one_sprite_32_armies(mask),
        "02_pipeline": lambda: pipeline(mask),
        "03_reference_gallery": reference_gallery,
        "07_wildcard_ideas": wildcard_ideas,
        "thumbnail_630x500": lambda: thumbnail(mask),
        "cover_1280x720": lambda: cover(mask),
    }
    if COW_MAP.is_dir():
        jobs["04_cow_map_units"] = cow_map_units
    if SHEET.is_file():
        jobs["08_sheet_cutter"] = sheet_cutter
    if AI_SHEET.is_file():
        jobs["09_ai_fixer"] = ai_fixer
    for stem, (make, needs) in ANNOTATED.items():
        if (NEW / needs).is_file():
            jobs[stem] = make
    for stem, make in jobs.items():
        out = save_smallest(make(), stem)
        print(f"{out.name:32s} {out.stat().st_size // 1024:5d} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
