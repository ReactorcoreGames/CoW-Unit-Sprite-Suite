"""Builds the YouTube trailer from real pipeline output. Rerun after art changes.

    python tools/build_trailer.py --preview          # 960x540 @ 15 fps, fast
    python tools/build_trailer.py --stills           # key frames + contact sheet
    python tools/build_trailer.py                    # final 1920x1080 @ 30 fps
    python tools/build_trailer.py --music other.mp3  # another track
    python tools/build_trailer.py --silent           # no music

Every sprite on screen goes through the suite's own code as it renders: the
hero tank is cut and converted by `split`/`convert` and recolored through
CoW's colortables by `cowpalette.recolor`, the hand-drawn sheet is cut by the
real cutter, and the AI sheet goes through `aifix` at its defaults. Pixel art
is only ever scaled with NEAREST. The GUI beats use the clips from
tools/record_trailer_clips.py when present, else the promo screenshots.

The edit is cut to "Heavyweight" by Ethan Meixsell (YouTube Audio Library,
in the project root, not shipped): scene lengths are counted in beats of its
147.7 BPM, so every cut lands on a beat, the AI section rides the track's
breakdown (29.25 s) and the payoff montage lands on its big section (53.6 s).
The track is faded out at the end. With --silent, or without the file, the
audio is silence (so the MP4 still carries AAC).

Writes into promo/trailer/:
    cow_unit_sprite_suite_trailer.mp4           the final render
    cow_unit_sprite_suite_trailer_preview.mp4   --preview
    thumbnail_1280x720.png                      YouTube thumbnail
    youtube.md                                  title, description with chapters, tags
    keyframes/                                  --stills
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import math
import pathlib
import random
import subprocess
import sys
import time
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_promo as bp  # noqa: E402
import cowpalette as cp  # noqa: E402
import split  # noqa: E402
from approot import ROOT  # noqa: E402

W, H, FPS = 1920, 1080, 30
OUT = ROOT / "promo" / "trailer"
CLIPS = OUT / "clips"
PROMO = bp.PROMO
PAL, TEAMS = bp.PAL, bp.TEAMS
MAP_UNITS = ROOT / "samples" / "cow map units"
UNITS_1X = bp.UNITS_1X
AI_SHEET = bp.AI_SHEET
AI_UNIT = bp.AI_UNIT
MUSIC = ROOT / "promo" / "trailer" / "Heavyweight - Ethan Meixsell.mp3"
BPM = 147.7                      # measured from the track; its first beat is at 0
BEAT = 60.0 / BPM


def beats(n: float) -> float:
    return n * BEAT

YELLOW = bp.BADGE
WHITE = (255, 255, 255)
SKY = (180, 220, 255)
GREY = (200, 202, 214)
CLASSIC = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole"]


# ------------------------------------------------------------------ easing

def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease(x: float) -> float:
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ramp(t: float, start: float, dur: float = 0.25) -> float:
    """0 before `start`, eased to 1 over `dur`."""
    return ease((t - start) / dur) if dur > 0 else float(t >= start)


def lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


# ------------------------------------------------------------------- drawing

@functools.lru_cache(maxsize=None)
def background(name: str, size: tuple[int, int] = (W, H), veil: int = 0) -> Image.Image:
    img = cp.make_background(name, size)
    if veil:
        img.alpha_composite(Image.new("RGBA", size, (10, 12, 30, veil)))
    return img


def bg(name: str, veil: int = 0) -> Image.Image:
    return background(name, (W, H), veil).copy()


def scale(im: Image.Image, k: float) -> Image.Image:
    k = max(k, 1e-3)
    return im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.NEAREST)


def with_alpha(layer: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return layer
    out = layer.copy()
    out.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
    return out


def paste(frame: Image.Image, layer: Image.Image, xy, a: float = 1.0) -> None:
    """alpha_composite at a float position, faded by `a`, clipped to the frame."""
    if a > 0.001:
        _paste_clipped(frame, with_alpha(layer, a), xy)


def _paste_clipped(frame: Image.Image, layer: Image.Image, xy) -> None:
    x, y = int(round(xy[0])), int(round(xy[1]))
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.width, x + layer.width), min(frame.height, y + layer.height)
    if x1 <= x0 or y1 <= y0:
        return
    frame.alpha_composite(layer.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


@dataclasses.dataclass
class Text:
    """A pre-rendered line of shadowed text, placed by anchor each frame."""

    layer: Image.Image
    pad: int
    w: int
    h: int

    def draw(self, frame: Image.Image, x: float, y: float, anchor: str = "mt",
             a: float = 1.0, rise: float = 0.0) -> None:
        """`anchor`: l/m/r then t/m/b. `a` fades it in, sliding up by `rise`
        px as it does."""
        if a <= 0.001:
            return
        ox = {"l": 0, "m": self.w / 2, "r": self.w}[anchor[0]]
        oy = {"t": 0, "m": self.h / 2, "b": self.h}[anchor[1]]
        paste(frame, self.layer, (x - ox - self.pad, y - oy - self.pad + rise * (1 - a)), a)


@functools.lru_cache(maxsize=None)
def text(s: str, face: str = bp.FONT_TITLE, size: int = 100, fill=WHITE,
         shadow: int = 8, stroke: int = 3) -> Text:
    f = bp.font(face, size)
    l, t, r, b = f.getbbox(s, anchor="la", stroke_width=stroke)
    pad = 4 * shadow + stroke + 4
    layer = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    bp.shadow_text(layer, (pad - l, pad - t), s, f, fill=fill, anchor="la",
                   shadow=shadow, stroke=stroke)
    return Text(layer, pad, r - l, b - t)


def title(s: str, size: int = 110, fill=WHITE) -> Text:
    return text(s, bp.FONT_TITLE, size, fill, 8, 3)


def body(s: str, size: int = 44, fill=WHITE) -> Text:
    return text(s, bp.FONT_BOLD, size, fill, 5, 2)


def card(im: Image.Image, pad: int = 20, fill=(255, 255, 255, 235)) -> Image.Image:
    c = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), fill)
    c.alpha_composite(im, (pad, pad))
    return c


def glow(im: Image.Image, radius: int = 18, pad: int = 60) -> Image.Image:
    """`im` over a soft black shadow of its own silhouette."""
    out = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    out.paste(im.getchannel("A"), (pad, pad))
    a = out.getchannel("A").filter(ImageFilter.GaussianBlur(radius))
    sh = Image.merge("RGBA", (*[a.point(lambda v: 0)] * 3, a))
    sh.alpha_composite(im, (pad, pad))
    return sh


def chip(s: str, size: int = 36) -> Image.Image:
    """A key hint in a dark rounded box: 'W / S  armies'."""
    f = bp.font(bp.FONT_BOLD, size)
    l, t, r, b = f.getbbox(s)
    im = Image.new("RGBA", (r - l + 48, b - t + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, im.width - 1, im.height - 1), 14, fill=(12, 14, 26, 225),
                        outline=YELLOW, width=3)
    d.text((24 - l, 15 - t), s, font=f, fill=YELLOW)
    return im


def plate(src: Image.Image, veil: int = 120) -> Image.Image:
    """A screenshot on a 1920x1080 night plate, centred at 1:1 (or fitted)."""
    img = bg("night", veil)
    s = src.convert("RGBA")
    if s.width > W or s.height > H:
        s = scale(s, min(W / s.width, H / s.height))
    img.alpha_composite(s, ((W - s.width) // 2, (H - s.height) // 2))
    return img


def camera(src: Image.Image, cx: float, cy: float, zoom: float) -> Image.Image:
    """The W x H view of `src` centred on (cx, cy) at `zoom`, clamped inside."""
    w, h = W / zoom, H / zoom
    x0 = min(max(cx - w / 2, 0), src.width - w)
    y0 = min(max(cy - h / 2, 0), src.height - h)
    return src.resize((W, H), Image.NEAREST, box=(x0, y0, x0 + w, y0 + h))


def push(src: Image.Image, t: float, dur: float, z1: float = 1.1,
         focus: tuple[float, float] = (W / 2, H / 2)) -> Image.Image:
    p = ease(t / dur)
    return camera(src, lerp(W / 2, focus[0], p), lerp(H / 2, focus[1], p), lerp(1.0, z1, p))


def zoom_to(src: Image.Image, t: float, hold: float, move: float, focus, z1: float) -> Image.Image:
    """Hold the whole plate (its title readable), then zoom into `focus`."""
    p = ramp(t, hold, move)
    return camera(src, lerp(W / 2, focus[0], p), lerp(H / 2, focus[1], p), lerp(1.0, z1, p))


def divider(frame: Image.Image, x: int, y0: int, y1: int) -> None:
    d = ImageDraw.Draw(frame)
    d.rectangle((x - 5, y0, x + 5, y1), fill=(0, 0, 0))
    d.rectangle((x - 2, y0, x + 2, y1), fill=WHITE)
    cy = (y0 + y1) // 2
    d.ellipse((x - 26, cy - 26, x + 26, cy + 26), fill=(0, 0, 0))
    d.ellipse((x - 21, cy - 21, x + 21, cy + 21), fill=YELLOW)
    d.text((x, cy + 1), "◀▶", font=bp.font("seguisym.ttf", 20), fill=(0, 0, 0), anchor="mm")


def key_out(im: Image.Image) -> Image.Image:
    return bp.key_green(im)


# ------------------------------------------------------------------- assets

class Assets:
    """Everything built from the pipeline, computed once."""

    def __init__(self) -> None:
        t0 = time.time()
        self.mask = bp.convert_hero()                       # the hero's CoW mask
        self.hero_red = key_out(Image.open(bp.HERO))        # as drawn, keyed
        self.hero_raw = Image.open(bp.HERO).convert("RGBA")  # as drawn, on green
        self.team_color = self._team_colors()
        self.march = self._march_units()
        self.problem = cp.split_frames(
            Image.open(MAP_UNITS / "megatank+mask.png").convert("RGBA"), 4)[0]
        self.sheet = Image.open(bp.SHEET).convert("RGBA")
        self.cut = split.split_sheet(self.sheet, name="sheet").pieces
        self.refs = self._reference_masks()
        self.ai = None
        if AI_SHEET.is_file():
            import aifix
            self.ai_raw = Image.open(AI_SHEET).convert("RGB")
            self.ai = aifix.fix_sheet(self.ai_raw, name="ai")
        self.backdrop_hook = bp.hero_backdrop((W + 480, H), self.mask, "night")
        self.backdrop_end = bp.hero_backdrop((W + 480, H), self.mask, "night")
        print(f"assets ready in {time.time() - t0:.1f}s "
              f"({len(self.refs)} reference units, AI sheet: {self.ai is not None})")

    def _team_colors(self) -> dict[str, tuple[int, int, int]]:
        """Each army's main body color: the hero's commonest red pixel (its
        commonest overall is the black outline), looked up in each table."""
        hist = cp.coord_histogram(self.mask)
        os_ = PAL["orange_star"]

        def red(c) -> bool:
            r, g, b, _ = os_.lookup(*c)
            return r - max(g, b) > 80
        r, g = max((c for c in hist if red(c)), key=hist.get)
        return {t: PAL[t].lookup(r, g)[:3] for t in TEAMS}

    def _march_units(self) -> list[list[Image.Image]]:
        names = ["light_tank", "heavy_tank", "megatank", "neotank", "apc", "recon",
                 "artillery", "missile", "flak", "rocketthrower", "piperunner",
                 "infantry+bm", "infantry+bh"]
        units = []
        for i, n in enumerate(names * 2):
            p = MAP_UNITS / f"{n}+mask.png"
            if not p.is_file():
                continue
            im = Image.open(p).convert("RGBA")
            frames = cp.split_frames(im, max(1, im.width // im.height))
            team = TEAMS[(i * 5) % len(TEAMS)]
            units.append([scale(cp.recolor(f, team, PAL), 96 / f.height) for f in frames])
        return units

    def _reference_masks(self) -> list[Image.Image]:
        out = []
        for p in sorted(UNITS_1X.glob("*.png"))[::2]:
            res = split.split_sheet(Image.open(p), name=p.stem)
            pieces = [q for q in res.pieces if q.result is not None and q.result.ok]
            if pieces:
                big = max(pieces, key=lambda q: (q.box[2] - q.box[0]) * (q.box[3] - q.box[1]))
                out.append(big.result.encoded)
        return out


def clip_frames(name: str) -> list[Image.Image]:
    folder = CLIPS / name
    return [plate(Image.open(p)) for p in sorted(folder.glob("*.png"))] if folder.is_dir() else []


# ------------------------------------------------------------------- scenes
#
# Each scene is a function of local time t (seconds) that returns a W x H
# RGBA frame. Factories take the assets and precompute what they can.

def scene_hook(A: Assets):
    k = max(1, 600 // A.mask.height)
    tanks = {t: glow(scale(cp.recolor(A.mask, t, PAL), k), 22, 70) for t in TEAMS}
    t1, t2, t3 = title("UNIT SPRITES,", 160), title("MADE FUN.", 190, YELLOW), \
        body("CoW Unit Sprite Suite  ·  for Commander Wars", 42, SKY)

    def render(t: float) -> Image.Image:
        x = int(t * 70)
        f = A.backdrop_hook.crop((x, 0, x + W, H))
        i = int(t / (BEAT / 4)) % 32           # a new army every 16th note
        tank = tanks[TEAMS[i]]
        f.alpha_composite(tank, (1390 - tank.width // 2, 520 - tank.height // 2))
        name = body(bp.pretty(TEAMS[i]), 46)
        name.draw(f, 1390, 900, "mt")
        body(f"{i + 1:02d} / 32", 34, YELLOW).draw(f, 1390, 966, "mt")
        t1.draw(f, 110, 300, "lt", ramp(t, 0.15), 30)
        t2.draw(f, 110, 480, "lt", ramp(t, 1.0), 30)
        t3.draw(f, 116, 700, "lt", ramp(t, 1.9), 20)
        return f
    return render


def scene_problem(A: Assets):
    spr = A.problem
    a = np.array(spr)
    op = a[..., 3] > 0
    cols, counts = np.unique(a[op][:, :3], axis=0, return_counts=True)
    swatches = [tuple(int(v) for v in c) for c in cols[np.argsort(-counts)][:12]]
    lines = [title("THIS IS HOW", 110), title("COMMANDER WARS", 110),
             title("STORES SPRITES.", 110)]
    nobody = body("Nobody wants to draw in this.", 66, YELLOW)
    cap = body("megatank+mask.png, as the game ships it", 32, GREY)

    def render(t: float) -> Image.Image:
        f = bg("dusk", 160)
        k = 8 + int(8 * ramp(t, 0.1, 0.3))          # integer steps only
        big = scale(spr, k)
        cx, cy = 440, 480
        box = Image.new("RGBA", (16 * 32 + 40, 16 * 32 + 40), (0, 0, 0, 110))
        f.alpha_composite(box, (cx - box.width // 2, cy - box.height // 2))
        f.alpha_composite(big, (cx - big.width // 2, cy - big.height // 2))
        cap.draw(f, cx, cy + 300, "mt", ramp(t, 0.6))
        d = ImageDraw.Draw(f)
        for i, c in enumerate(swatches):
            if t >= 1.0 + i * 0.07:
                x = 184 + i * 44
                d.rectangle((x, 870, x + 38, 908), fill=c, outline=(0, 0, 0), width=3)
        for i, ln in enumerate(lines):
            ln.draw(f, 820, 230 + i * 125, "lt", ramp(t, 0.35 + i * 0.2), 30)
        nobody.draw(f, 826, 680, "lt", ramp(t, 2.0, 0.3), 24)
        return f
    return render


def scene_draw_red(A: Assets):
    k = max(1, min(6, 360 // A.mask.height))
    p1 = card(scale(A.hero_raw, k), 24)
    p2 = card(scale(A.mask, k), 24)
    picks = ["orange_star", "blue_moon", "green_earth", "yellow_comet", "black_hole", "pink_frontier"]
    k3 = max(1, min(3, 190 // A.mask.height))
    tiles = []
    for tm in picks:
        tile = background("day", (230, 200)).copy()
        s = scale(cp.recolor(A.mask, tm, PAL), k3)
        tile.alpha_composite(s, ((230 - s.width) // 2, (200 - s.height) // 2))
        tiles.append(tile)
    head = title("DRAW IN ORANGE STAR RED", 110)
    arrow = title("→", 110)
    labels = [(title("1. DRAW", 62), body("Orange Star red on flat green", 30)),
              (title("2. CONVERT", 62), body("CoW's mask: the master file", 30)),
              (title("3. ANY ARMY", 62), body("via CoW's own colortables", 30))]
    xs = [110, 110 + p1.width + 150, 0]
    xs[2] = xs[1] + p2.width + 150
    y = 200

    def render(t: float) -> Image.Image:
        f = bg("dusk", 40)
        head.draw(f, W / 2, 34, "mt", ramp(t, 0.0, 0.2), 20)
        for i, (p, at) in enumerate(((p1, 0.25), (p2, 1.0))):
            a = ramp(t, at, 0.25)
            paste(f, p, (xs[i], y + 30 * (1 - a)), a)
            labels[i][0].draw(f, xs[i] + p.width / 2, y + p.height + 20, "mt", a)
            labels[i][1].draw(f, xs[i] + p.width / 2, y + p.height + 100, "mt", a)
            if i < 1 or t >= 1.8:
                arrow.draw(f, xs[i] + p.width + 75, y + p.height / 2, "mm", ramp(t, at + 0.5, 0.2))
        for i, tile in enumerate(tiles):
            a = ramp(t, 1.8 + i * 0.12, 0.2)
            paste(f, tile, (xs[2] + (i % 3) * 240, y + (i // 3) * 210 + 20 * (1 - a)), a)
        a = ramp(t, 1.8, 0.25)
        labels[2][0].draw(f, xs[2] + 350, y + p1.height + 20, "mt", a)
        labels[2][1].draw(f, xs[2] + 350, y + p1.height + 100, "mt", a)
        return f
    return render


def scene_cutter(A: Assets):
    sheet, pieces = A.sheet, A.cut
    head = title("CUT A WHOLE SHEET IN ONE CLICK", 104)
    arrow = title("→", 110)
    sub = body(f"{len(pieces)} units found, scaled and converted", 34)
    small = bp.font(bp.FONT_BOLD, 16)
    R1 = (60, 196, 0.5)                     # final x, y, scale (4x art -> exact 2x)
    tw, th, gap, gx, gy = 205, 150, 8, 822, 200
    tiles = []
    for i, p in enumerate(pieces):
        tile = background("day", (tw, th)).copy()
        s = cp.recolor(p.result.encoded, bp.CUTTER_TEAMS[i % len(bp.CUTTER_TEAMS)], PAL)
        s = scale(s, max(1, min(2, (th - 12) // s.height, (tw - 12) // s.width)))
        tile.alpha_composite(s, ((tw - s.width) // 2, th - s.height - 4))
        ImageDraw.Draw(tile).text((5, 3), f"{p.index:02d}", font=small, fill=(20, 20, 30))
        tiles.append(tile)

    def render(t: float) -> Image.Image:
        f = bg("dusk", 40)
        p = ease(t / 1.8)
        s = lerp(1.0, R1[2], p)
        x0, y0 = lerp(960 - 420, R1[0], p), lerp(600 - 380, R1[1], p)
        shown = scale(sheet, s)
        if p >= 1:
            frame = Image.new("RGBA", (shown.width + 12, shown.height + 12), (0, 0, 0, 255))
            f.alpha_composite(frame, (int(x0) - 6, int(y0) - 6))
        paste(f, shown, (x0, y0))
        d = ImageDraw.Draw(f)
        for i, pc in enumerate(pieces):
            if t < 1.9 + i * 0.05:
                break
            bx0, by0, bx1, by1 = (v * R1[2] for v in pc.box)
            bx0, by0, bx1, by1 = R1[0] + bx0 - 3, R1[1] + by0 - 3, R1[0] + bx1 + 2, R1[1] + by1 + 2
            d.rectangle([bx0, by0, bx1, by1], outline=(0, 0, 0), width=4)
            d.rectangle([bx0 + 1, by0 + 1, bx1 - 1, by1 - 1], outline=(255, 214, 40), width=2)
            lab = f"{pc.index:02d}"
            lw = d.textlength(lab, font=small)
            d.rectangle([bx0, by0 - 19, bx0 + lw + 8, by0], fill=(0, 0, 0))
            d.text((bx0 + 4, by0 - 18), lab, font=small, fill=(255, 214, 40))
        arrow.draw(f, 752, 520, "mm", ramp(t, 3.0, 0.2))
        for i, tile in enumerate(tiles):
            a = ramp(t, 3.1 + i * 0.06, 0.18)
            paste(f, tile, (gx + (i % 5) * (tw + gap), gy + (i // 5) * (th + gap) + 20 * (1 - a)), a)
        sub.draw(f, gx + (5 * tw + 4 * gap) / 2, gy + 4 * (th + gap) + 14, "mt", ramp(t, 4.4))
        head.draw(f, W / 2, 30, "mt", ramp(t, 0.0, 0.2), 20)
        return f
    return render


def scene_armies(A: Assets):
    head = title("EVERY ARMY, FOR FREE", 110)
    tw, th, gap = 224, 168, 8
    x0, y0 = (W - (8 * tw + 7 * gap)) // 2, 168
    k = max(1, min(3, (th - 40) // A.mask.height, (tw - 16) // A.mask.width))
    f20 = bp.font(bp.FONT_BOLD, 20)
    tiles = []
    for team in TEAMS:
        tile = background("day", (tw, th)).copy()
        s = scale(cp.recolor(A.mask, team, PAL), k)
        tile.alpha_composite(s, ((tw - s.width) // 2, (th - 30 - s.height) // 2 + 2))
        ImageDraw.Draw(tile).text((tw // 2, th - 6), bp.pretty(team), font=f20,
                                  fill=(20, 20, 30), anchor="md")
        tiles.append(tile)

    def render(t: float) -> Image.Image:
        f = bg("night", 60)
        for i, tile in enumerate(tiles):
            c, r = i % 8, i // 8
            a = ramp(t, 0.2 + (c + r) * 0.07, 0.2)
            paste(f, tile, (x0 + c * (tw + gap), y0 + r * (th + gap) + 24 * (1 - a)), a)
        head.draw(f, W / 2, 30, "mt", ramp(t, 0.0, 0.2), 20)
        return f
    return render


def march_overlay(A: Assets):
    """CoW map units marching along the bottom, idle frames cycling."""
    n = len(A.march)
    spacing = max(170, (W + 400) // max(1, n) + 1)
    loop = n * spacing
    band = Image.new("RGBA", (W, 150), (0, 0, 0, 0))
    ba = np.zeros((150, W, 4), np.uint8)
    ba[..., 3] = np.linspace(0, 150, 150, dtype=np.uint8)[:, None]
    band = Image.fromarray(ba, "RGBA")

    def draw(f: Image.Image, t: float) -> None:
        f.alpha_composite(band, (0, H - 150))
        for i, frames in enumerate(A.march):
            x = (i * spacing + t * 150) % loop - 200
            if -200 < x < W:
                fr = frames[int(t / 0.15 + i) % len(frames)]
                paste(f, fr, (x, H - 22 - fr.height))
    return draw


def scene_clip(frames: list[Image.Image], fallback: pathlib.Path, head: str,
               chips: dict[int, str], start: float, step: float, z1: float = 1.08,
               focus=(W / 2, H / 2), head_y: int = 860, ai: bool = False):
    if not frames:
        frames = [plate(Image.open(fallback))]
        chips = {}
    cap = title(head, 92)
    chip_im = {j: chip(s) for j, s in chips.items()}

    def render(t: float, dur: float) -> Image.Image:
        j = 0 if t < start else min(len(frames) - 1, 1 + int((t - start) / step))
        f = push(frames[j], t, dur, z1, focus)
        cap.draw(f, W / 2, head_y, "mt", ramp(t, 0.15, 0.25), 20)
        keys = [c for c in chip_im if c <= j]
        if keys:
            im = chip_im[max(keys)]
            f.alpha_composite(im, ((W - im.width) // 2, head_y + 112))
        if ai:
            ai_pill(f)
        return f
    return render


def wildcard_rolls(n: int, seed: int = 11) -> list[list[str]]:
    """`n` subjects rolled by the generator's own core from its default slots
    (a2 vehicle + a3 connector + c3 weapon), kept as their parts."""
    import wildcards as wc
    slots = wc.load_slots()
    on = wc.default_keys(slots)
    for sl in slots:
        sl.enabled = sl.key in on
    rng = random.Random(seed)
    out, seen = [], set()
    for _ in range(n * 30):
        wc.randomize(slots, rng)
        full = wc.assemble(slots)
        if 14 <= len(full) <= 40 and full not in seen:
            seen.add(full)
            out.append([" ".join(sl.current.replace(wc.PLACEHOLDER, " ").split())
                        for sl in slots if sl.enabled])
        if len(out) == n:
            break
    return out


def scene_wildcards(A: Assets):
    """The generator as the AI route's first step: its slots, subject and
    prompt row (06) and the idea cloud (07), cropped at 1:1, in a column on
    the left, and real rolls scrolling past on the right like a slot machine."""
    gen = Image.open(PROMO / "06_wildcard_generator.png").convert("RGBA")
    cloud = Image.open(PROMO / "07_wildcard_ideas.png").convert("RGBA")
    crops = [(gen.crop((0, 152, 940, 492)), "roll the slots"),
             (gen.crop((0, 822, 940, 1022)), "copy the full prompt"),
             (cloud.crop((280, 340, 1220, 540)), "or just browse ideas")]
    col_x, col_y, gap = 40, 214, 18
    panels, y = [], col_y
    for im, label in crops:
        fr = Image.new("RGBA", (im.width + 8, im.height + 8), (0, 0, 0, 255))
        fr.alpha_composite(im, (4, 4))
        c = chip(label, 24)
        fr.alpha_composite(c, (fr.width - 12 - c.width, fr.height - 12 - c.height))
        panels.append((fr, y))
        y += fr.height + gap

    # the roll strip: each subject in three colors, one per slot
    colors = [WHITE, (150, 156, 180), YELLOW]
    f36 = bp.font(bp.FONT_BOLD, 36)
    rolls = wildcard_rolls(60)
    lh = 58
    px, pw = 1010, W - 40 - 1010
    strip = Image.new("RGBA", (pw, lh * len(rolls)), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    for i, parts in enumerate(rolls):
        x = 30
        for part, c in zip(parts, colors):
            d.text((x, i * lh + 8), part, font=f36, fill=c, stroke_width=2, stroke_fill=(0, 0, 0))
            x += d.textlength(part + " ", font=f36)
    view_y, view_h = 290, 1040 - 290
    panel = Image.new("RGBA", (pw, 1040 - col_y), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle((0, 0, pw - 1, panel.height - 1), 18,
                                            fill=(12, 14, 26, 215), outline=(70, 74, 100), width=2)
    legend = [body("a2 vehicle", 30), body("+  a3 connector", 30, colors[1]),
              body("+  c3 weapon", 30, YELLOW)]
    fade = np.zeros((90, pw, 4), np.uint8)
    fade[..., :3] = (12, 14, 26)
    fade[..., 3] = np.linspace(215, 0, 90).astype(np.uint8)[:, None]
    fade_top = Image.fromarray(fade, "RGBA")
    fade_bot = fade_top.transpose(Image.FLIP_TOP_BOTTOM)
    head = title("WILDCARD GENERATOR", 104)
    sub = body("random vehicle ideas, rolled from plain-text lists into a ready AI prompt", 36, SKY)
    base = 7 * lh - (view_h // 2 - lh // 2)     # line 7 rests in the bar at t = 0

    def render(t: float) -> Image.Image:
        f = bg("night", 90)
        for i, (fr, y) in enumerate(panels):
            a = ramp(t, 0.2 + i * 0.25, 0.25)
            paste(f, fr, (col_x - 30 * (1 - a), y), a)
        paste(f, panel, (px, col_y), ramp(t, 0.1, 0.2))
        lx = px + 30
        for lg in legend:
            lg.draw(f, lx, col_y + 22, "lt", ramp(t, 0.3))
            lx += lg.w + 18
        # one roll per beat, easing into the selection bar like a slot machine
        k, frac = divmod(t / BEAT, 1.0)
        off = int(base + lh * (k + ease(frac * 2.5)))
        paste(f, strip.crop((0, off, pw, off + view_h)), (px, view_y), ramp(t, 0.3, 0.3))
        mid = view_y + view_h // 2 - lh // 2 + 2
        ImageDraw.Draw(f).rounded_rectangle((px + 12, mid, px + pw - 12, mid + lh), 10,
                                            outline=YELLOW, width=3)
        f.alpha_composite(fade_top, (px, view_y))
        f.alpha_composite(fade_bot, (px, view_y + view_h - 90))
        head.draw(f, W / 2, 26, "mt", ramp(t, 0.0, 0.2), 20)
        sub.draw(f, W / 2, 140, "mt", ramp(t, 0.2))
        ai_pill(f)
        return f
    return render


def scene_ai_card(A: Assets):
    lines = [(title("OPTIONAL", 80, YELLOW), 230), (title("TURN AN AI SHEET INTO", 124), 340),
             (title("REAL CoW SPRITES", 124), 490)]
    sub1 = body("Battle scale only  ·  the result is a starting point for hand-fixing", 44, SKY)
    sub2 = body("Your image generator's edit mode  +  the AI Sprite Fixer", 38, GREY)

    def render(t: float) -> Image.Image:
        f = bg("dusk", 150)
        for i, (ln, y) in enumerate(lines):
            ln.draw(f, W / 2, y, "mt", ramp(t, 0.05 + i * 0.18), 30)
        sub1.draw(f, W / 2, 690, "mt", ramp(t, 0.7), 20)
        sub2.draw(f, W / 2, 770, "mt", ramp(t, 0.9), 20)
        return f
    return render


@functools.lru_cache(maxsize=1)
def _pill() -> Image.Image:
    return chip("OPTIONAL AI ROUTE", 26)


def ai_pill(f: Image.Image) -> None:
    f.alpha_composite(_pill(), (22, 18))


def scene_annotated(name: str, hold: float, move: float, focus, z1: float):
    src = plate(Image.open(PROMO / name))

    def render(t: float) -> Image.Image:
        f = zoom_to(src, t, hold, move, focus, z1)
        ai_pill(f)
        return f
    return render


def scene_sheet_wipe(A: Assets):
    """13: the generator's JPEG -> the fixer's +mask.png -> Blue Moon, as wipes
    over the same sheet. Every sprite is placed where the fixer found it."""
    still = plate(Image.open(PROMO / "13_sheet_to_cow_files.jpg"))
    if A.ai is None:
        return lambda t: (lambda f: (ai_pill(f), f)[1])(zoom_to(still, t, 1.0, 1.0, (1440, 560), 1.4))
    sheet = A.ai
    K = 3                                         # sprite px -> screen px, integer
    S = K / sheet.pitch                           # sheet px -> screen px
    raw = A.ai_raw.convert("RGBA").resize((round(A.ai_raw.width * S), round(A.ai_raw.height * S)),
                                          Image.NEAREST)
    slate = (24, 26, 40, 255)
    layers = {}
    for team in (None, "blue_moon"):
        lay = Image.new("RGBA", raw.size, slate)
        for u in sheet.units:
            if u.result is None or u.result.encoded is None:
                continue
            ys, xs = np.nonzero(u.isolated[..., 3] > 0)
            if not len(xs):
                continue
            spr = u.result.encoded if team is None else cp.recolor(u.result.encoded, team, PAL)
            spr = scale(spr, K)
            cx = (u.box[0] + (xs.min() + xs.max()) / 2) * S
            cy = (u.box[1] + (ys.min() + ys.max()) / 2) * S
            lay.alpha_composite(spr, (int(cx - spr.width / 2), int(cy - spr.height / 2)))
        layers[team] = lay
    px, py = (W - raw.width) // 2, 168
    head = title("SHEET IN, CoW FILES OUT", 96)
    caps = [body("the generator's JPEG: no pixel grid, smeared colors", 38),
            body("+mask.png: what Commander Wars reads (you never draw in it)", 38),
            body("the same files in Blue Moon, through CoW's colortables", 38)]

    def wiped(t: float) -> Image.Image:
        f = bg("night", 120)
        frame = Image.new("RGBA", (raw.width + 12, raw.height + 12), (0, 0, 0, 255))
        f.alpha_composite(frame, (px - 6, py - 6))
        f.alpha_composite(raw, (px, py))
        w1 = int(raw.width * ease((t - 1.1) / 0.8))
        w2 = int(raw.width * ease((t - 2.2) / 0.8))
        if w1 > 0:
            f.alpha_composite(layers[None].crop((0, 0, w1, raw.height)), (px, py))
        if w2 > 0:
            f.alpha_composite(layers["blue_moon"].crop((0, 0, w2, raw.height)), (px, py))
        if 0 < w1 < raw.width:
            divider(f, px + w1, py, py + raw.height)
        if 0 < w2 < raw.width:
            divider(f, px + w2, py, py + raw.height)
        stage = 0 if t < 1.4 else 1 if t < 2.5 else 2
        caps[stage].draw(f, W / 2, py + raw.height + 22, "mt")
        head.draw(f, W / 2, 30, "mt")
        ai_pill(f)
        return f

    def render(t: float) -> Image.Image:
        if t < 0.8:
            f = still.copy()
            ai_pill(f)
            return f
        if t < 1.05:
            return dissolve(still, wiped(t), (t - 0.8) / 0.25)
        return wiped(t)
    return render


def scene_one_unit(A: Assets):
    """09: one AI unit, a before/after wipe at matching scale, then 4 armies."""
    if A.ai is None:
        src = plate(Image.open(PROMO / "09_ai_fixer.jpg"))
        return lambda t: push(src, t, 3.0, 1.1)
    u = A.ai.units[AI_UNIT - 1]
    K = 8
    S = K / A.ai.pitch
    ys, xs = np.nonzero(u.isolated[..., 3] > 0)
    m = 10
    x0, y0 = u.box[0] + xs.min() - m, u.box[1] + ys.min() - m
    x1, y1 = u.box[0] + xs.max() + m + 1, u.box[1] + ys.max() + m + 1
    before = A.ai_raw.crop((x0, y0, x1, y1)).convert("RGBA")
    before = before.resize((round(before.width * S), round(before.height * S)), Image.NEAREST)
    key = tuple(int(v) for v in A.ai.key) + (255,)
    after = Image.new("RGBA", before.size, key)
    spr = scale(u.result.quantized, K)
    after.alpha_composite(spr, ((after.width - spr.width) // 2, (after.height - spr.height) // 2))
    k2 = max(1, min(3, 290 // u.result.quantized.width, 180 // u.result.quantized.height))
    tw, th = 320, 210
    tiles = []
    for team in bp.AI_ARMIES:
        tile = background("day", (tw, th)).copy()
        s = scale(cp.recolor(u.result.encoded, team, PAL), k2)
        tile.alpha_composite(s, ((tw - s.width) // 2, (th - s.height) // 2))
        tiles.append(tile)
    head = title("AI SHEET  →  CoW SPRITE", 96)
    gen, fix = chip("GENERATED", 30), chip("FIXED", 30)
    w, h = u.result.quantized.size
    sub = body(f"snapped to {w}x{h} px, on CoW's palette, one master file", 36)
    any_army = title("ANY ARMY", 64)
    arrow = title("→", 100)
    bx, by = 70, 330 + max(0, (440 - before.height) // 2)
    gx = W - 70 - (2 * tw + 12)

    def render(t: float) -> Image.Image:
        f = bg("dusk", 60)
        frame = Image.new("RGBA", (before.width + 12, before.height + 12), (0, 0, 0, 255))
        f.alpha_composite(frame, (bx - 6, by - 6))
        f.alpha_composite(before, (bx, by))
        wx = int(before.width * ease((t - 0.35) / 0.9))
        if wx > 0:
            f.alpha_composite(after.crop((0, 0, wx, after.height)), (bx, by))
        if 0 < wx < before.width:
            divider(f, bx + wx, by, by + before.height)
        f.alpha_composite(gen if wx < before.width / 2 else fix, (bx + 12, by + 12))
        sub.draw(f, bx + before.width / 2, by + before.height + 24, "mt", ramp(t, 1.2))
        arrow.draw(f, (bx + before.width + gx) / 2, by + before.height / 2, "mm", ramp(t, 1.3, 0.2))
        any_army.draw(f, gx + tw + 6, 250, "mt", ramp(t, 1.35))
        for i, tile in enumerate(tiles):
            a = ramp(t, 1.4 + i * 0.08, 0.18)
            paste(f, tile, (gx + (i % 2) * (tw + 12), 340 + (i // 2) * (th + 12) + 20 * (1 - a)), a)
        head.draw(f, W / 2, 30, "mt")
        ai_pill(f)
        return f
    return render


def scene_payoff(A: Assets):
    pool: list[Image.Image] = list(A.refs)
    if A.ai is not None:
        pool += [u.result.encoded for u in A.ai.units if u.result is not None and u.result.ok]
    cols, rows = 8, 4
    tw, th = W // cols, H // rows
    bgs = ["day", "dawn", "dusk", "night"]
    period = 2 * BEAT                       # the grid re-rolls every two beats
    cache: dict[tuple[int, int], Image.Image] = {}

    def tile(gen: int, i: int) -> Image.Image:
        if (gen, i) not in cache:
            rng = random.Random(gen * 1000 + i)
            spr = pool[rng.randrange(len(pool))]
            team = TEAMS[rng.randrange(len(TEAMS))]
            im = background(bgs[(i + gen) % 4], (tw, th)).copy()
            k = max(1, min(4, (tw - 20) // spr.width, (th - 20) // spr.height))
            s = scale(cp.recolor(spr, team, PAL), k)
            im.alpha_composite(s, ((tw - s.width) // 2, (th - s.height) // 2))
            cache[(gen, i)] = im
        return cache[(gen, i)]

    band = Image.new("RGBA", (W, 250), (8, 10, 24, 190))
    head = title("YOUR UNITS. EVERY ARMY.", 130)
    sub = body("hand-drawn and AI-fixed sprites  ·  real output through CoW's colortables", 38, SKY)

    def render(t: float) -> Image.Image:
        f = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        for i in range(cols * rows):
            c, r = i % cols, i // cols
            local = t - (c + r) * 0.035
            gen = max(0, int(local / period))
            f.alpha_composite(tile(gen, i), (c * tw, r * th))
            since = local - gen * period
            if gen > 0 and since < 0.1:
                flash = Image.new("RGBA", (tw, th), (255, 255, 255, int(160 * (1 - since / 0.1))))
                f.alpha_composite(flash, (c * tw, r * th))
        a = ramp(t, 1.2, 0.3)
        if a > 0:
            paste(f, band, (0, 415), a)
            head.draw(f, W / 2, 440, "mt", a, 20)
            sub.draw(f, W / 2, 590, "mt", ramp(t, 1.5))
        return f
    return render


def scene_end(A: Assets):
    k = max(1, 600 // A.mask.height)
    tanks = {t: glow(scale(cp.recolor(A.mask, t, PAL), k), 22, 70) for t in CLASSIC}
    l1, l2 = title("CoW UNIT", 230), title("SPRITE SUITE", 172, YELLOW)
    l3 = body("Free on itch.io", 78)
    l4 = body("reactorcore.itch.io", 62, SKY)
    l5 = body("Made by Reactorcore", 44, GREY)
    credit = body("Commander Wars colortables and sample sprites: LGPL-3.0, Robosturm & "
                  "contributors. Not affiliated with Nintendo.", 26, GREY)

    def render(t: float) -> Image.Image:
        x = int(t * 50)
        f = A.backdrop_end.crop((x, 0, x + W, H))
        tank = tanks[CLASSIC[int(t / (2 * BEAT)) % len(CLASSIC)]]
        f.alpha_composite(tank, (1440 - tank.width // 2, 470 - tank.height // 2))
        l1.draw(f, 90, 90, "lt", ramp(t, 0.1), 30)
        l2.draw(f, 96, 330, "lt", ramp(t, 0.3), 30)
        l3.draw(f, 100, 580, "lt", ramp(t, 0.7), 20)
        l4.draw(f, 100, 690, "lt", ramp(t, 0.9), 20)
        l5.draw(f, 100, 790, "lt", ramp(t, 1.1), 20)
        credit.draw(f, W / 2, H - 70, "mt", ramp(t, 1.3))
        return f
    return render


# -------------------------------------------------------------- transitions

_DIS = np.random.default_rng(7).permutation(48 * 27).reshape(27, 48)
_SWEEP_S = None


def dissolve(old: Image.Image, new: Image.Image, p: float) -> Image.Image:
    """Pixel dissolve in 40 px blocks."""
    m = _DIS < p * _DIS.size
    m = np.repeat(np.repeat(m, 40, 0), 40, 1)
    a, b = np.array(old.convert("RGB")), np.array(new.convert("RGB"))
    return Image.fromarray(np.where(m[..., None], b, a), "RGB").convert("RGBA")


def sweep_maker(colors: list[tuple[int, int, int]]):
    """Army-color sweep: a slanted, stair-stepped band of team-color stripes
    crosses the frame left to right, uncovering the new scene behind it."""
    blk = 40
    xs = (np.arange(W) // blk * blk)[None, :].astype(float)
    ys = (np.arange(H) // blk * blk)[:, None].astype(float)
    s = xs + 0.45 * ys
    band, stripe = 640.0, 80.0
    pal = np.array(colors, np.uint8)
    smax = s.max()

    def sweep(old: Image.Image, new: Image.Image, p: float) -> Image.Image:
        front = lerp(-10, smax + band + 10, ease(p))
        a, b = np.array(old.convert("RGB")), np.array(new.convert("RGB"))
        out = np.where((s < front - band)[..., None], b, a)
        inb = (s >= front - band) & (s < front)
        idx = ((front - s) // stripe).astype(int) % len(pal)
        out[inb] = pal[idx[inb]]
        return Image.fromarray(out, "RGB").convert("RGBA")
    return sweep


def fade_black(old: Image.Image, new: Image.Image, p: float) -> Image.Image:
    src = old if p < 0.5 else new
    k = abs(1 - 2 * p)
    return Image.blend(Image.new("RGBA", src.size, (0, 0, 0, 255)), src, k)


# ----------------------------------------------------------------- timeline

@dataclasses.dataclass
class Scene:
    name: str
    dur: float
    render: Callable
    tr: str = "cut"            # transition in: cut / dissolve / sweep / fade
    tr_dur: float = 0.0
    march: bool = False
    chapter: str = ""          # starts a YouTube chapter
    wants_dur: bool = False    # render(t, dur) instead of render(t)
    start: float = 0.0

    def frame(self, t: float) -> Image.Image:
        return self.render(t, self.dur) if self.wants_dur else self.render(t)


def build_scenes(A: Assets) -> list[Scene]:
    pv, fx = clip_frames("previewer"), clip_frames("fixer")
    # Lengths in beats. 0-72: the track's loud opening (hook, Route A, the
    # previewer); 72-132: its breakdown and build (the optional AI route);
    # 132: the big section hits as the payoff montage starts.
    B = beats
    sc = [
        Scene("hook", B(12), scene_hook(A), chapter="Unit sprites, made fun"),
        Scene("problem", B(12), scene_problem(A), "sweep", 0.55),
        Scene("draw_red", B(11), scene_draw_red(A), "dissolve", 0.4, True,
              chapter="Drawing by hand, and the previewer"),
        Scene("cutter", B(14), scene_cutter(A), "sweep", 0.5, True),
        Scene("armies", B(11), scene_armies(A), "dissolve", 0.4, True),
        Scene("previewer", B(12), scene_clip(
            pv, PROMO / "05_previewer.png", "PREVIEW ANY SPRITE, IN ANY ARMY",
            {1: "W / S   step through the armies", 8: "Q / E   step through the backgrounds",
             13: "A / D   step through the sprites"},
            start=0.5, step=0.27, z1=1.06, focus=(W / 2, 560)), "sweep", 0.5, wants_dur=True),
        Scene("ai_card", B(5), scene_ai_card(A), "sweep", 0.5, chapter="Optional: the AI route"),
        Scene("wildcards", B(15), scene_wildcards(A), "dissolve", 0.35),
        Scene("ai_11", B(7), scene_annotated("11_edit_mode_setup.jpg", 1.0, 0.7, (640, 450), 1.5),
              "dissolve", 0.3),
        Scene("ai_12", B(6), scene_annotated("12_edit_mode_result.jpg", 0.8, 0.6, (480, 560), 1.65),
              "dissolve", 0.3),
        Scene("ai_13", B(9), scene_sheet_wipe(A), "dissolve", 0.3),
        Scene("ai_10", B(7), scene_clip(
            fx, PROMO / "10_ai_fixer_gui.png", "AI SPRITE FIXER",
            {2: "W / S   step through the armies", 8: "SIZE   64 → 48 → 32 px wide",
             11: "back to native size"},
            start=0.2, step=0.24, z1=1.05, focus=(W / 2, 500), head_y=840, ai=True),
            "dissolve", 0.3, wants_dur=True),
        Scene("ai_09", B(6), scene_one_unit(A), "dissolve", 0.3),
        Scene("ai_16", B(5), scene_annotated("16_even_out.jpg", 0.7, 0.6, (1060, 600), 1.7),
              "dissolve", 0.3),
        Scene("payoff", B(22), scene_payoff(A), "sweep", 0.6, chapter="Every army, and where to get it"),
        Scene("end", B(17), scene_end(A), "dissolve", 0.5),
    ]
    return sc


def lay_out(scenes: list[Scene]) -> float:
    """Set each scene's start; returns the total length."""
    t = 0.0
    for sc in scenes:
        sc.start = t
        t += sc.dur
    return t


