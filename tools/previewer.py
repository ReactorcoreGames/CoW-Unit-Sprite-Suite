"""CoW sprite previewer / recolor exporter (Phase 2).

Loads a folder of index-encoded PNGs and shows them recolored into any of the
33 team palettes, over any of the 6 backgrounds, at integer NEAREST zoom.

    A / D    previous / next sprite
    W / S    previous / next team color
    Q / E    previous / next background
    R / F    zoom out / in        (also - / =)
    T        next frame           (also [ / ])
    Ctrl+O   open folder      Ctrl+E  export current     Ctrl+Shift+E  export all
    Ctrl+G   export collage   Esc     leave a text field (never quits)

Run:  python tools/previewer.py [folder]
"""

from __future__ import annotations

import math
import pathlib
import sys
import traceback

import numpy as np
import ttkbootstrap as tb
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, Y, W

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cowpalette as cp  # noqa: E402
import convert  # noqa: E402  -- Phase 5, shared with tools/batch_convert.py
import split  # noqa: E402  -- Phase 5.5, shared with its own CLI
import guikit as gk  # noqa: E402  -- icon, light/dark, Guide button, tooltips
from guikit import tip  # noqa: E402

from approot import ROOT  # noqa: E402  -- project root, or the exe folder when frozen

# Opens on launch: CoW's own map units, shipped as samples (same path from source).
DEFAULT_FOLDER = ROOT / "samples" / "cow map units"
OUTPUT = ROOT / "output"