class Renderer:
    def __init__(self, A: Assets, scenes: list[Scene], total: float):
        self.scenes, self.total = scenes, total
        self.marcher = march_overlay(A)
        self.march_t0 = next(s.start for s in scenes if s.march)
        self.sweep = sweep_maker([A.team_color[t] for t in
                                  ["orange_star", "blue_moon", "green_earth", "yellow_comet",
                                   "black_hole", "pink_frontier", "teal_isle", "purple"]])
        self._last: dict[int, Image.Image] = {}

    def _scene_frame(self, i: int, lt: float) -> Image.Image:
        s = self.scenes[i]
        f = s.frame(lt)
        if s.march:
            self.marcher(f, s.start + lt - self.march_t0)
        return f

    def _last_frame(self, i: int) -> Image.Image:
        if i not in self._last:
            self._last = {i: self._scene_frame(i, self.scenes[i].dur - 1 / FPS)}
        return self._last[i]

    def frame(self, gt: float) -> Image.Image:
        i = max(j for j, s in enumerate(self.scenes) if s.start <= gt + 1e-9)
        s = self.scenes[i]
        lt = gt - s.start
        f = self._scene_frame(i, lt)
        if i > 0 and s.tr != "cut" and lt < s.tr_dur:
            old = self._last_frame(i - 1)
            p = lt / s.tr_dur
            f = {"dissolve": dissolve, "sweep": self.sweep, "fade": fade_black}[s.tr](old, f, p)
        tail = self.total - gt
        if tail < 0.8:
            f = Image.blend(Image.new("RGBA", f.size, (0, 0, 0, 255)), f.convert("RGBA"),
                            clamp01(tail / 0.8))
        return f