def _scale_to_cell(sprite: Image.Image, cell: int, fill: bool = False) -> Image.Image:
    """Scale a sprite toward `cell`, NEAREST only -- never smoothed.

    Default (fill=False) prefers a whole-number factor (2x, 3x up; 1/2, 1/3
    down) so pixel edges stay aligned, and leaves a sprite alone when no integer
    factor fits. That keeps pixel art crisp, and is the right mode for battle
    animations, whose sizes are genuinely varied and have no common factor.

    With fill=True the sprite is scaled to reach `cell` on its long edge even
    when the factor is fractional. Map units are 16/20/24/32 px: 16 doubles
    cleanly to 32, but 20 and 24 have no integer path there and otherwise sit
    looking undersized in a 32px grid. A fractional NEAREST scale gives them
    uneven pixel bands -- the trade for a uniform sheet.
    """
    w, h = sprite.size
    if w == 0 or h == 0 or (w == cell and h == cell):
        return sprite

    if w <= cell and h <= cell:
        k = min(cell // w, cell // h)
        if k >= 2 and not (fill and (w * k < cell and h * k < cell)):
            return sprite.resize((w * k, h * k), Image.NEAREST)
        if k == 1 and not fill:
            return sprite
    else:
        for d in range(2, 17):
            if w % d == 0 and h % d == 0 and w // d <= cell and h // d <= cell:
                return sprite.resize((w // d, h // d), Image.NEAREST)

    k = min(cell / w, cell / h)
    return sprite.resize((max(1, round(w * k)), max(1, round(h * k))), Image.NEAREST)


ZOOMS = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32)
DEFAULT_ZOOM_INDEX = ZOOMS.index(8)

# pydata, dark by default: sprites read best against a dark surround. The
# wildcard generator uses gruvbox, light by default, so the two never look alike.
LIGHT_THEME, DARK_THEME = "pydata-light", "pydata-dark"

# The canvas sits a step off the window background, so its edge stays visible
# even behind a flat key background. Mixed per theme in _on_theme.
CANVAS_STEP = 0.07

# "Fit to cell" sizes. Sprites vary (16/20/24/32 px), which makes browsing jumpy;
# locking a cell size keeps every sprite the same on-screen size.
FIT_SIZES = (16, 32, 48, 64, 128, 256)
DEFAULT_FIT = 32

# Breathing room around the sprite, in sprite pixels, so a densely packed sprite
# does not sit flush against the gradient edge.
DEFAULT_MARGIN = 4


class Previewer(tb.Window):
    def __init__(self, folder: pathlib.Path | None = None):
        super().__init__(themename=DARK_THEME, light_theme=LIGHT_THEME,
                         dark_theme=DARK_THEME, title="CoW Sprite Previewer")
        self.geometry("1180x760")
        self.minsize(860, 560)

        self.palettes = cp.load_palettes()
        self.teams = self.palettes.team_names()
        self.backgrounds = list(cp.BACKGROUNDS)

        self.folder: pathlib.Path | None = None
        self.paths: list[pathlib.Path] = []
        self.sprite_i = 0
        self.team_i = 0
        self.bg_i = self.backgrounds.index("day")
        self.zoom_i = DEFAULT_ZOOM_INDEX
        self.frame_i = 0
        self._photo: ImageTk.PhotoImage | None = None
        self._source: Image.Image | None = None

        self.frame_count = tb.IntVar(value=1)
        self.status = tb.StringVar(value="No folder loaded.")
        self.fit_enabled = tb.BooleanVar(value=True)
        self.fit_size = tb.IntVar(value=DEFAULT_FIT)
        self.margin = tb.IntVar(value=DEFAULT_MARGIN)
        self.fill_cell = tb.BooleanVar(value=False)

        self._build()
        gk.setup(self, "icon_previewer", self._on_theme)
        self._bind_keys()

        start = folder or (DEFAULT_FOLDER if DEFAULT_FOLDER.is_dir() else None)
        if start:
            self.load_folder(start)

    # ------------------------------------------------------------------ ui

    def _build(self) -> None:
        top = tb.Frame(self, padding=(10, 8))
        top.pack(side="top", fill=X)

        def button(text, icon, style, cmd, help_, pad=6):
            b = tb.Button(top, text=text, icon=icon, bootstyle=style, command=cmd)
            b.pack(side=LEFT, padx=(pad, 0))
            return tip(b, help_)

        button("Open folder", "folder2-open", "primary", self.pick_folder,
               "Browse a folder of finished CoW sprites: the '+mask.png' files in "
               "the game's 'ugly' mask palette (washed-out greens and browns), which "
               "the previewer shows in real team colors. Your own red drawings are "
               "not mask sprites yet -- bring them in with Convert folder or Cut "
               "sheet.  Ctrl+O", pad=0)
        button("Export current", "download", "success-outline", self.export_current,
               "Save the sprite on screen, recolored into the current team, to the "
               "output folder as  name+team.png.  Ctrl+E", pad=10)
        button("Export all (this team)", "files", "success", self.export_all,
               "Recolor EVERY sprite in this folder into the current team and save "
               "them all to the output folder.  Ctrl+Shift+E")
        button("Export collage", "grid-3x3", "info", self.export_collage,
               "Save one picture showing every sprite in this folder side by side, "
               "in the current team and background. Tile size is the 'Fit to cell' "
               "size below.  Ctrl+G")
        button("Convert folder...", "magic", "secondary-outline", self.convert_folder,
               "Turn your own drawings into CoW sprites. Pick a folder of PNGs drawn "
               "in Orange Star red on flat #00ff00 green, one unit per file. Each is "
               "converted to the 'ugly' mask palette CoW reads (a +mask.png). Results "
               "go to output/converted/ and open here.", pad=10)
        button("Cut sheet...", "scissors", "secondary-outline", self.cut_sheet,
               "Drew many units on one image, in Orange Star red on green? Pick it "
               "and each unit is cut out and converted to a +mask.png separately. "
               "Results go to output/cut/<sheet name>/ and open here.")

        gk.theme_button(top, self).pack(side=RIGHT)
        gk.guide_button(top, self.status).pack(side=RIGHT, padx=(0, 6))

        opts = tb.Frame(self, padding=(10, 2))
        opts.pack(side="top", fill=X)

        tip(tb.Label(opts, text="Split into"),
            "Animated sprites are a strip of frames side by side. Tell the "
            "previewer how many frames the strip holds and it shows one at a time "
            "(T steps through them).").pack(side=LEFT, padx=(0, 4))
        self.spin = tb.Spinbox(opts, from_=1, to=64, width=4, textvariable=self.frame_count,
                               command=self._on_frame_count)
        self.spin.pack(side=LEFT)
        tip(self.spin, "Frames in this sprite strip. Type a number and press Enter, "
                       "or use the arrows. 1 = a still sprite.")
        # Enter or leaving the field commits; Esc hands focus back to the window
        # so the single-key shortcuts work again.
        self.spin.bind("<Return>", lambda e: self._commit_field())
        self.spin.bind("<KP_Enter>", lambda e: self._commit_field())
        self.spin.bind("<FocusOut>", lambda e: self._on_frame_count())
        self.spin.bind("<Escape>", lambda e: self._commit_field())
        tb.Label(opts, text="frames").pack(side=LEFT, padx=(4, 0))

        # Never guessed -- battleanim frame counts are not derivable from size.
        tip(tb.Button(opts, text="Guess from width", bootstyle="neutral-outline",
                      command=self._guess_frames),
            "Sets the frame count to width / height, which is right for CoW map "
            "units. Battle animations cannot be guessed this way -- count their "
            "frames yourself.").pack(side=LEFT, padx=(8, 0))

        fit = tb.Checkbutton(opts, text="Fit to cell", variable=self.fit_enabled,
                             bootstyle="round-toggle", command=self.render)
        fit.pack(side=LEFT, padx=(24, 0))
        tip(fit, "Center every sprite in a square of the same size, so flipping "
                 "between small and large sprites does not make the view jump.")
        cell = tb.Combobox(opts, width=5, state="readonly",
                           values=[str(v) for v in FIT_SIZES], textvariable=self.fit_size)
        cell.pack(side=LEFT, padx=(8, 0))
        cell.bind("<<ComboboxSelected>>", lambda e: (self.focus_set(), self.render()))
        tip(cell, "Size of that square, in sprite pixels. CoW map units are 16-32. "
                  "Also the tile size of Export collage.")
        tb.Label(opts, text="px cell").pack(side=LEFT, padx=(4, 0))

        tip(tb.Label(opts, text="BG margin"),
            "Empty background left around the sprite, in sprite pixels."
            ).pack(side=LEFT, padx=(24, 4))
        msp = tb.Spinbox(opts, from_=0, to=64, width=4, textvariable=self.margin,
                         command=self.render)
        msp.pack(side=LEFT)
        msp.bind("<Return>", lambda e: self._commit_field())
        msp.bind("<FocusOut>", lambda e: self.render())
        msp.bind("<Escape>", lambda e: self._commit_field())
        tip(msp, "Empty background left around the sprite, in sprite pixels. "
                 "Applies to the view and to Export collage.")
        tb.Label(opts, text="px").pack(side=LEFT, padx=(4, 0))

        # Off by default: battleanim sprites have no common factor and are best
        # left at a clean integer scale.
        tip(tb.Checkbutton(opts, text="Collage: fill cell (allow fractional scale)",
                           variable=self.fill_cell, bootstyle="round-toggle"),
            "Export collage only. On: small sprites are stretched to fill their "
            "tile, even if that makes some pixel rows thicker than others. Off: "
            "sprites only grow by whole steps (2x, 3x...), so every pixel stays "
            "crisp.").pack(side=LEFT, padx=(24, 0))

        bar = tb.Frame(self, padding=(10, 6))
        bar.pack(side="bottom", fill=X)
        tb.Label(bar, textvariable=self.status, style=gk.MUTED).pack(side=LEFT, anchor=W)
        gk.credit_label(bar).pack(side=RIGHT, padx=(16, 0))
        tip(tb.Label(bar, text="A/D sprite   W/S team   Q/E background   R/F zoom   T frame",
                     style=gk.MUTED),
            "Keyboard shortcuts. The arrow keys also work: left/right for sprite, "
            "up/down for team. Esc leaves a text box so the shortcuts work again."
            ).pack(side=RIGHT)

        # Readout, one stepper per thing the keyboard steps, so everything the
        # shortcuts do is also a click away.
        read = tb.Frame(self, padding=(10, 2, 10, 0))
        read.pack(side="bottom", fill=X)

        def stepper(back, fwd, help_, minus="chevron-left", plus="chevron-right",
                    side=LEFT, pad=0, **label):
            parts = [
                tip(tb.Button(read, icon=minus, icon_only=True, bootstyle="secondary-ghost",
                              takefocus=False, command=back), help_),
                tip(tb.Label(read, text="-", **label), help_),
                tip(tb.Button(read, icon=plus, icon_only=True, bootstyle="secondary-ghost",
                              takefocus=False, command=fwd), help_),
            ]
            # Packing from the right reverses the order; keep minus on the left.
            for i, w in enumerate(parts if side == LEFT else parts[::-1]):
                w.pack(side=side, padx=4 if i == 1 else ((pad, 0) if i == 0 else 0))
            return parts[1]

        self.lbl_sprite = stepper(
            lambda: self.step_sprite(-1), lambda: self.step_sprite(1),
            "Previous / next sprite in the folder.  A / D or Left / Right.",
            font=("", 11, "bold"))
        self.lbl_team = stepper(
            lambda: self.step_team(-1), lambda: self.step_team(1),
            "Team color the sprite is shown in. CoW has 32 armies; 'mask' shows "
            "the file as it really is, in the 'ugly' mask palette.  W / S or "
            "Up / Down.", pad=18, bootstyle="primary")
        self.lbl_bg = stepper(
            lambda: self.step_bg(-1), lambda: self.step_bg(1),
            "Backdrop behind the sprite, for checking it reads on different "
            "terrain and times of day. Never part of an export.  Q / E.", pad=18)
        self.lbl_zoom = tb.Label(read, text="-", style=gk.MUTED)
        self.lbl_zoom.pack(side=RIGHT, padx=(10, 0))
        tip(self.lbl_zoom, "The sprite's real size in pixels, and the fit cell size.")
        self.lbl_zoom_n = stepper(
            lambda: self.step_zoom(-1), lambda: self.step_zoom(1),
            "Zoom out / in. Always whole steps, so pixels stay sharp.  R / F.",
            minus="zoom-out", plus="zoom-in", side=RIGHT)

        self.canvas = tb.Canvas(self, highlightthickness=0)
        self.canvas.pack(side="top", fill=BOTH, expand=True, padx=10, pady=(6, 4))
        self.canvas.bind("<Configure>", lambda e: self.render())

    def _on_theme(self, colors) -> None:
        self.canvas.configure(background=gk.mix(colors.bg, colors.fg, CANVAS_STEP))

    def _typing(self) -> bool:
        """True when focus sits in a text-entry widget, so letters mean letters."""
        try:
            w = self.focus_get()
        except KeyError:
            return False
        return isinstance(w, (tb.Spinbox, tb.Entry, tb.Combobox))

    def _nav(self, fn):
        """Wrap a shortcut so it is ignored while typing in a field."""
        def handler(event=None):
            if self._typing():
                return None
            fn()
            return "break"
        return handler

    def _bind_keys(self) -> None:
        for key, fn in (
            ("a", lambda: self.step_sprite(-1)), ("d", lambda: self.step_sprite(1)),
            ("w", lambda: self.step_team(-1)),   ("s", lambda: self.step_team(1)),
            ("q", lambda: self.step_bg(-1)),     ("e", lambda: self.step_bg(1)),
            ("r", lambda: self.step_zoom(-1)),   ("f", lambda: self.step_zoom(1)),
            ("t", lambda: self.step_frame(1)),
            ("bracketleft", lambda: self.step_frame(-1)),
            ("bracketright", lambda: self.step_frame(1)),
            ("minus", lambda: self.step_zoom(-1)),
            ("equal", lambda: self.step_zoom(1)),
            ("plus", lambda: self.step_zoom(1)),
        ):
            h = self._nav(fn)
            self.bind(f"<KeyPress-{key}>", h)
            if len(key) == 1:
                self.bind(f"<KeyPress-{key.upper()}>", h)
        self.bind("<Left>", self._nav(lambda: self.step_sprite(-1)))
        self.bind("<Right>", self._nav(lambda: self.step_sprite(1)))
        self.bind("<Up>", self._nav(lambda: self.step_team(-1)))
        self.bind("<Down>", self._nav(lambda: self.step_team(1)))
        self.bind("<Control-o>", lambda e: self.pick_folder())
        self.bind("<Control-e>", lambda e: self.export_current())
        self.bind("<Control-E>", lambda e: self.export_all())
        self.bind("<Control-Shift-E>", lambda e: self.export_all())
        self.bind("<Control-g>", lambda e: self.export_collage())
        # Esc leaves a text field. It deliberately does NOT quit -- pressing it
        # by reflex used to kill the program.
        self.bind("<Escape>", lambda e: self.focus_set())
        self.canvas.bind("<Button-1>", lambda e: self.focus_set())

    # -------------------------------------------------------------- loading

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(
            title="Folder of CoW mask sprites (+mask.png)",
            initialdir=str(self.folder or DEFAULT_FOLDER.parent),
        )
        if d:
            self.load_folder(pathlib.Path(d))

    def load_folder(self, folder: pathlib.Path) -> None:
        folder = pathlib.Path(folder)
        paths = sorted(p for p in folder.glob("*.png") if p.is_file())
        if not paths:
            messagebox.showwarning("No sprites", f"No PNG files in\n{folder}")
            return
        self.folder = folder
        self.paths = paths
        self.sprite_i = 0
        self.frame_i = 0
        self.title(f"CoW Sprite Previewer - {folder.name}")
        self._load_sprite()

    def _load_sprite(self) -> None:
        try:
            self._source = Image.open(self.paths[self.sprite_i]).convert("RGBA")
        except Exception as exc:
            self._source = None
            self.status.set(f"Could not open {self.paths[self.sprite_i].name}: {exc}")
        self.frame_i = 0
        self.render()

    # ------------------------------------------------------------ navigation

    def _step(self, attr: str, delta: int, n: int) -> None:
        if n <= 0:
            return
        setattr(self, attr, (getattr(self, attr) + delta) % n)

    def step_sprite(self, d: int) -> None:
        if not self.paths:
            return
        self._step("sprite_i", d, len(self.paths))
        self._load_sprite()

    def step_team(self, d: int) -> None:
        self._step("team_i", d, len(self.teams))
        self.render()

    def step_bg(self, d: int) -> None:
        self._step("bg_i", d, len(self.backgrounds))
        self.render()

    def step_frame(self, d: int) -> None:
        self._step("frame_i", d, max(1, self.frame_count.get()))
        self.render()

    def step_zoom(self, d: int) -> None:
        self.zoom_i = max(0, min(len(ZOOMS) - 1, self.zoom_i + d))
        self.render()

    def _on_frame_count(self) -> None:
        self.frame_i = 0
        self.render()

    def _commit_field(self) -> None:
        """Accept a typed value and hand focus back to the window."""
        self._on_frame_count()
        self.focus_set()

    def _guess_frames(self) -> None:
        """Opt-in only. Map units are width/height frames; battleanim is not.

        The docs are explicit that a battleanim frame count cannot be derived
        from dimensions, so this is a button the user presses, never a default.
        """
        if self._source is None:
            return
        w, h = self._source.size
        n = w // h if h and w % h == 0 and w >= h else 1
        self.frame_count.set(max(1, n))
        self._on_frame_count()
        self.status.set(f"Guessed {max(1, n)} frames from {w}x{h} (width / height).")

    # -------------------------------------------------------------- render

    @property
    def team(self) -> str:
        return self.teams[self.team_i]

    @property
    def background(self) -> str:
        return self.backgrounds[self.bg_i]

    @property
    def zoom(self) -> int:
        return ZOOMS[self.zoom_i]

    def current_frame(self, image: Image.Image) -> Image.Image:
        n = max(1, self.frame_count.get())
        try:
            frames = cp.split_frames(image, n)
        except ValueError:
            return image
        return frames[min(self.frame_i, len(frames) - 1)]

    def rendered(self, image: Image.Image) -> Image.Image:
        """Recolored current frame, transparent background -- what export writes."""
        return cp.recolor(self.current_frame(image), self.team, self.palettes)

    def _cell_size(self) -> int:
        try:
            return max(1, int(self.fit_size.get()))
        except Exception:
            return DEFAULT_FIT

    def _margin(self) -> int:
        try:
            return max(0, int(self.margin.get()))
        except Exception:
            return DEFAULT_MARGIN

    def staged(self, sprite: Image.Image) -> Image.Image:
        """Place the sprite on its backdrop, honouring fit-to-cell and margin.

        With fit on, every sprite is centred in a cell of the chosen size, so
        browsing a folder of mixed 16/20/24/32px units stops jumping around.
        Sprites larger than the cell are never scaled -- the cell grows instead,
        because a non-integer downscale would wreck the pixel art.
        """
        m = self._margin()
        if self.fit_enabled.get():
            cell = self._cell_size()
            w = max(cell, sprite.width) + 2 * m
            h = max(cell, sprite.height) + 2 * m
        else:
            w, h = sprite.width + 2 * m, sprite.height + 2 * m

        bg = cp.make_background(self.background, (w, h))
        bg.alpha_composite(sprite, ((w - sprite.width) // 2, (h - sprite.height) // 2))
        return bg

    def render(self) -> None:
        self.canvas.delete("all")
        if self._source is None:
            self._set_readout(None)
            return

        sprite = self.rendered(self._source)
        z = self.zoom
        shown = self.staged(sprite)
        shown = shown.resize((shown.width * z, shown.height * z), Image.NEAREST)

        self._photo = ImageTk.PhotoImage(shown)
        cw = self.canvas.winfo_width() or 1
        ch = self.canvas.winfo_height() or 1
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)
        self._set_readout(sprite)

    def _undefined_count(self) -> int:
        if self._source is None:
            return 0
        return int(cp.undefined_mask(
            self.current_frame(self._source), self.team, self.palettes).sum())

    def _set_readout(self, sprite: Image.Image | None) -> None:
        if sprite is None or not self.paths:
            self.lbl_sprite.config(text="-")
            self.lbl_team.config(text="-")
            self.lbl_bg.config(text="-")
            self.lbl_zoom.config(text="-")
            self.lbl_zoom_n.config(text="-")
            return
        name = self.paths[self.sprite_i].name
        n = max(1, self.frame_count.get())
        frame = f"   frame {self.frame_i + 1}/{n}" if n > 1 else ""
        self.lbl_sprite.config(
            text=f"{name}   [{self.sprite_i + 1}/{len(self.paths)}]{frame}")
        self.lbl_team.config(
            text=f"team: {self.team}  [{self.team_i + 1}/{len(self.teams)}]")
        self.lbl_bg.config(
            text=f"bg: {self.background}  [{self.bg_i + 1}/{len(self.backgrounds)}]")
        fit = f"   cell {self._cell_size()}" if self.fit_enabled.get() else ""
        self.lbl_zoom_n.config(text=f"zoom {self.zoom}x")
        self.lbl_zoom.config(text=f"{sprite.width}x{sprite.height} px{fit}")

        # A correctly encoded sprite addresses only defined table cells. Any
        # undefined pixel renders as a hole, so surface it rather than hide it.
        bad = self._undefined_count()
        opaque = int((np.asarray(self.current_frame(self._source))[..., 3] > 0).sum())
        if bad and bad >= opaque:
            # Every pixel is off the table: an ordinary picture, most likely the
            # user's own red drawing opened before converting it.
            self.status.set(
                f"{name} is not a CoW mask sprite, so it can't be shown in team "
                f"colors. If it is your own red drawing, convert it first "
                f"(Convert folder / Cut sheet).")
        elif bad:
            self.status.set(
                f"WARNING: {bad} px of this sprite have no color in '{self.team}' "
                f"and show as holes - the file may be damaged or hand-edited.")
        elif self.folder:
            self.status.set(f"{len(self.paths)} sprites in {self.folder.name}")

    # -------------------------------------------------------------- export

    def export_current(self) -> None:
        if self._source is None:
            return
        OUTPUT.mkdir(parents=True, exist_ok=True)
        name = cp.export_name(self.paths[self.sprite_i].name, self.team)
        dest = OUTPUT / name
        try:
            self.rendered(self._source).save(dest)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status.set(f"Exported {name} -> output/")

    def export_all(self) -> None:
        """Every sprite in the folder, recolored into the current team.

        Exports the whole image, not the current frame -- a strip stays a strip.
        """
        if not self.paths:
            return
        OUTPUT.mkdir(parents=True, exist_ok=True)
        ok = 0
        errors: list[str] = []
        for p in self.paths:
            try:
                im = Image.open(p).convert("RGBA")
                cp.recolor(im, self.team, self.palettes).save(
                    OUTPUT / cp.export_name(p.name, self.team))
                ok += 1
            except Exception as exc:
                errors.append(f"{p.name}: {exc}")
        msg = f"Exported {ok}/{len(self.paths)} sprites as +{self.team}.png -> output/"
        if errors:
            msg += f"   ({len(errors)} failed)"
            messagebox.showwarning("Some exports failed", "\n".join(errors[:10]))
        self.status.set(msg)


    def convert_folder(self) -> None:
        """Run the Phase 5 pipeline over a folder of raw generator output.

        Calls `convert.convert_image` -- the same function `batch_convert.py`
        uses -- so the GUI and the command line cannot drift apart. Results land
        in `output/converted/`, and anything the grid detector was unsure of
        goes to `output/converted/needs-review/` instead of being guessed at.

        The unit class is asked for, never inferred: it is metadata the user
        chose when they generated the art, and the image is not evidence of it.
        """
        folder = filedialog.askdirectory(title="Folder of drawings to convert (red on green)")
        if not folder:
            return
        src = pathlib.Path(folder)

        asked = self._ask_unit_class()
        if asked is None:
            return
        choice, key = asked

        files = sorted(p for p in src.iterdir()
                       if p.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".bmp"})
        if not files:
            messagebox.showinfo("Nothing to convert", f"No images in {src}")
            return

        dest = OUTPUT / "converted"
        dest.mkdir(parents=True, exist_ok=True)
        review = dest / "needs-review"

        try:
            pal = convert._resolve_class(choice)
        except KeyError as exc:
            messagebox.showerror("Unknown class", str(exc))
            return

        done = flagged = failed = 0
        problems: list[str] = []
        for path in files:
            try:
                res = convert.convert_file(path, pal, key=key)
            except Exception as exc:
                failed += 1
                problems.append(f"{path.name}: {exc}")
                continue
            if res.needs_review:
                flagged += 1
                review.mkdir(parents=True, exist_ok=True)
                if res.snapped is not None:
                    res.snapped.save(review / f"{res.name}.snapped.png")
                (review / f"{res.name}.txt").write_text(
                    res.reason + "\n", encoding="utf-8")
                continue
            res.encoded.save(dest / f"{res.name}+mask.png")
            done += 1

        msg = (f"Converted {done}/{len(files)} as {choice} "
               f"-> output/converted/")
        if flagged:
            msg += f"   ({flagged} need review)"
        if problems:
            messagebox.showwarning("Some conversions failed",
                                   "\n".join(problems[:10]))
        self.status.set(msg)

        # Show the results straight away -- the point of having this in the GUI
        # is seeing the output in every team without a second round trip.
        if done:
            self.load_folder(dest)

    def cut_sheet(self) -> None:
        """Cut a sheet of many units apart and convert each one (Phase 5.5).

        Calls `split.split_sheet` and `split.write_outputs` -- the same functions
        `tools/split.py` uses. Results land in `output/cut/<sheet>/`, with
        `rgb/<sheet>.index.png` showing every piece numbered. A `<sheet>.names.txt`
        beside the sheet, one name per line in that order, names the sprites.
        """
        path = filedialog.askopenfilename(
            title="Sheet of units to cut apart",
            filetypes=[("Images", "*.png *.webp *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if not path:
            return
        sheet = pathlib.Path(path)

        asked = self._ask_unit_class()
        if asked is None:
            return
        choice, key = asked

        names_file = split.names_file_for(sheet)
        names = split.read_names(names_file) if names_file else None
        try:
            with Image.open(sheet) as im:
                image = im.copy()
            res = split.split_sheet(image, choice, name=sheet.stem, names=names,
                                     key=key)
        except Exception as exc:
            messagebox.showerror("Could not cut sheet", f"{exc}")
            return
        if not res.pieces:
            messagebox.showinfo("Nothing found", f"No sprites found on {sheet.name}")
            return

        dest = OUTPUT / "cut" / sheet.stem
        done, flagged = split.write_outputs(res, image, dest)
        msg = f"Cut {len(res.pieces)} sprites, {done} converted -> output/cut/{sheet.stem}/"
        if flagged:
            msg += f"   ({flagged} need review)"
        if names and len(names) != len(res.pieces):
            messagebox.showwarning(
                "Name count mismatch",
                f"{len(names)} names for {len(res.pieces)} sprites -- check "
                f"rgb/{sheet.stem}.index.png in the output folder.")
        self.status.set(msg)
        if done:
            self.load_folder(dest)

    def _ask_unit_class(self) -> tuple[str, str] | None:
        """Modal chooser for the background color and target unit class.

        Returns (class, key), or None if cancelled."""
        from tkinter import StringVar, Toplevel

        names = convert.class_names()
        win = Toplevel(self)
        win.title("Convert settings")
        win.transient(self)
        win.resizable(False, False)
        chosen: dict[str, str | None] = {"value": None}
        var = StringVar(value=names[0])
        key_var = StringVar(value="green")

        frame = tb.Frame(win, padding=14)
        frame.pack(fill=BOTH, expand=True)
        tb.Label(frame, text="Background color to remove:").pack(anchor=W)
        keys = tb.Frame(frame)
        keys.pack(anchor=W, pady=(2, 12))
        for k, label in (("green", "Green #00ff00 (hand-drawn art)"),
                         ("magenta", "Magenta #ff00ff")):
            tb.Radiobutton(keys, text=label, value=k,
                           variable=key_var).pack(side=LEFT, padx=(0, 14))
        tb.Label(frame, text="Snap colors to which palette?").pack(anchor=W)
        tb.Label(frame,
                 text="'all' suits almost everything: class is not a color limit.",
                 style=gk.MUTED).pack(anchor=W, pady=(0, 8))
        for n in names:
            pal = convert.load_class_palettes()[n]
            extra = "  -- every class combined" if n == convert.ALL_CLASSES else ""
            tb.Radiobutton(frame, text=f"{n}  ({len(pal)} colors){extra}",
                           value=n, variable=var).pack(anchor=W, pady=1)

        row = tb.Frame(frame)
        row.pack(fill=X, pady=(12, 0))

        def ok():
            chosen["value"] = (var.get(), key_var.get())
            win.destroy()

        tb.Button(row, text="Convert", bootstyle="primary",
                  command=ok).pack(side=RIGHT)
        tb.Button(row, text="Cancel", bootstyle="secondary-outline",
                  command=win.destroy).pack(side=RIGHT, padx=(0, 6))

        win.grab_set()
        self.wait_window(win)
        return chosen["value"]

    def export_collage(self) -> None:
        """One sheet: first frame of every sprite, in the current team.

        Every tile is the chosen cell size, so the grid is uniform. Sprites are
        scaled to fill the cell in BOTH directions -- small ones scale up, large
        ones down -- which is what CoW itself does, since it mixes GBA-era (AW2)
        and DS-era (AWDS) art at different native sizes into the same slots.

        Scaling is integer-factor NEAREST whenever a factor fits, so pixel art
        stays crisp; only when no integer factor lands inside the cell does it
        fall back to a NEAREST fit (still unsmoothed).
        """
        if not self.paths:
            return
        OUTPUT.mkdir(parents=True, exist_ok=True)

        cell = self._cell_size()
        m = self._margin()
        tile = cell + 2 * m
        n = max(1, int(self.frame_count.get()))

        tiles: list[Image.Image] = []
        skipped: list[str] = []
        for p in self.paths:
            try:
                im = Image.open(p).convert("RGBA")
            except Exception as exc:
                skipped.append(f"{p.name}: {exc}")
                continue
            try:
                frame = cp.split_frames(im, n)[0]
            except ValueError:
                frame = im        # count does not divide this sprite; use it whole
            sprite = cp.recolor(frame, self.team, self.palettes)

            sprite = _scale_to_cell(sprite, cell, self.fill_cell.get())

            bg = cp.make_background(self.background, (tile, tile))
            bg.alpha_composite(sprite, ((tile - sprite.width) // 2,
                                        (tile - sprite.height) // 2))
            tiles.append(bg)

        if not tiles:
            messagebox.showwarning("Collage", "No sprites could be loaded.")
            return

        cols = math.ceil(math.sqrt(len(tiles)))
        rows = math.ceil(len(tiles) / cols)
        sheet = Image.new("RGBA", (cols * tile, rows * tile), (0, 0, 0, 0))
        for i, t in enumerate(tiles):
            sheet.paste(t, ((i % cols) * tile, (i // cols) * tile))

        dest = OUTPUT / f"collage+{self.team}+{cell}px.png"
        try:
            sheet.save(dest)
        except Exception as exc:
            messagebox.showerror("Collage failed", str(exc))
            return
        mode = "fill" if self.fill_cell.get() else "integer scale"
        msg = (f"Collage: {len(tiles)} sprites, {cols}x{rows} @ {tile}px "
               f"({mode}) -> {dest.name}")
        if skipped:
            msg += f"   ({len(skipped)} skipped)"
        self.status.set(msg)


def main(argv: list[str]) -> int:
    folder = pathlib.Path(argv[1]) if len(argv) > 1 else None
    if folder and not folder.is_dir():
        print(f"not a folder: {folder}")
        return 2
    try:
        Previewer(folder).mainloop()
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