# -------------------------------------------------------------------- output

def thumbnail(A: Assets) -> Image.Image:
    TW, TH = 1280, 720
    img = bp.hero_backdrop((TW, TH), A.mask, "night")
    k = max(1, 540 // A.mask.height)
    tank = glow(scale(cp.recolor(A.mask, "orange_star", PAL), k), 18, 60)
    img.alpha_composite(tank, (TW - tank.width + 10, (TH - tank.height) // 2 + 20))
    title("CoW UNIT", 150).draw(img, 46, 40, "lt")
    title("SPRITE SUITE", 112, YELLOW).draw(img, 50, 210, "lt")
    title("UNIT SPRITES,", 84).draw(img, 52, 380, "lt")
    title("MADE FUN.", 96, SKY).draw(img, 52, 474, "lt")
    body("Free  ·  Commander Wars modding", 38).draw(img, 56, 620, "lt")
    return img


def youtube_md(scenes: list[Scene], total: float) -> str:
    promo = (ROOT / "promo.md").read_text(encoding="utf-8")
    yt = promo.split("## YouTube", 1)[1].split("\n---", 1)[0]
    ttl = yt.split("**Title:**", 1)[1].split("**Description:**", 1)[0].strip()
    desc = yt.split("```", 2)[1].strip("\n")
    tags = yt.split("**Tags:**", 1)[1].strip()
    chapters = [f"{int(s.start // 60)}:{int(round(s.start)) % 60:02d} {s.chapter}"
                for s in scenes if s.chapter]
    first, rest = desc.split("\n\n", 1)
    honest = ("The AI route is optional and works at battle-animation scale only: the AI "
              "Sprite Fixer turns a generator's sheet into CoW sprites as a starting point "
              "for hand-fixing, not finished art.")
    full = "\n\n".join([first, rest.split("\n\nDownload:", 1)[0], honest,
                        "Chapters:\n" + "\n".join(chapters),
                        "Download:" + rest.split("\n\nDownload:", 1)[1]])
    credit = ("Commander Wars colortables and sample sprites: LGPL-3.0, Robosturm & "
              "contributors (https://github.com/Robosturm/Commander_Wars). "
              "Not affiliated with Nintendo or the Commander Wars project.")
    full += "\n\n" + credit
    return (f"# YouTube upload\n\nGenerated by tools/build_trailer.py; chapter times follow "
            f"the trailer's timeline ({total:.1f} s).\n\n**Title:**\n\n{ttl}\n\n"
            f"**Description:**\n\n```\n{full}\n```\n\n**Tags:**\n\n{tags}\n\n"
            f"**Thumbnail:** thumbnail_1280x720.png\n")


def encode(R: Renderer, total: float, out: pathlib.Path, preview: bool,
           music: pathlib.Path | None) -> None:
    step = 2 if preview else 1
    fps = FPS // step
    n = int(round(total * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if music:
        cmd += ["-i", str(music)]
        af = ["-af", f"volume=-1.5dB,afade=t=out:st={max(0, total - 3):.2f}:d=3"]  # headroom for AAC
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        af = []
    vf = ["-vf", "scale=960:540:flags=neighbor"] if preview else []
    cmd += vf + af + ["-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-tune", "animation",
                      "-preset", "veryfast" if preview else "slow",
                      "-crf", "24" if preview else "16",
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                      "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for fi in range(0, n, step):
        proc.stdin.write(R.frame(fi / FPS).convert("RGB").tobytes())
        if fi % (FPS * 5) < step:
            print(f"  {fi / FPS:5.1f}s / {total:.1f}s   ({time.time() - t0:.0f}s elapsed)")
    proc.stdin.close()
    if proc.wait():
        raise SystemExit("ffmpeg failed")
    print(f"wrote {out}  ({out.stat().st_size / 1e6:.1f} MB)")


def stills(R: Renderer, scenes: list[Scene]) -> None:
    folder = OUT / "keyframes"
    folder.mkdir(parents=True, exist_ok=True)
    picks = []
    for s in scenes:
        for frac in ((0.85,) if s.dur < 4 else (0.45, 0.9)):
            picks.append((s.name, s.start + s.dur * frac))
    thumbs = []
    for name, t in picks:
        f = R.frame(t).convert("RGB")
        f.save(folder / f"{t:05.1f}s_{name}.png")
        thumbs.append((f"{t:4.1f}s {name}", f.resize((480, 270), Image.NEAREST)))
    cols = 4
    rows = -(-len(thumbs) // cols)
    sheet = Image.new("RGB", (cols * 484, rows * 300), (20, 20, 28))
    d = ImageDraw.Draw(sheet)
    for i, (lab, im) in enumerate(thumbs):
        x, y = (i % cols) * 484 + 2, (i // cols) * 300 + 2
        sheet.paste(im, (x, y))
        d.text((x + 4, y + 272), lab, font=bp.font(bp.FONT_BOLD, 18), fill=(230, 230, 240))
    sheet.save(folder / "contact_sheet.png")
    print(f"wrote {len(thumbs)} key frames + contact_sheet.png to {folder}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--preview", action="store_true", help="960x540 @ 15 fps, fast")
    ap.add_argument("--stills", action="store_true", help="key frames and a contact sheet only")
    ap.add_argument("--at", type=float, nargs="*", help="render single frames at these times")
    ap.add_argument("--music", type=pathlib.Path, default=MUSIC,
                    help="audio track to lay under the video (default: the Heavyweight track)")
    ap.add_argument("--silent", action="store_true", help="no music, a silent audio track")
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    A = Assets()
    scenes = build_scenes(A)
    total = lay_out(scenes)
    print("timeline: " + "  ".join(f"{s.name} {s.start:.1f}" for s in scenes) + f"  end {total:.1f}")
    R = Renderer(A, scenes, total)

    if args.at:
        for t in args.at:
            R.frame(t).convert("RGB").save(OUT / f"frame_{t:05.2f}.png")
        return 0
    if args.stills:
        stills(R, scenes)
        return 0
    thumbnail(A).convert("RGB").save(OUT / "thumbnail_1280x720.png")
    (OUT / "youtube.md").write_text(youtube_md(scenes, total), encoding="utf-8")
    name = "cow_unit_sprite_suite_trailer" + ("_preview" if args.preview else "")
    music = None if args.silent or not args.music.is_file() else args.music
    print(f"music: {music.name if music else 'none (silent track)'}")
    encode(R, total, OUT / f"{name}.mp4", args.preview, music)
    return 0


if __name__ == "__main__":
    sys.exit(main())
